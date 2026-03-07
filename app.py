import re
import time
import traceback
from contextlib import asynccontextmanager
from typing import List

import neo4j
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.retrievers import VectorRetriever, VectorCypherRetriever, Text2CypherRetriever, ToolsRetriever
from neo4j_graphrag.generation import RagTemplate, GraphRAG

from config import (
    NEO4J_URI, NEO4J_AUTH, GOOGLE_API_KEY, GEMINI_BASE_URL,
    LLM_MODEL, EMBEDDING_MODEL, INDEX_NAME, QA_INDEX_NAME,
)
from gemini_embedder import GeminiEmbedder
from routers.admin import router as admin_router


# ─────────────────────────────────────────
# 앱 초기화
# ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트"""
    try:
        initialize_retrievers()
        print("✅ Retriever 초기화 완료")
    except Exception as e:
        print(f"⚠️ 경고: Retriever 초기화 실패: {e}")
    yield
    driver.close()
    print("🔌 Neo4j 드라이버 종료")


app = FastAPI(title="GraphRAG Demo API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Neo4j
driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)

# LLM (Gemini OpenAI 호환 엔드포인트)
llm = OpenAILLM(
    model_name=LLM_MODEL,
    model_params={"temperature": 0},
    base_url=GEMINI_BASE_URL,
    api_key=GOOGLE_API_KEY,
)

# 임베딩 (google-genai 네이티브 SDK)
embedder = GeminiEmbedder(model=EMBEDDING_MODEL, api_key=GOOGLE_API_KEY)

# app.state에 공유 객체 등록 (라우터에서 request.app.state로 접근)
app.state.driver = driver
app.state.embedder = embedder

# Retriever 전역 변수
graphrag = None


# ─────────────────────────────────────────
# Pydantic 모델 (Query API)
# ─────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    used_nodes: List[str]
    used_edges: List[str]
    retriever_used: str
    context: str = ""


# ─────────────────────────────────────────
# 유틸리티 함수
# ─────────────────────────────────────────

def extract_nodes_from_content(content: str) -> tuple[List[str], List[str]]:
    """검색 결과에서 Menu/SubMenu/MenuItem 노드와 엣지 추출"""
    nodes = []
    edges = []

    main_match = re.search(r"main_menu=\'?([^\'\\,\n]+)\'?", content)
    sub_match  = re.search(r"sub_menu=\'?([^\'\\,\n]+)\'?", content)
    item_match = re.search(r"menu_item_name=\'?([^\'\\,\n]+)\'?", content)

    if main_match:
        nodes.append(f"Menu_{main_match.group(1).strip()}")
    if sub_match and sub_match.group(1).strip():
        nodes.append(f"SubMenu_{sub_match.group(1).strip()}")
        edges.append("HAS_SUBMENU")
    if item_match:
        nodes.append(f"MenuItem_{item_match.group(1).strip()}")
        edges.append("HAS_ITEM")

    if not nodes:
        path_match = re.search(
            r'\[관련 메뉴\]\s*([\가-힣A-Za-z0-9()\s]+(?:\s*>\s*[\가-힣A-Za-z0-9()\s]+)*)',
            content
        )
        if path_match:
            parts = [p.strip() for p in path_match.group(1).split('>') if p.strip()]
            if len(parts) >= 1:
                nodes.append(f"Menu_{parts[0]}")
            if len(parts) >= 2:
                nodes.append(f"SubMenu_{parts[1]}")
                edges.append("HAS_SUBMENU")
            if len(parts) >= 3:
                nodes.append(f"MenuItem_{parts[2]}")
                edges.append("HAS_ITEM")

    return list(set(nodes)), list(set(edges))


def get_neo4j_schema() -> str:
    """Neo4j 스키마 정보 가져오기"""
    with driver.session() as session:
        node_info = session.run("""
            CALL db.schema.nodeTypeProperties()
            YIELD nodeType, propertyName, propertyTypes
            RETURN nodeType, collect(propertyName) as properties
        """).data()

        patterns = session.run("""
            MATCH (n)-[r]->(m)
            RETURN DISTINCT labels(n)[0] as source, type(r) as relationship, labels(m)[0] as target
            LIMIT 20
        """).data()

        schema_text = "=== Neo4j Schema ===\n\n노드 타입:\n"
        for node in node_info:
            schema_text += f"- {node['nodeType']}: {node['properties']}\n"

        schema_text += "\n관계 패턴:\n"
        for pattern in patterns:
            schema_text += f"- ({pattern['source']})-[:{pattern['relationship']}]->({pattern['target']})\n"

        return schema_text


# ─────────────────────────────────────────
# Retriever 초기화
# ─────────────────────────────────────────

def initialize_retrievers():
    """Retrievers 초기화"""
    global graphrag

    vector_retriever = VectorRetriever(
        driver=driver, index_name=INDEX_NAME, embedder=embedder
    )

    retrieval_query = """
    WITH node AS menuItem, score
    OPTIONAL MATCH (menuItem)<-[:HAS_ITEM]-(subMenu:SubMenu)
    OPTIONAL MATCH (subMenu)<-[:HAS_SUBMENU]-(menu:Menu)
    OPTIONAL MATCH (menuItem)<-[:HAS_ITEM]-(menu2:Menu)
    OPTIONAL MATCH (qa:QA)-[:IN_MENU]->(menuItem)
    WITH menuItem, score, subMenu, menu, menu2,
         [qa IN collect(DISTINCT qa) WHERE qa IS NOT NULL
          | 'Q: ' + qa.question + '\nA: ' + qa.answer][0..3] AS qa_texts
    OPTIONAL MATCH (menuItem)-[relEdge:RELATED_MENU]-(relMenu:MenuItem)
    WITH menuItem, score, subMenu, menu, menu2, qa_texts,
         [r IN collect(DISTINCT {name: relMenu.name, path: relMenu.menu_path, type: relEdge.type})
          WHERE r.name IS NOT NULL] AS related_menus
    RETURN
        menuItem.name AS menu_item_name,
        menuItem.description AS description,
        menuItem.menu_path AS menu_path,
        COALESCE(subMenu.name, '') AS sub_menu,
        COALESCE(menu.name, menu2.name, menuItem.main_menu) AS main_menu,
        score AS similarity_score,
        CASE WHEN size(qa_texts) > 0
             THEN reduce(s='[관련 QA 사례]\n', t IN qa_texts | s + t + '\n\n')
             ELSE '' END AS related_qa,
        CASE WHEN size(related_menus) > 0
             THEN reduce(s='[관련 메뉴]\n', r IN related_menus |
                  s + CASE r.type
                      WHEN '선행업무' THEN '→ 선행업무: '
                      WHEN '후속업무' THEN '← 후속업무: '
                      ELSE '↔ 관련메뉴: ' END + r.path + '\n')
             ELSE '' END AS related_menus_text
    """

    vector_cypher_retriever = VectorCypherRetriever(
        driver=driver, index_name=INDEX_NAME,
        retrieval_query=retrieval_query, embedder=embedder
    )

    neo4j_schema = get_neo4j_schema()
    examples = [
        """
        USER INPUT: 검침 메뉴에는 어떤 기능들이 있나요?
        CYPHER QUERY:
        MATCH (m:Menu {name: "검침"})-[:HAS_SUBMENU]->(s:SubMenu)-[:HAS_ITEM]->(i:MenuItem)
        RETURN m.name AS 대분류, s.name AS 중분류, i.name AS 소분류, i.description AS 설명
        """,
        """
        USER INPUT: 부과 기초정보에서 할 수 있는 것들을 알려주세요
        CYPHER QUERY:
        MATCH (m:Menu {name: "부과"})-[:HAS_SUBMENU]->(s:SubMenu {name: "기초정보"})-[:HAS_ITEM]->(i:MenuItem)
        RETURN i.name AS 메뉴, i.description AS 설명
        """,
        """
        USER INPUT: 전자결재 메뉴 목록을 보여주세요
        CYPHER QUERY:
        MATCH (m:Menu {name: "Xp전자결재"})-[:HAS_ITEM]->(i:MenuItem)
        RETURN i.name AS 메뉴, i.description AS 설명
        """,
        """
        USER INPUT: 전체 대분류 메뉴 목록
        CYPHER QUERY:
        MATCH (m:Menu) RETURN m.name AS 대분류메뉴
        """,
    ]

    text2cypher_retriever = Text2CypherRetriever(
        driver=driver, llm=llm, neo4j_schema=neo4j_schema, examples=examples,
    )

    qa_retrieval_query = """
    WITH node AS qa, score
    OPTIONAL MATCH (qa)-[:IN_MENU]->(m:MenuItem)
    WITH qa, score, collect(DISTINCT m.menu_path) AS menu_paths
    RETURN
        qa.question AS question,
        qa.answer AS answer,
        qa.tags AS tags,
        qa.source AS source,
        score AS similarity_score,
        CASE WHEN size(menu_paths) > 0
             THEN '[관련 메뉴] ' + reduce(s='', p IN menu_paths | s + p + ' / ')
             ELSE '' END AS menu_path
    """
    qa_retriever = VectorCypherRetriever(
        driver=driver, index_name=QA_INDEX_NAME,
        retrieval_query=qa_retrieval_query, embedder=embedder,
    )

    vector_tool = vector_retriever.convert_to_tool(
        name="vector_retriever",
        description="기능 설명이나 키워드로 관련 메뉴를 의미 기반으로 검색. 예: '급여 계산', '차량 등록', '수납 처리 방법'"
    )
    vector_cypher_tool = vector_cypher_retriever.convert_to_tool(
        name="vectorcypher_retriever",
        description="특정 기능의 상세 설명과 메뉴 경로(대분류>중분류>소분류)를 함께 조회. 예: '입주 등록은 어떻게 하나요?', '이 기능이 어느 메뉴에 있나요?'"
    )
    text2cypher_tool = text2cypher_retriever.convert_to_tool(
        name="text2cypher_retriever",
        description="특정 대분류/중분류의 전체 메뉴 목록 조회, 메뉴 구조 파악에 사용. 예: '검침 메뉴 전체 목록', '회계 메뉴에는 뭐가 있나요?', '전체 메뉴 목록'"
    )
    qa_tool = qa_retriever.convert_to_tool(
        name="qa_retriever",
        description="오류 해결, 문제 상황, 사용 방법 등 실제 질문과 답변 사례를 검색할 때 사용. 예: '검침값이 안 나와요', '전표가 생성되지 않아요', '~하는 방법'"
    )

    tools_retriever = ToolsRetriever(
        driver=driver, llm=llm,
        tools=[vector_tool, vector_cypher_tool, text2cypher_tool, qa_tool],
    )

    prompt_template = RagTemplate(
        template="""당신은 XPERP 시스템의 매뉴얼 안내 챗봇입니다.
사용자의 질문에 대해 검색된 정보를 바탕으로 정확하고 친절하게 안내하세요.

질문: {query_text}

검색된 정보:
{context}

지침:
1. 검색된 정보가 메뉴 설명인 경우: 메뉴 경로를 [대분류 > 중분류 > 소분류] 형식으로 표시하세요.
2. 검색된 정보가 QA(질문/답변) 사례인 경우: 답변 내용을 중심으로 명확하게 안내하세요.
3. 메뉴 설명과 함께 [관련 QA 사례]가 포함된 경우: QA 사례를 활용하여 더 구체적인 안내를 제공하세요.
4. QA에 [관련 메뉴] 경로가 포함된 경우: 해당 메뉴 경로를 답변에 포함하세요.
5. [관련 메뉴] 섹션이 있는 경우 반드시 함께 안내하세요:
   - '→ 선행업무'는 이 메뉴를 사용하기 전에 먼저 처리해야 하는 메뉴입니다.
   - '← 후속업무'는 이 메뉴 처리 후 이어서 진행하는 메뉴입니다.
   - '↔ 관련메뉴'는 함께 참고하면 유용한 메뉴입니다.
6. 여러 관련 항목이 있다면 모두 안내하세요.
7. 검색 결과에 없는 내용은 추측하지 마세요.

답변:""",
        expected_inputs=["context", "query_text"]
    )

    graphrag = GraphRAG(
        llm=llm, retriever=tools_retriever, prompt_template=prompt_template
    )


# ─────────────────────────────────────────
# 라우터 등록 및 정적 파일
# ─────────────────────────────────────────

app.include_router(admin_router)
app.mount("/libs", StaticFiles(directory="libs"), name="libs")


@app.get("/")
async def root():
    """루트 페이지 - 시각화 HTML 반환"""
    return FileResponse("index.html")


@app.get("/admin")
async def admin_page():
    """관리 화면"""
    return FileResponse("admin.html")


# ─────────────────────────────────────────
# Graph / Query / Health API
# ─────────────────────────────────────────

@app.get("/graph")
def get_graph():
    """전체 그래프 구조 반환"""
    try:
        with driver.session() as session:
            nodes_result = session.run("""
                MATCH (n)
                WHERE n:Menu OR n:SubMenu OR n:MenuItem OR n:Task
                RETURN
                    elementId(n) as id,
                    labels(n)[0] as label,
                    n.name as title,
                    CASE
                        WHEN n:Menu     THEN {name: n.name}
                        WHEN n:SubMenu  THEN {name: n.name, main_menu: n.main_menu}
                        WHEN n:MenuItem THEN {name: n.name, description: n.description, menu_path: n.menu_path}
                        WHEN n:Task     THEN {name: n.name, domain: n.domain, type: n.type, frequency: n.frequency}
                        ELSE {}
                    END as properties
            """)

            nodes = []
            for record in nodes_result:
                nodes.append({
                    "id": record["id"],
                    "label": record["label"],
                    "title": record["title"] or "No title",
                    "properties": dict(record["properties"])
                })

            edges_result = session.run("""
                MATCH (n)-[r]->(m)
                WHERE (
                    (n:Menu OR n:SubMenu OR n:MenuItem)
                    AND (m:Menu OR m:SubMenu OR m:MenuItem)
                ) OR (
                    n:MenuItem AND type(r) = 'RELATED_TASK' AND m:Task
                )
                RETURN
                    elementId(r) as id,
                    elementId(n) as source,
                    elementId(m) as target,
                    type(r) as relationship
            """)

            edges = []
            for record in edges_result:
                edges.append({
                    "id": record["id"],
                    "source": record["source"],
                    "target": record["target"],
                    "relationship": record["relationship"]
                })

            return {"nodes": nodes, "edges": edges}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph retrieval failed: {str(e)}")


@app.post("/query", response_model=QueryResponse)
def query_graphrag(req: QueryRequest):
    """GraphRAG 쿼리 실행 및 사용된 노드/엣지 반환"""
    try:
        if graphrag is None:
            raise HTTPException(status_code=500, detail="GraphRAG not initialized")

        last_err = None
        for attempt in range(4):
            try:
                result = graphrag.search(query_text=req.question, return_context=True)
                break
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = 15 * (attempt + 1)
                    print(f"⚠️  LLM 429 - {wait}초 후 재시도 ({attempt+1}/4)")
                    time.sleep(wait)
                    last_err = e
                else:
                    raise
        else:
            raise last_err

        used_nodes = []
        used_edges = []
        retriever_used = "unknown"
        context_str = ""

        print(f"\n=== 쿼리: {req.question} ===")

        try:
            if hasattr(result, 'retriever_result') and result.retriever_result:
                retriever_result = result.retriever_result

                if hasattr(retriever_result, 'metadata') and retriever_result.metadata:
                    tools_selected = retriever_result.metadata.get('tools_selected', [])
                    print(f"📌 선택된 Retriever: {tools_selected}")

                if hasattr(retriever_result, 'items') and retriever_result.items:
                    for item in retriever_result.items:
                        if hasattr(item, 'metadata') and item.metadata:
                            if 'tool' in item.metadata:
                                retriever_used = item.metadata['tool']
                            elif 'retriever_name' in item.metadata:
                                retriever_used = item.metadata['retriever_name']

                            if 'id' in item.metadata and 'nodeLabels' in item.metadata:
                                node_id = item.metadata['id']
                                node_labels = item.metadata['nodeLabels']
                                score = item.metadata.get('score', 1.0)

                                our_labels = {'MenuItem', 'Menu', 'SubMenu', 'QA'}
                                if our_labels.intersection(set(node_labels)) and score >= 0.7:
                                    used_nodes.append(f"ElementId_{node_id}")

                        if hasattr(item, 'content'):
                            content = str(item.content)
                            context_str += content + "\n\n"

                            nodes_from_content, edges_from_content = extract_nodes_from_content(content)
                            used_nodes.extend(nodes_from_content)
                            used_edges.extend(edges_from_content)

        except Exception as parse_error:
            print(f"⚠️ 파싱 오류: {parse_error}")

        used_nodes = list(set(used_nodes))
        used_edges = list(set(used_edges))

        print(f"🎯 최종 반환 노드: {used_nodes}")
        print(f"🔗 최종 반환 엣지: {used_edges}")

        return QueryResponse(
            answer=result.answer if hasattr(result, 'answer') else str(result),
            used_nodes=used_nodes,
            used_edges=used_edges,
            retriever_used=retriever_used,
            context=context_str[:1000] if context_str else "No context available"
        )

    except Exception as e:
        print(f"Query failed: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@app.get("/health")
def health_check():
    """헬스 체크"""
    try:
        with driver.session() as session:
            session.run("RETURN 1")
        return {"status": "healthy", "neo4j": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"unhealthy: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800)
