"""
QA 태그 집계 스크립트
- Neo4j에서 모든 QA 노드의 tags 조회
- 태그 빈도 집계 및 그룹핑
"""

import os
from collections import Counter
from dotenv import load_dotenv
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")


def fetch_all_tags(session):
    result = session.run("""
        MATCH (q:QA)
        WHERE q.tags IS NOT NULL AND size(q.tags) > 0
        RETURN q.tags AS tags, q.source AS source
    """)
    return [(r["tags"], r["source"]) for r in result]


def analyze_tags(rows):
    tag_counter = Counter()
    source_counter = Counter()
    tag_by_source = {}  # source → {tag: count}

    for tags, source in rows:
        for tag in tags:
            tag = tag.strip()
            if not tag:
                continue
            tag_counter[tag] += 1
            source_counter[source] += 1

            if source not in tag_by_source:
                tag_by_source[source] = Counter()
            tag_by_source[source][tag] += 1

    return tag_counter, source_counter, tag_by_source


def print_report(tag_counter, source_counter, tag_by_source):
    total_qa = sum(source_counter.values())
    total_unique_tags = len(tag_counter)

    print("=" * 60)
    print("  XPERP QA 태그 집계 분석")
    print("=" * 60)
    print(f"  태그 있는 QA 수    : {total_qa:,}개")
    print(f"  고유 태그 수       : {total_unique_tags:,}개")
    print("=" * 60)

    # 전체 태그 빈도 TOP 50
    print("\n📊 전체 태그 빈도 TOP 50\n")
    print(f"  {'태그':<20} {'빈도':>6}  {'비율':>6}")
    print(f"  {'-'*20} {'-'*6}  {'-'*6}")
    for tag, cnt in tag_counter.most_common(50):
        pct = cnt / total_qa * 100
        print(f"  {tag:<20} {cnt:>6}  {pct:>5.1f}%")

    # 파일(소스)별 태그 수
    print(f"\n\n📁 소스 파일별 QA 수 및 태그 현황\n")
    print(f"  {'파일명':<30} {'QA수':>6}  {'고유태그':>8}")
    print(f"  {'-'*30} {'-'*6}  {'-'*8}")
    for src, cnt in sorted(source_counter.items()):
        unique = len(tag_by_source.get(src, {}))
        print(f"  {src:<30} {cnt:>6}  {unique:>8}")

    # 소스별 TOP 5 태그
    print(f"\n\n🔍 소스 파일별 TOP 5 태그\n")
    for src in sorted(tag_by_source.keys()):
        top5 = tag_by_source[src].most_common(5)
        tags_str = ", ".join([f"{t}({c})" for t, c in top5])
        print(f"  [{src}]")
        print(f"    {tags_str}")

    # 1회만 등장하는 희귀 태그
    rare = [t for t, c in tag_counter.items() if c == 1]
    print(f"\n\n💡 1회만 등장한 태그: {len(rare)}개")
    if rare:
        print(f"  {', '.join(sorted(rare)[:30])}" + (" ..." if len(rare) > 30 else ""))

    # 핵심 업무 개념 후보 (5회 이상)
    core = [(t, c) for t, c in tag_counter.most_common() if c >= 5]
    print(f"\n\n✅ 핵심 업무 개념 후보 (5회 이상 등장): {len(core)}개")
    print(f"  {', '.join([t for t, _ in core])}")


def main():
    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
    except Exception as e:
        print(f"❌ Neo4j 연결 실패: {e}")
        return

    with driver.session() as session:
        rows = fetch_all_tags(session)

    driver.close()

    if not rows:
        print("❌ 태그가 있는 QA 데이터가 없습니다.")
        return

    tag_counter, source_counter, tag_by_source = analyze_tags(rows)
    print_report(tag_counter, source_counter, tag_by_source)


if __name__ == "__main__":
    main()
