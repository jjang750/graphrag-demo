"""
MenuItem ↔ Concept 직접 연결 스크립트
- MenuItem.description 텍스트에 Concept.name이 포함되면 RELATED_CONCEPT 엣지 생성
- 예: MenuItem "전기검침" 설명에 "검침값" 이라는 단어 → RELATED_CONCEPT 엣지

실행:
    .venv\\Scripts\\python.exe link_concepts.py
"""

import os
from dotenv import load_dotenv
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")


def clear_existing_links(session):
    """기존 RELATED_CONCEPT 엣지 삭제"""
    result = session.run("""
        MATCH ()-[r:RELATED_CONCEPT]->()
        DELETE r
        RETURN count(r) AS c
    """)
    c = result.single()["c"]
    if c > 0:
        print(f"  기존 RELATED_CONCEPT 엣지 {c}개 삭제")
    else:
        print("  삭제할 기존 엣지 없음")


def create_links(session):
    """
    MenuItem.description에 Concept.name이 포함되면 RELATED_CONCEPT 엣지 생성.
    Concept 이름이 2자 이하인 경우 오매칭 가능성이 높으므로 3자 이상만 매칭.
    """
    result = session.run("""
        MATCH (m:MenuItem), (c:Concept)
        WHERE m.description IS NOT NULL
          AND size(c.name) >= 3
          AND m.description CONTAINS c.name
        MERGE (m)-[r:RELATED_CONCEPT]->(c)
        ON CREATE SET r.weight = 1
        RETURN count(r) AS created
    """)
    return result.single()["created"]


def verify(session):
    """검증 리포트"""
    total = session.run(
        "MATCH ()-[r:RELATED_CONCEPT]->() RETURN count(r) AS c"
    ).single()["c"]

    items_count = session.run("""
        MATCH (m:MenuItem)-[:RELATED_CONCEPT]->()
        RETURN count(DISTINCT m) AS c
    """).single()["c"]

    concepts_count = session.run("""
        MATCH ()-[:RELATED_CONCEPT]->(c:Concept)
        RETURN count(DISTINCT c) AS c
    """).single()["c"]

    total_items = session.run("MATCH (m:MenuItem) RETURN count(m) AS c").single()["c"]
    total_concepts = session.run("MATCH (c:Concept) RETURN count(c) AS c").single()["c"]

    print(f"\n  총 RELATED_CONCEPT 엣지  : {total:,}개")
    print(f"  연결된 MenuItem          : {items_count}개 / {total_items}개 ({items_count/total_items*100:.0f}%)")
    print(f"  연결된 Concept           : {concepts_count}개 / {total_concepts}개 ({concepts_count/total_concepts*100:.0f}%)")

    # MenuItem별 연결 Concept 수 TOP 10
    top_items = session.run("""
        MATCH (m:MenuItem)-[:RELATED_CONCEPT]->(c:Concept)
        RETURN m.name AS menu, m.main_menu AS main, count(c) AS cnt
        ORDER BY cnt DESC
        LIMIT 10
    """)
    print(f"\n📋 MenuItem별 연결 Concept 수 TOP 10:\n")
    print(f"  {'대메뉴':<12} {'메뉴항목':<25} {'Concept수':>6}")
    print(f"  {'-'*12} {'-'*25} {'-'*6}")
    for r in top_items:
        print(f"  {(r['main'] or ''):<12} {r['menu']:<25} {r['cnt']:>6}")

    # 도메인별 연결 분포
    domain_dist = session.run("""
        MATCH ()-[:RELATED_CONCEPT]->(c:Concept)
        RETURN c.domain AS domain, count(*) AS cnt
        ORDER BY cnt DESC
    """)
    print(f"\n📊 도메인별 RELATED_CONCEPT 분포:\n")
    print(f"  {'도메인':<20} {'엣지수':>6}")
    print(f"  {'-'*20} {'-'*6}")
    for r in domain_dist:
        print(f"  {r['domain']:<20} {r['cnt']:>6}")


def main():
    print("=" * 60)
    print("  MenuItem ↔ Concept 직접 연결 생성")
    print("=" * 60)
    print(f"  Neo4j URI : {NEO4J_URI}\n")

    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ Neo4j 연결 실패: {e}")
        return

    with driver.session() as session:
        print("🗑️  기존 RELATED_CONCEPT 엣지 정리 중...")
        clear_existing_links(session)

        print("\n🔗 MenuItem ↔ Concept 연결 생성 중...")
        print("   (MenuItem.description에 Concept.name 포함 여부 매칭)")
        created = create_links(session)
        print(f"   ✅ {created:,}개 RELATED_CONCEPT 엣지 생성 완료\n")

        print("📊 결과 검증:")
        verify(session)

    driver.close()
    print("\n🎉 완료! 서버를 재시작하면 그래프에서 엣지가 표시됩니다.")


if __name__ == "__main__":
    main()
