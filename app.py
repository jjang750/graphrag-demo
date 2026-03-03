import os
import re
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import neo4j
from dotenv import load_dotenv
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.retrievers import VectorRetriever, VectorCypherRetriever, Text2CypherRetriever, ToolsRetriever
from neo4j_graphrag.generation import RagTemplate, GraphRAG
from gemini_embedder import GeminiEmbedder

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트"""
    # Startup
    try:
        initialize_retrievers()
        print("✅ Retriever 초기화 완료")
    except Exception as e:
        print(f"⚠️ 경고: Retriever 초기화 실패: {e}")
    yield
    # Shutdown
    driver.close()
    print("🔌 Neo4j 드라이버 종료")


app = FastAPI(title="GraphRAG Demo API", lifespan=lifespan)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Neo4j 연결
URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
AUTH = ("neo4j", os.getenv("NEO4J_PASSWORD", "password"))
driver = neo4j.GraphDatabase.driver(URI, auth=AUTH)

# Google AI Studio OpenAI 호환 엔드포인트 설정
# Gemini API는 OpenAI SDK와 호환되는 엔드포인트를 제공함
# 참고: https://ai.google.dev/gemini-api/docs/openai
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# LLM 및 Embedder 설정 (Gemini 모델 사용)
llm = OpenAILLM(
    model_name="gemini-3-flash-preview",
    model_params={"temperature": 0},
    # OpenAI 클라이언트 파라미터: Gemini 호환 엔드포인트로 라우팅
    base_url=GEMINI_BASE_URL,
    api_key=GOOGLE_API_KEY,
)
# 임베딩: google-generativeai 네이티브 SDK 사용
# OpenAI 호환 엔드포인트는 text-embedding-004를 미지원하므로 네이티브 SDK로 처리
embedder = GeminiEmbedder(
    model="gemini-embedding-001",
    api_key=GOOGLE_API_KEY,
)

# 전역 변수로 retriever 저장
INDEX_NAME = "menu_vector_index"
QA_INDEX_NAME = "qa_vector_index"
vector_retriever = None
vector_cypher_retriever = None
qa_retriever = None
text2cypher_retriever = None
tools_retriever = None
graphrag = None


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    used_nodes: List[str]
    used_edges: List[str]
    retriever_used: str
    context: str = ""


def extract_nodes_from_content(content: str) -> tuple[List[str], List[str]]:
    """검색 결과에서 Menu/SubMenu/MenuItem 노드와 엣지 추출"""
    nodes = []
    edges = []

    # ── vectorcypher_retriever 포맷 ──────────────────────────────
    # main_menu='검침' sub_menu='전기검침' menu_item_name='전기검침'
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

    # ── qa_retriever 포맷 ────────────────────────────────────────
    # menu_path='[관련 메뉴] 검침 > 수도검침 > 수도사용량조회 / '
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


def initialize_retrievers():
    """Retrievers 초기화"""
    global vector_retriever, vector_cypher_retriever, qa_retriever, text2cypher_retriever, tools_retriever, graphrag

    # Vector Retriever
    vector_retriever = VectorRetriever(
        driver=driver,
        index_name=INDEX_NAME,
        embedder=embedder
    )

    # VectorCypher Retriever
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
        driver=driver,
        index_name=INDEX_NAME,
        retrieval_query=retrieval_query,
        embedder=embedder
    )

    # Text2Cypher Retriever
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
        driver=driver,
        llm=llm,
        neo4j_schema=neo4j_schema,
        examples=examples,
    )

    # QA Retriever
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
        driver=driver,
        index_name=QA_INDEX_NAME,
        retrieval_query=qa_retrieval_query,
        embedder=embedder,
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
        driver=driver,
        llm=llm,
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
        llm=llm,
        retriever=tools_retriever,
        prompt_template=prompt_template
    )


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


@app.get("/")
async def root():
    """루트 페이지 - 시각화 HTML 반환"""
    return FileResponse("index.html")


@app.get("/admin")
async def admin():
    """관리 화면"""
    return FileResponse("admin.html")


# ─────────────────────────────────────────
# Admin API
# ─────────────────────────────────────────

@app.get("/admin/menus")
async def admin_get_menus(q: str = Query("", description="메뉴 검색어")):
    """메뉴 트리 + 각 MenuItem의 연결 QA 수 반환"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (m:MenuItem)
                WHERE $q = '' OR m.name CONTAINS $q OR m.menu_path CONTAINS $q
                OPTIONAL MATCH (qa:QA)-[:IN_MENU]->(m)
                WITH m, count(qa) AS qa_count
                OPTIONAL MATCH (s:SubMenu)-[:HAS_ITEM]->(m)
                OPTIONAL MATCH (menu:Menu)-[:HAS_SUBMENU]->(s)
                OPTIONAL MATCH (menu2:Menu)-[:HAS_ITEM]->(m)
                RETURN
                    m.id            AS id,
                    m.name          AS name,
                    m.menu_path     AS menu_path,
                    m.main_menu     AS main_menu,
                    m.sub_menu      AS sub_menu,
                    qa_count
                ORDER BY m.main_menu, m.sub_menu, m.name
            """, q=q)
            items = [dict(r) for r in result]
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/qa")
async def admin_get_qa(
    menuitem_id: Optional[str] = Query(None, description="연결된 QA 조회 (MenuItem.id)"),
    q: Optional[str] = Query(None, description="QA 검색어"),
    source: Optional[str] = Query(None, description="소스 파일 필터"),
    unlinked: bool = Query(False, description="미연결 QA만 조회"),
    limit: int = Query(50, le=200)
):
    """QA 목록 조회 — 필터: menuitem_id | 검색어 | 소스 | 미연결"""
    try:
        with driver.session() as session:
            if menuitem_id:
                # 특정 MenuItem에 연결된 QA
                result = session.run("""
                    MATCH (q:QA)-[:IN_MENU]->(m:MenuItem {id: $mid})
                    RETURN q.id AS id, q.question AS question,
                           q.answer AS answer, q.tags AS tags, q.source AS source
                    ORDER BY q.id
                    LIMIT $limit
                """, mid=menuitem_id, limit=limit)
            elif unlinked:
                # IN_MENU 연결이 없는 QA
                where = "WHERE NOT (q)-[:IN_MENU]->()"
                if source:
                    where += " AND q.source = $source"
                if q:
                    where += " AND (q.question CONTAINS $q OR q.answer CONTAINS $q)"
                result = session.run(f"""
                    MATCH (q:QA) {where}
                    RETURN q.id AS id, q.question AS question,
                           q.answer AS answer, q.tags AS tags, q.source AS source
                    ORDER BY q.id
                    LIMIT $limit
                """, q=q or "", source=source or "", limit=limit)
            else:
                # 검색어 조회
                where_clauses = []
                if source:
                    where_clauses.append("q.source = $source")
                if q:
                    where_clauses.append("(q.question CONTAINS $q OR q.answer CONTAINS $q)")
                where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                result = session.run(f"""
                    MATCH (q:QA) {where}
                    RETURN q.id AS id, q.question AS question,
                           q.answer AS answer, q.tags AS tags, q.source AS source
                    ORDER BY q.id
                    LIMIT $limit
                """, q=q or "", source=source or "", limit=limit)

            rows = []
            for r in result:
                rows.append({
                    "id": r["id"],
                    "question": r["question"],
                    "answer": r["answer"],
                    "tags": r["tags"] or [],
                    "source": r["source"],
                })
        return {"items": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/qa/{qa_id}/detail")
async def admin_get_qa_detail(qa_id: str):
    """QA 단건 조회 (ID 직접 지정)"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (q:QA {id: $qa_id})
                RETURN q.id AS id, q.question AS question,
                       q.answer AS answer, q.tags AS tags, q.source AS source
            """, qa_id=qa_id)
            row = result.single()
            if not row:
                raise HTTPException(status_code=404, detail="QA를 찾을 수 없습니다")
            return {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "tags": row["tags"] or [],
                "source": row["source"],
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/qa/{qa_id}/menus")
async def admin_get_qa_menus(qa_id: str):
    """특정 QA에 연결된 MenuItem 목록"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (q:QA {id: $qa_id})-[:IN_MENU]->(m:MenuItem)
                RETURN m.id AS id, m.name AS name, m.menu_path AS menu_path
            """, qa_id=qa_id)
            items = [dict(r) for r in result]
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LinkRequest(BaseModel):
    qa_id: str
    menuitem_id: str


class MenuRelationRequest(BaseModel):
    source_id: str
    target_id: str
    rel_type: str   # "관련메뉴" | "선행업무" | "후속업무"
    memo: str = ""


@app.post("/admin/qa-link")
async def admin_create_link(req: LinkRequest):
    """QA ↔ MenuItem 연결 생성"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (q:QA {id: $qa_id})
                MATCH (m:MenuItem {id: $mid})
                MERGE (q)-[r:IN_MENU]->(m)
                RETURN q.id AS qa_id, m.id AS menu_id, m.menu_path AS menu_path
            """, qa_id=req.qa_id, mid=req.menuitem_id)
            row = result.single()
            if not row:
                raise HTTPException(status_code=404, detail="QA 또는 MenuItem을 찾을 수 없습니다")
        return {"status": "ok", "qa_id": row["qa_id"], "menu_path": row["menu_path"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/qa-link")
async def admin_delete_link(req: LinkRequest):
    """QA ↔ MenuItem 연결 해제"""
    try:
        with driver.session() as session:
            session.run("""
                MATCH (q:QA {id: $qa_id})-[r:IN_MENU]->(m:MenuItem {id: $mid})
                DELETE r
            """, qa_id=req.qa_id, mid=req.menuitem_id)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/menu-relations")
async def admin_get_menu_relations():
    """MenuItem 간 RELATED_MENU 관계 목록 조회"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (a:MenuItem)-[r:RELATED_MENU]->(b:MenuItem)
                RETURN
                    a.id AS source_id, a.name AS source_name, a.menu_path AS source_path,
                    r.type AS rel_type, r.memo AS memo,
                    b.id AS target_id, b.name AS target_name, b.menu_path AS target_path
                ORDER BY a.menu_path
            """)
            items = [dict(r) for r in result]
        return {"items": items, "count": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/menu-relation")
async def admin_create_menu_relation(req: MenuRelationRequest):
    """MenuItem ↔ MenuItem 관계 생성"""
    if req.source_id == req.target_id:
        raise HTTPException(status_code=400, detail="소스와 대상 메뉴가 동일합니다")
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (a:MenuItem {id: $source_id}), (b:MenuItem {id: $target_id})
                MERGE (a)-[r:RELATED_MENU {type: $rel_type}]->(b)
                SET r.memo = $memo, r.created_at = datetime()
                RETURN a.menu_path AS source_path, b.menu_path AS target_path
            """, source_id=req.source_id, target_id=req.target_id,
                rel_type=req.rel_type, memo=req.memo)
            row = result.single()
            if not row:
                raise HTTPException(status_code=404, detail="MenuItem을 찾을 수 없습니다")
        return {"status": "ok", "source_path": row["source_path"], "target_path": row["target_path"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/menu-relation")
async def admin_delete_menu_relation(req: MenuRelationRequest):
    """MenuItem ↔ MenuItem 관계 삭제"""
    try:
        with driver.session() as session:
            session.run("""
                MATCH (a:MenuItem {id: $source_id})-[r:RELATED_MENU {type: $rel_type}]->(b:MenuItem {id: $target_id})
                DELETE r
            """, source_id=req.source_id, target_id=req.target_id, rel_type=req.rel_type)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/sources")
async def admin_get_sources():
    """QA source 목록 + 수량"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (q:QA)
                RETURN q.source AS source, count(q) AS cnt
                ORDER BY cnt DESC
            """)
            items = [{"source": r["source"], "count": r["cnt"]} for r in result]
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/review")
async def admin_review(
    source: str = Query(..., description="검토할 QA 소스"),
    limit: int = Query(200, le=500),
):
    """소스별 연결된 QA + 메뉴 한 번에 조회 (오탐 검토용)"""
    try:
        with driver.session() as session:
            result = session.run("""
                MATCH (q:QA {source: $source})-[:IN_MENU]->(m:MenuItem)
                WITH q, collect({
                    id: m.id,
                    name: m.name,
                    path: m.menu_path,
                    main: m.main_menu
                }) AS menus
                RETURN q.id AS id, q.question AS question,
                       q.source AS source, q.tags AS tags, menus
                ORDER BY q.id
                LIMIT $limit
            """, source=source, limit=limit)
            items = []
            for r in result:
                items.append({
                    "id": r["id"],
                    "question": r["question"],
                    "source": r["source"],
                    "tags": r["tags"] or [],
                    "menus": list(r["menus"]),
                })
        return {"items": items, "count": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph")
async def get_graph():
    """전체 그래프 구조 반환"""
    try:
        with driver.session() as session:
            # 노드 가져오기 (Menu 계층 + Task)
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

            # 엣지 가져오기: Menu 계층 엣지 + MenuItem→Task RELATED_TASK 엣지
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

            return {
                "nodes": nodes,
                "edges": edges
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph retrieval failed: {str(e)}")


@app.post("/query", response_model=QueryResponse)
async def query_graphrag(req: QueryRequest):
    """GraphRAG 쿼리 실행 및 사용된 노드/엣지 반환"""
    import time as _time
    try:
        if graphrag is None:
            raise HTTPException(status_code=500, detail="GraphRAG not initialized")

        # 429 분당 한도 초과 시 자동 재시도
        last_err = None
        for attempt in range(4):
            try:
                result = graphrag.search(query_text=req.question, return_context=True)
                break
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    wait = 15 * (attempt + 1)
                    print(f"⚠️  LLM 429 - {wait}초 후 재시도 ({attempt+1}/4)")
                    _time.sleep(wait)
                    last_err = e
                else:
                    raise
        else:
            raise last_err

        # 사용된 노드와 엣지 추출
        used_nodes = []
        used_edges = []
        retriever_used = "unknown"
        context_str = ""

        print(f"\n=== 쿼리: {req.question} ===")

        # result 객체 처리
        try:
            if hasattr(result, 'retriever_result') and result.retriever_result:
                retriever_result = result.retriever_result

                # 선택된 retriever 확인
                if hasattr(retriever_result, 'metadata') and retriever_result.metadata:
                    tools_selected = retriever_result.metadata.get('tools_selected', [])
                    print(f"📌 선택된 Retriever: {tools_selected}")

                # items 처리
                if hasattr(retriever_result, 'items') and retriever_result.items:
                    filtered_count = 0

                    for idx, item in enumerate(retriever_result.items):
                        # retriever 이름 추출
                        if hasattr(item, 'metadata') and item.metadata:
                            if 'tool' in item.metadata:
                                retriever_used = item.metadata['tool']
                            elif 'retriever_name' in item.metadata:
                                retriever_used = item.metadata['retriever_name']

                            # ElementId 기반 노드 매칭 (vector/vectorcypher retriever)
                            if 'id' in item.metadata and 'nodeLabels' in item.metadata:
                                node_id = item.metadata['id']
                                node_labels = item.metadata['nodeLabels']
                                score = item.metadata.get('score', 1.0)

                                our_labels = {'MenuItem', 'Menu', 'SubMenu', 'QA'}
                                if our_labels.intersection(set(node_labels)) and score >= 0.7:
                                    used_nodes.append(f"ElementId_{node_id}")
                                    filtered_count += 1
                            else:
                                filtered_count += 1

                        # content 처리 + 이름 기반 노드 추출 (전체 retriever 공통)
                        if hasattr(item, 'content'):
                            content = str(item.content)
                            context_str += content + "\n\n"

                            nodes_from_content, edges_from_content = extract_nodes_from_content(content)
                            used_nodes.extend(nodes_from_content)
                            used_edges.extend(edges_from_content)

        except Exception as parse_error:
            print(f"⚠️ 파싱 오류: {parse_error}")

        # 중복 제거
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
        import traceback
        error_detail = f"Query failed: {str(e)}\n{traceback.format_exc()}"
        print(error_detail)
        raise HTTPException(status_code=500, detail=error_detail)


@app.get("/health")
async def health_check():
    """헬스 체크"""
    try:
        with driver.session() as session:
            session.run("RETURN 1")
        return {"status": "healthy", "neo4j": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
