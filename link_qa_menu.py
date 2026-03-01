"""
QA → MenuItem 자동 연결 스크립트
- QA 답변 텍스트에서 [대분류 > 중분류 > 소분류] 패턴 추출
- MenuItem.menu_path와 매칭하여 (QA)-[:IN_MENU]->(MenuItem) 엣지 생성
- 1:N 지원 (QA 하나가 여러 MenuItem에 연결 가능)

실행:
    .venv\\Scripts\\python.exe link_qa_menu.py --dry-run   # 미리 보기
    .venv\\Scripts\\python.exe link_qa_menu.py             # 실제 적용
"""

import os
import re
import argparse
from collections import Counter
from dotenv import load_dotenv
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# [A > B > C] 또는 [A > B] 형식 (대괄호 안, > 포함)
MENU_PATH_PATTERN = re.compile(
    r'\[([가-힣A-Za-z0-9()\s/]+(?:\s*>\s*[가-힣A-Za-z0-9()\s/]+)+)\]'
)


def normalize_path(path: str) -> str:
    """경로 정규화: > 앞뒤 공백 제거"""
    return re.sub(r'\s*>\s*', ' > ', path.strip())


def extract_menu_paths(answer: str) -> list:
    """답변 텍스트에서 메뉴 경로 목록 추출"""
    matches = MENU_PATH_PATTERN.findall(answer)
    return [normalize_path(m) for m in matches]


def fetch_menu_paths(session):
    """MenuItem menu_path → id 맵 생성"""
    result = session.run("""
        MATCH (m:MenuItem)
        WHERE m.menu_path IS NOT NULL
        RETURN m.id AS id, m.menu_path AS path, m.name AS name
    """)
    path_map = {}
    for r in result:
        norm = normalize_path(r["path"])
        path_map[norm] = {"id": r["id"], "name": r["name"], "path": r["path"]}
    return path_map


def fetch_all_qa(session):
    """모든 QA 조회"""
    result = session.run("""
        MATCH (q:QA)
        RETURN q.id AS id, q.answer AS answer
        ORDER BY q.id
    """)
    return [(r["id"], r["answer"] or "") for r in result]


def clear_existing_links(session):
    result = session.run("""
        MATCH ()-[r:IN_MENU]->()
        DELETE r
        RETURN count(r) AS c
    """)
    c = result.single()["c"]
    if c > 0:
        print(f"  기존 IN_MENU 엣지 {c}개 삭제")
    else:
        print("  기존 엣지 없음")


def create_links_batch(session, links):
    """배치로 IN_MENU 엣지 생성"""
    for qa_id, menu_item_id in links:
        session.run("""
            MATCH (q:QA {id: $qa_id})
            MATCH (m:MenuItem {id: $menu_id})
            MERGE (q)-[:IN_MENU]->(m)
        """, qa_id=qa_id, menu_id=menu_item_id)


def verify(session, total_qa):
    result_total = session.run(
        "MATCH ()-[r:IN_MENU]->() RETURN count(r) AS c"
    ).single()["c"]
    result_qa = session.run(
        "MATCH (q:QA)-[:IN_MENU]->() RETURN count(DISTINCT q) AS c"
    ).single()["c"]
    result_menu = session.run(
        "MATCH ()-[:IN_MENU]->(m:MenuItem) RETURN count(DISTINCT m) AS c"
    ).single()["c"]

    print(f"\n  총 IN_MENU 엣지  : {result_total:,}개")
    print(f"  연결된 QA        : {result_qa:,}개 / {total_qa:,}개 ({result_qa/total_qa*100:.1f}%)")
    print(f"  연결된 MenuItem  : {result_menu}개")

    top = session.run("""
        MATCH ()-[:IN_MENU]->(m:MenuItem)
        RETURN m.menu_path AS path, count(*) AS cnt
        ORDER BY cnt DESC LIMIT 10
    """)
    print(f"\n📋 MenuItem별 연결 QA 수 TOP 10:\n")
    print(f"  {'메뉴 경로':<40} {'QA수':>5}")
    print(f"  {'-'*40} {'-'*5}")
    for r in top:
        print(f"  {r['path']:<40} {r['cnt']:>5}")


def main():
    parser = argparse.ArgumentParser(description="QA → MenuItem 자동 연결")
    parser.add_argument("--dry-run", action="store_true", help="DB 수정 없이 미리 보기")
    args = parser.parse_args()

    print("=" * 60)
    print("  QA → MenuItem 자동 연결 (답변 텍스트 파싱)")
    print("=" * 60)

    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        return

    with driver.session() as session:
        print("📥 데이터 로드 중...")
        all_qa = fetch_all_qa(session)
        path_map = fetch_menu_paths(session)
        print(f"  QA {len(all_qa):,}개 / MenuItem 경로 {len(path_map)}개 로드\n")

        # 패턴 추출 및 매칭
        links = []           # (qa_id, menu_item_id)
        unmatched = []       # 매칭 안 된 경로

        for qa_id, answer in all_qa:
            paths = extract_menu_paths(answer)
            for path in paths:
                if path in path_map:
                    links.append((qa_id, path_map[path]["id"]))
                else:
                    unmatched.append(path)

        matched_qa = len(set(qa_id for qa_id, _ in links))

        print(f"📊 파싱 결과:")
        print(f"  메뉴 경로 추출된 QA  : {matched_qa:,}개 / {len(all_qa):,}개 ({matched_qa/len(all_qa)*100:.1f}%)")
        print(f"  생성할 IN_MENU 엣지  : {len(links):,}개")
        print(f"  미매칭 경로          : {len(unmatched)}개")

        if unmatched:
            top_unmatched = Counter(unmatched).most_common(15)
            print(f"\n  미매칭 경로 TOP 15 (MenuItem.menu_path에 없는 경로):")
            for path, cnt in top_unmatched:
                print(f"    [{path}]  {cnt}회")

        if args.dry_run:
            print(f"\n[DRY-RUN] 실제 적용: python link_qa_menu.py (--dry-run 없이)")
            driver.close()
            return

        # 실제 적용
        print(f"\n🔗 IN_MENU 엣지 생성 중...")
        clear_existing_links(session)
        create_links_batch(session, links)
        print(f"✅ {len(links):,}개 IN_MENU 엣지 생성 완료\n")

        print("📊 결과 검증:")
        verify(session, len(all_qa))

    driver.close()
    print("\n🎉 완료!")


if __name__ == "__main__":
    main()
