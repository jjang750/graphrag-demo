# XPERP 매뉴얼 챗봇 (GraphRAG 기반)

XPERP 매뉴얼 문서(QA)를 Neo4j 그래프 DB에 저장하고,
GraphRAG(벡터 검색 + 그래프 순회)로 질문에 답변하는 챗봇 시스템.

```
┌─────────────────┐
│  챗봇 UI        │  ← 질문 입력 / 답변 + 그래프 시각화
└────────┬────────┘
         │
    FastAPI (app.py)
         │
┌────────▼────────┐
│   GraphRAG      │  ← VectorCypherRetriever + ToolsRetriever
│   (Gemini LLM)  │
└────────┬────────┘
         │
┌────────▼────────┐
│   Neo4j DB      │  ← MenuItem / QA / RELATED_MENU / IN_MENU
└─────────────────┘
```

---

## 그래프 데이터 구조

```
Menu ──HAS_SUBMENU──▶ SubMenu ──HAS_ITEM──▶ MenuItem ◀──IN_MENU── QA
                                                │
                                         RELATED_MENU
                                                │
                                            MenuItem
```

| 노드 | 주요 속성 |
|------|-----------|
| `Menu` | name |
| `SubMenu` | id, name, main_menu |
| `MenuItem` | id, name, menu_path, main_menu, sub_menu, description, embedding |
| `QA` | id, question, answer, tags, source, embedding |

| 관계 | 의미 |
|------|------|
| `IN_MENU` | QA → MenuItem 연결 |
| `RELATED_MENU` | MenuItem ↔ MenuItem 업무 흐름 연결 |

---

## 설치

### 사전 요구사항

- Python 3.11+
- Neo4j 5.x (Docker 또는 Neo4j Desktop)
- Google API Key (Gemini LLM + 임베딩)

### Neo4j 실행 (Docker)

```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:latest
```

### 패키지 설치

```bash
uv venv
uv pip install -r requirements.txt
uv pip install google-genai
```

### 환경 변수 설정

```bash
cp .env.example .env
```

`.env` 파일:

```
NEO4J_URI=neo4j://localhost:7687
NEO4J_PASSWORD=password
GOOGLE_API_KEY=your_google_api_key_here
```

---

## 초기 데이터 구축

### 1. 메뉴 초기화 (`init_db.py`)

XPERP 메뉴 목록 엑셀(`xperp_menu_list.xlsx`)에서 MenuItem 노드를 생성하고
Gemini 임베딩을 생성하여 Neo4j에 저장합니다.

```bash
.venv\Scripts\python.exe init_db.py
```

- 소스: `xperp_menu_list.xlsx` (대분류/중분류/소분류/설명 컬럼 포함)
- Menu / SubMenu / MenuItem 노드 생성
- MenuItem별 Gemini 임베딩 생성 (`gemini-embedding-001`, 3072차원)
- `menu_vector_index` 벡터 인덱스 생성

### 2. QA 데이터 임포트 (`add_qa.py`)

`manuals/` 폴더의 QA .txt 파일을 파싱하여 QA 노드를 생성하고 Gemini 임베딩을 저장합니다.

```bash
# 기본 경로 (manuals/*.txt)
.venv\Scripts\python.exe add_qa.py

# 특정 폴더 지정
.venv\Scripts\python.exe add_qa.py --dir manuals/QA_20260306
```

- 이미 임베딩이 완료된 QA는 자동 스킵 (중단 후 이어서 실행 가능)
- API 한도 초과(429) 시 자동 재시도

### 3. QA 태그 정규화 (`update_qa_tags.py`)

QA .txt 파일의 태그를 Gemini LLM으로 정규화하여 MenuItem 이름과 매칭 가능한 형태로 변환합니다.

```bash
# 기본 경로 (manuals/*.txt)
.venv\Scripts\python.exe update_qa_tags.py

# 특정 폴더 지정
.venv\Scripts\python.exe update_qa_tags.py --dir manuals/QA_20260306

# 미리 보기 (파일/DB 수정 없음)
.venv\Scripts\python.exe update_qa_tags.py --dir manuals/QA_20260306 --dry-run

# 특정 소스만 처리
.venv\Scripts\python.exe update_qa_tags.py --source 부과qna
```

- .txt 파일의 T 라인에 MenuItem 태그 추가 (`.bak` 백업 생성)
- Neo4j QA 노드의 `tags` 속성도 동기화
- 콜론(`: `) 및 탭(`\t`) 구분자 형식 모두 지원

### 4. QA ↔ 메뉴 연결

#### 태그 기반 매핑 (`link_qa_tag.py`)

QA의 정규화된 태그와 MenuItem 이름을 매칭하여 `IN_MENU` 엣지를 생성합니다.

```bash
# 미리 보기 (DB 수정 없음)
.venv\Scripts\python.exe link_qa_tag.py --dry-run

# 실제 적용
.venv\Scripts\python.exe link_qa_tag.py

# 기존 연결 초기화 후 재생성
.venv\Scripts\python.exe link_qa_tag.py --clear-existing
```

#### 임베딩 유사도 기반 매핑 (`link_qa_menu_embed.py`)

QA 임베딩 ↔ MenuItem 임베딩의 코사인 유사도로 추가 연결합니다.

```bash
# 미리 보기
.venv\Scripts\python.exe link_qa_menu_embed.py --dry-run

# 임계값 0.75, QA당 최대 1개 메뉴 연결
.venv\Scripts\python.exe link_qa_menu_embed.py --threshold 0.75 --top-k 1
```

---

## 신규 QA 파일 추가

새 QA .txt 파일을 `manuals/<폴더명>/`에 넣은 후 아래 순서로 처리합니다.

### QA 파일 형식

탭(`\t`) 또는 콜론(`: `) 구분자 모두 지원합니다.

```
Q1	질문 텍스트
A1	"답변 텍스트"
T1	#태그1 #태그2

Q2	질문 텍스트
A2	답변 텍스트
T2	#태그1 #태그2
```

### 처리 순서

#### 1단계: 태그 정규화 (Gemini LLM으로 MenuItem 태그 추가)

```bash
# 미리 보기
.venv\Scripts\python.exe update_qa_tags.py --dir manuals/QA_20260306 --dry-run

# 실제 적용 (.txt 파일 + Neo4j 동시 업데이트)
.venv\Scripts\python.exe update_qa_tags.py --dir manuals/QA_20260306
```

#### 2단계: Neo4j에 QA 임포트 (임베딩 생성)

```bash
.venv\Scripts\python.exe add_qa.py --dir manuals/QA_20260306
```

> **참고**: 1단계에서 .txt 파일에 MenuItem 태그가 추가된 상태이므로,
> `add_qa.py`가 실행될 때 정규화된 태그까지 함께 임포트됩니다.

#### 3단계: 메뉴 연결 (IN_MENU 엣지 생성)

```bash
# 미리 보기
.venv\Scripts\python.exe link_qa_tag.py --dry-run

# 실제 적용 (기존 연결 유지, 신규만 추가)
.venv\Scripts\python.exe link_qa_tag.py
```

### 소스별 도메인 매핑

`update_qa_tags.py`는 파일명(소스)에 따라 후보 MenuItem 범위를 제한하여 정확도를 높입니다.

| 파일명(소스) | 대상 도메인 |
|-------------|------------|
| `부과qna`, `부과` | 부과 |
| `수납qna` | 수납 |
| `검침qna` | 검침 |
| `회계qna`, `회계` | 회계 |
| `입주자qna` | 입주자 |
| `단지qna`, `단지` | 단지관리 |
| `인사급여qna`, `인사급여` | 인사/급여 |
| `투표qna` | 입주자 |
| `아이디qna` | 시스템 |
| `민원` | 단지관리 |
| `기타qna`, `qna` | 전체 도메인 |

새 소스를 추가하려면 `update_qa_tags.py`의 `DOMAIN_MAP`에 항목을 추가하세요.

---

## 서버 실행

```bash
.venv\Scripts\uvicorn app:app --host 0.0.0.0 --port 8800 --reload
```

| 경로 | 설명 |
|------|------|
| `http://localhost:8800` | 챗봇 UI |
| `http://localhost:8800/admin` | 관리자 UI |

---

## 메뉴 목록 갱신

새 메뉴 CSV(`docs/xperp_menu_list_all.csv`)가 업데이트됐을 때 사용합니다.

### CSV 형식

```
대분류,중분류,소분류
시스템,사용자관리,사용자정보
부과,부과처리,관리비부과처리
...
```

### 갱신 절차

#### 1단계: 메뉴 노드 갱신

```bash
# 변경 내용 미리 확인 (Neo4j 수정 없음)
.venv\Scripts\python.exe update_menu.py

# 실제 적용 (삭제된 메뉴 중 QA 연결 없는 것만 삭제)
.venv\Scripts\python.exe update_menu.py --delete
```

**동작 규칙:**

| 상태 | 처리 |
|------|------|
| CSV에 있고 Neo4j에도 있음 | name / menu_path 갱신, description·embedding 보존 |
| CSV에 있고 Neo4j에 없음 | 신규 노드 생성 (description 빈 값, embedding 없음) |
| CSV에 없고 Neo4j에 있음 | `--delete` 시 삭제 (단, QA 연결된 메뉴는 보존) |

#### 2단계: 신규 메뉴에 설명 입력 (관리자 UI)

신규 추가된 메뉴는 description이 없어 벡터 검색 대상에서 제외됩니다.
관리자 UI의 **📝 메뉴설명** 탭에서 직접 입력합니다.

1. `http://localhost:8800/admin` 접속
2. **📝 메뉴설명** 탭 클릭
3. "설명 없는 메뉴만 보기" 체크 → 대상 목록 확인
4. 메뉴 클릭 → 설명 입력 → **✨ 저장 + 임베딩 생성** 클릭

#### 3단계: QA 재매핑

```bash
# 태그 기반 매핑 재실행 (신규 메뉴 포함, 기존 연결 유지)
.venv\Scripts\python.exe link_qa_tag.py

# 임베딩 기반 매핑 (description이 있는 메뉴만 대상)
.venv\Scripts\python.exe link_qa_menu_embed.py --threshold 0.75
```

---

## 관리자 UI 기능

`http://localhost:8800/admin`

### 📄 QA관리 탭

| 기능 | 설명 |
|------|------|
| 메뉴 목록 | 좌측 패널에서 메뉴 클릭 → 연결된 QA 목록 표시 |
| 연결됨 탭 | 선택 메뉴에 연결된 QA 목록 |
| 미연결 탭 | 어떤 메뉴에도 연결되지 않은 QA 목록 |
| 검색 탭 | 질문·답변 키워드 또는 소스 파일로 QA 검색 |
| ⚠️ 검토 탭 | 소스별 연결 상태 확인, 오탐 QA 연결 해제 |
| QA 편집 | QA 클릭 → 상세 패널 → ✏️ 편집 버튼으로 질문·답변·태그 수정 |
| 메뉴 연결 추가 | 상세 패널 하단에서 메뉴 검색 후 연결 |
| 메뉴 연결 해제 | 연결된 메뉴 옆 "연결 해제" 버튼 클릭 |
| 📥 QA 내보내기 | 메뉴별 QA를 JSON 파일 묶음(ZIP)으로 다운로드 |

### 🔗 메뉴관계 탭

MenuItem 간 업무 흐름 관계를 수동으로 정의합니다.
등록된 관계는 챗봇 검색에 즉시 반영됩니다.

| 관계 유형 | 의미 |
|-----------|------|
| ↔ 관련메뉴 | 함께 참고할 메뉴 |
| → 선행업무 | 이 메뉴 이전에 처리해야 할 메뉴 |
| ← 후속업무 | 이 메뉴 이후에 이어지는 메뉴 |

사용법: 좌측(소스 메뉴) 클릭 → 가운데에서 관계 유형 선택 → 우측(대상 메뉴) 클릭 → **➕ 관계 추가**

### 📝 메뉴설명 탭

`update_menu.py`로 추가된 신규 메뉴의 description을 입력하고 임베딩을 생성합니다.

| 버튼 | 동작 |
|------|------|
| 💾 저장 | 설명 텍스트만 저장 |
| ✨ 저장 + 임베딩 생성 | 설명 저장 + Gemini 임베딩 생성 (챗봇 검색 활성화) |

---

## 스크립트 목록

| 파일 | 역할 |
|------|------|
| `init_db.py` | 초기 메뉴 데이터 구축 (엑셀 → Neo4j) |
| `update_menu.py` | 메뉴 목록 갱신 (CSV → Neo4j, 증분 업데이트) |
| `add_qa.py` | QA 파일 임포트 (txt → Neo4j, `--dir` 지원) |
| `update_qa_tags.py` | QA 태그 정규화 (Gemini LLM, `--dir` 지원) |
| `link_qa_tag.py` | QA ↔ MenuItem 태그 기반 연결 |
| `link_qa_menu_embed.py` | QA ↔ MenuItem 임베딩 유사도 연결 |
| `link_qa_menu.py` | QA ↔ MenuItem 텍스트 패턴 연결 (레거시) |
| `app.py` | FastAPI 서버 (챗봇 + 관리자 API) |
| `gemini_embedder.py` | Gemini 임베딩 유틸리티 |
