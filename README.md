# GraphRAG 실시간 시각화 데모

```
┌─────────────────┐
│   Frontend      │
│   (HTML + JS)   │  ← vis-network.js를 사용한 그래프 시각화
└────────┬────────┘
         │
    HTTP API
         │
┌────────▼────────┐
│   FastAPI       │
│   Backend       │
└────────┬────────┘
         │
┌────────▼────────┐
│   GraphRAG      │  ← ToolsRetriever (Vector + VectorCypher + Text2Cypher)
└────────┬────────┘
         │
┌────────▼────────┐
│   Neo4j DB      │  ← Knowledge Graph 저장소
└─────────────────┘
```

## 설치 및 실행

### 기본 사항
1. Docker Desktop이 실행 중이어야 함
2. Neo4j Desktop이 실행 중이어야 함

```
docker run -d `
  --name neo4j `
  -p 7474:7474 -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/password `
  neo4j:latest

```


### 0. Neo4j Database
   - Neo4j 실행 중이어야 함 (기본: `neo4j://localhost:7687`)
   - `init_db.py` 스크립트를 통해 샘플 데이터 구축 및 `content_vector_index` 생성 필요


# init_db.py 실행

```
.venv\Scripts\python init_db.py
```

### 1. 환경 설정

```bash
uv venv
uv pip install -r requirements.txt
# Gemini 임베딩을 위한 최신 구글 SDK 설치
uv pip install google-genai

cp .env.example .env
# .env 파일에 GOOGLE_API_KEY 추가 등 설정
```

### 2. 샘플 데이터 및 벡터 인덱스 초기화

API 키 설정과 DB 구동이 확인되었으면, 데모로 사용할 샘플 뉴스와 그래프를 삽입합니다.

```bash
.venv\Scripts\python init_db.py
```

### 3. 서버 실행

```bash
.venv\Scripts\uvicorn app:app --host 0.0.0.0 --port 8800 --reload
```

### 4. 브라우저에서 접속

```
http://localhost:8800
```
(또는 `ipconfig`로 확인한 로컬망 IP 주소 예: `http://192.168.38.92:8800` 로도 외부 네트워크 접근이 가능합니다.)

## 사용 방법

1. **그래프 로딩**: 페이지가 로드되면 자동으로 전체 Knowledge Graph가 표시됩니다

2. **질문 입력**: 좌측 패널에서 질문을 입력하거나 예시 질문을 클릭합니다

3. **검색 실행**: "🚀 검색하기" 버튼을 클릭합니다

4. **결과 확인**:
   - 좌측: LLM이 생성한 답변과 메타데이터
   - 우측: 해당 질문에 사용된 노드들이 빨간색으로 하이라이트

### 데모 실행 예시

![alt text](image.png)
