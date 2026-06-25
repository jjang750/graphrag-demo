# AGENTS.md — graphrag-demo

XPERP 매뉴얼 챗봇. Neo4j 그래프 DB에 QA 문서를 저장하고 GraphRAG(벡터 검색 + 그래프 순회)로 답변하는 시스템.

## 역할
- XPERP 매뉴얼·QA 데이터를 Neo4j에 적재 (MenuItem / QA / RELATED_MENU / IN_MENU 노드/관계).
- FastAPI(`app.py`) 엔드포인트로 질문 수신.
- Gemini LLM + VectorCypherRetriever + ToolsRetriever 로 답변 + 그래프 시각화.

## 기술 스택
- Python 3.11 (`.venv\Scripts\python.exe` 사용)
- 패키지 관리: **uv** (`uv pip install -r requirements.txt`)
- FastAPI · Neo4j Python driver · neo4j-graphrag
- LLM: `gemini-3-flash-preview`
- 임베딩: `gemini-embedding-001`
- Docker (`docker-compose.yml` — Neo4j 컨테이너 추정)

## 구조 (주요)
```
app.py                       # FastAPI 진입점
config.py
init_db.py                   # Neo4j 스키마/노드 초기화
read_excel.py                # 매뉴얼/QA 원본 엑셀 적재
gemini_embedder.py           # 임베딩 생성
add_qa.py                    # QA 노드 추가
build_concepts.py            # 컨셉(개념) 노드 구성
analyze_tags.py, normalize_tags.py, update_qa_tags.py
link_concepts.py             # 개념-QA 연결
link_qa_menu.py
link_qa_menu_embed.py        # 임베딩 기반 메뉴-QA 매핑
link_qa_tag.py
migrate_concept_to_task.py
update_menu.py
test_parallel_search.py      # 병렬 검색 벤치마크
admin.html / index.html      # UI
routers/                     # FastAPI 라우터
libs/                        # 내부 헬퍼
manuals/                     # 원본 매뉴얼 (엑셀/PDF)
docs/                        # 설계 문서
claudedocs/                  # Claude 작성 분석 문서
AGENT.md                     # 실행 명령 메모 (uv, python 경로 등)
docker-compose.yml
requirements.txt
```

## 실행
```
# 의존성
uv pip install -r requirements.txt

# Neo4j 기동
docker compose up -d

# 초기 적재 (최초 1회)
.venv\Scripts\python.exe init_db.py
.venv\Scripts\python.exe read_excel.py
.venv\Scripts\python.exe add_qa.py
# (필요 시) build_concepts.py, link_*.py 순차 실행

# API 기동
.venv\Scripts\python.exe app.py

# 질의
curl -X POST http://localhost:8800/query \
  -H "Content-Type: application/json" \
  -d '{"question": "..."}'
```

## 주의
- 진실 소스는 `AGENT.md` (실행 명령) + `README.md` (아키텍처). 둘 다 유지.
- 루트에 다수의 ETL 스크립트가 흩어져 있음. 실행 순서 의존성 있음 — 새 노드 추가 시 영향 확인.
- 본 프로젝트와 `xperp-manual-guide`, `xperp_qna_chatbot*` 가 모두 XPERP 매뉴얼 도메인. 어느 것이 최신 발전 라인인지 확인 필요.
