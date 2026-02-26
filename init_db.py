"""
Neo4j 데이터베이스 초기화 스크립트
- app.py 스키마 기반으로 샘플 뉴스 기사 데이터 생성
- Gemini text-embedding-004로 벡터 임베딩 생성
- content_vector_index 벡터 인덱스 생성

실행 방법:
    .venv\\Scripts\\activate
    python init_db.py
"""

import os
from dotenv import load_dotenv
import neo4j
from gemini_embedder import GeminiEmbedder

load_dotenv()

# ─── 설정 ───────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
INDEX_NAME = "content_vector_index"
EMBEDDING_DIM = 3072  # text-embedding-004 실제 차원 수

# ─── 샘플 뉴스 데이터 ───────────────────────────────────
# 카테고리: 정치, 경제, 사회, IT/과학, 스포츠
SAMPLE_ARTICLES = [
    {
        "article_id": "ART_001_0000000001",
        "title": "정부, 새로운 경제 활성화 정책 발표",
        "url": "https://example.com/news/0000000001",
        "published_date": "2025-01-15",
        "category": "경제",
        "chunks": [
            "정부는 15일 새로운 경제 활성화 패키지를 발표했다. 이번 정책은 중소기업 지원 강화와 수출 촉진을 핵심으로 하며, 총 5조원 규모의 재정이 투입될 예정이다.",
            "경제 활성화 패키지에는 금리 인하 지원, 수출 기업 세제 혜택, 고용 창출 인센티브 등이 포함된다. 전문가들은 이번 정책이 내수 진작에 도움이 될 것으로 기대하고 있다.",
        ],
    },
    {
        "article_id": "ART_001_0000000002",
        "title": "국회, 디지털 경제 관련 법안 통과",
        "url": "https://example.com/news/0000000002",
        "published_date": "2025-01-16",
        "category": "정치",
        "chunks": [
            "국회는 16일 본회의에서 디지털 경제 촉진법을 통과시켰다. 이 법안은 AI 기업 육성과 데이터 경제 활성화를 위한 규제 완화를 주요 내용으로 담고 있다.",
            "법안 통과로 국내 AI 스타트업들은 데이터 활용 범위가 크게 넓어질 전망이다. 야당은 개인정보 보호 우려를 제기하며 반대했으나 여당 주도로 가결됐다.",
        ],
    },
    {
        "article_id": "ART_001_0000000003",
        "title": "삼성전자, 차세대 AI 반도체 출시 예고",
        "url": "https://example.com/news/0000000003",
        "published_date": "2025-01-17",
        "category": "IT/과학",
        "chunks": [
            "삼성전자는 2분기 중 차세대 AI 가속 반도체 '엑시노스 AI'를 출시할 계획이라고 밝혔다. 해당 칩은 기존 대비 연산 성능이 3배 향상됐으며 전력 효율도 크게 개선됐다.",
            "삼성전자 측은 이 반도체가 온디바이스 AI 구현에 최적화돼 있으며, 스마트폰·노트북·서버 등 다양한 기기에 탑재될 예정이라고 설명했다.",
        ],
    },
    {
        "article_id": "ART_001_0000000004",
        "title": "한국 야구팀, 국제 대회 우승 쾌거",
        "url": "https://example.com/news/0000000004",
        "published_date": "2025-01-18",
        "category": "스포츠",
        "chunks": [
            "한국 야구 국가대표팀이 아시아 챔피언십에서 일본을 꺾고 우승을 차지했다. 최종전에서 5대 3으로 승리하며 3년 만에 아시아 정상에 올랐다.",
            "대회 MVP로 선정된 김철수 선수는 '팀원 모두가 최선을 다한 결과'라며 소감을 밝혔다. 국내 야구 팬들은 SNS에서 축하 메시지를 쏟아내고 있다.",
        ],
    },
    {
        "article_id": "ART_001_0000000005",
        "title": "서울시, 대중교통 요금 인상 검토",
        "url": "https://example.com/news/0000000005",
        "published_date": "2025-01-19",
        "category": "사회",
        "chunks": [
            "서울시가 버스·지하철 등 대중교통 요금 인상을 검토하고 있다고 밝혔다. 운영 적자 누적과 인건비 상승이 주요 원인으로 지목됐으며, 인상 폭은 100~200원 수준이 될 것으로 보인다.",
            "시민단체는 물가 상승 속에 대중교통 요금까지 오르면 서민 부담이 가중된다며 반대 입장을 표명했다. 서울시는 시민 의견 수렴 후 최종 결정할 예정이다.",
        ],
    },
    {
        "article_id": "ART_002_0000000006",
        "title": "코스피, 외국인 매수에 상승 마감",
        "url": "https://example.com/news/0000000006",
        "published_date": "2025-01-20",
        "category": "경제",
        "chunks": [
            "코스피가 외국인 투자자들의 연속 매수에 힘입어 2,650선을 회복했다. 반도체·자동차·바이오 업종이 강세를 이끌며 지수 상승을 견인했다.",
            "증권가는 미국 연준의 금리 인하 기대감과 원·달러 환율 안정이 외국인 매수세를 불러일으키고 있다고 분석했다.",
        ],
    },
    {
        "article_id": "ART_002_0000000007",
        "title": "네이버, 생성형 AI 검색 서비스 정식 출시",
        "url": "https://example.com/news/0000000007",
        "published_date": "2025-01-21",
        "category": "IT/과학",
        "chunks": [
            "네이버가 생성형 AI 기반 검색 서비스 'AI 검색'을 정식 출시했다. 사용자 질문에 문서를 참조한 요약 답변을 제공하며, 출처 링크도 함께 표시된다.",
            "네이버 측은 '하이퍼클로바X' 모델을 탑재해 한국어 특화 AI 검색을 구현했다고 밝혔다. 구글·마이크로소프트와의 AI 검색 경쟁이 본격화될 전망이다.",
        ],
    },
]


def create_embedder():
    """Gemini 네이티브 임베더 생성 (google-generativeai SDK 사용)"""
    print("📡 Gemini 임베더 초기화 중...")
    embedder = GeminiEmbedder(
        model="gemini-embedding-001",
        api_key=GOOGLE_API_KEY,
    )
    print("✅ 임베더 초기화 완료")
    return embedder


def clear_database(session):
    """기존 데이터 전체 삭제"""
    print("🗑️  기존 데이터 삭제 중...")
    session.run("MATCH (n) DETACH DELETE n")
    print("✅ 기존 데이터 삭제 완료")


def create_constraints(session):
    """유니크 제약 조건 생성"""
    print("📌 제약 조건 생성 중...")
    constraints = [
        "CREATE CONSTRAINT article_id IF NOT EXISTS FOR (a:Article) REQUIRE a.article_id IS UNIQUE",
        "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
        "CREATE CONSTRAINT content_id IF NOT EXISTS FOR (c:Content) REQUIRE c.content_id IS UNIQUE",
    ]
    for constraint in constraints:
        try:
            session.run(constraint)
        except Exception as e:
            print(f"  제약 조건 이미 존재 또는 오류: {e}")
    print("✅ 제약 조건 생성 완료")


def insert_data(session, embedder):
    """샘플 데이터 삽입 및 임베딩 생성"""
    total = len(SAMPLE_ARTICLES)
    for i, article in enumerate(SAMPLE_ARTICLES, 1):
        print(f"\n[{i}/{total}] 기사 처리 중: {article['title']}")

        # 1. Category 노드 생성 (없으면)
        session.run(
            "MERGE (c:Category {name: $name})",
            name=article["category"],
        )

        # 2. Article 노드 생성
        session.run(
            """
            MERGE (a:Article {article_id: $article_id})
            SET a.title = $title,
                a.url = $url,
                a.published_date = $published_date
            """,
            **{k: article[k] for k in ["article_id", "title", "url", "published_date"]},
        )

        # 3. Article → Category 관계
        session.run(
            """
            MATCH (a:Article {article_id: $article_id})
            MATCH (c:Category {name: $category})
            MERGE (a)-[:BELONGS_TO]->(c)
            """,
            article_id=article["article_id"],
            category=article["category"],
        )

        # 4. Content(Chunk) 노드 생성 + 임베딩
        for chunk_idx, chunk_text in enumerate(article["chunks"]):
            content_id = f"{article['article_id']}_chunk_{chunk_idx}"
            print(f"  🔢 임베딩 생성 중: chunk_{chunk_idx} ...", end=" ", flush=True)

            # Gemini 임베딩 생성
            embedding = embedder.embed_query(chunk_text)
            print(f"dim={len(embedding)} ✅")

            session.run(
                """
                MERGE (c:Content {content_id: $content_id})
                SET c.chunk = $chunk,
                    c.title = $title,
                    c.embedding = $embedding
                """,
                content_id=content_id,
                chunk=chunk_text,
                title=article["title"],
                embedding=embedding,
            )

            # Article → Content 관계
            session.run(
                """
                MATCH (a:Article {article_id: $article_id})
                MATCH (c:Content {content_id: $content_id})
                MERGE (a)-[:HAS_CHUNK]->(c)
                """,
                article_id=article["article_id"],
                content_id=content_id,
            )

    print(f"\n✅ 총 {total}개 기사 삽입 완료")


def create_vector_index(session):
    """벡터 인덱스 생성"""
    print(f"\n🔍 벡터 인덱스 '{INDEX_NAME}' 생성 중...")

    # 기존 인덱스 삭제 (있을 경우)
    try:
        session.run(f"DROP INDEX {INDEX_NAME} IF EXISTS")
    except Exception:
        pass

    # 인덱스 생성
    session.run(
        f"""
        CREATE VECTOR INDEX {INDEX_NAME} IF NOT EXISTS
        FOR (c:Content) ON c.embedding
        OPTIONS {{
            indexConfig: {{
                `vector.dimensions`: {EMBEDDING_DIM},
                `vector.similarity_function`: 'cosine'
            }}
        }}
        """
    )
    print(f"✅ 벡터 인덱스 '{INDEX_NAME}' 생성 완료")


def verify_data(session):
    """데이터 검증"""
    print("\n📊 데이터 검증:")
    counts = session.run("""
        MATCH (a:Article) WITH count(a) AS articles
        MATCH (c:Category) WITH articles, count(c) AS categories
        MATCH (co:Content) WITH articles, categories, count(co) AS contents
        RETURN articles, categories, contents
    """).single()

    if counts:
        print(f"  Article 노드: {counts['articles']}개")
        print(f"  Category 노드: {counts['categories']}개")
        print(f"  Content 노드: {counts['contents']}개")

    indexes = session.run("SHOW INDEXES WHERE name = $name", name=INDEX_NAME).data()
    if indexes:
        print(f"  벡터 인덱스: '{INDEX_NAME}' 존재 ✅")
    else:
        print(f"  벡터 인덱스: '{INDEX_NAME}' 없음 ❌")


def main():
    print("=" * 60)
    print("  GraphRAG Demo - Neo4j 초기화 스크립트")
    print("=" * 60)
    print(f"  Neo4j URI : {NEO4J_URI}")
    print(f"  Index     : {INDEX_NAME}")
    print(f"  Embedding : text-embedding-004 (Gemini)")
    print("=" * 60)

    # 임베더 초기화
    embedder = create_embedder()

    # Neo4j 연결
    print(f"\n🔌 Neo4j 연결 중: {NEO4J_URI}")
    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))

    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ Neo4j 연결 실패: {e}")
        return

    with driver.session() as session:
        clear_database(session)
        create_constraints(session)
        insert_data(session, embedder)
        create_vector_index(session)
        verify_data(session)

    driver.close()
    print("\n🎉 초기화 완료! 이제 서버를 실행하세요:")
    print("   .venv\\Scripts\\uvicorn app:app --host 0.0.0.0 --port 8800 --reload")


if __name__ == "__main__":
    main()
