"""
QA → MenuItem 태그 기반 IN_MENU 엣지 생성 스크립트
- 정규화된 QA.tags와 MenuItem.name (공백 제거) 비교로 매칭
- MERGE로 생성하여 기존 임베딩 기반 매핑과 중복 없이 병행 가능
- 소스별 커버리지 통계 출력

실행:
    .venv\\Scripts\\python.exe link_qa_tag.py --dry-run
    .venv\\Scripts\\python.exe link_qa_tag.py
    .venv\\Scripts\\python.exe link_qa_tag.py --clear-existing
"""

import os
import argparse
from collections import defaultdict

from dotenv import load_dotenv
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# 소스별 강제 매핑: 태그 매칭과 무관하게 해당 소스의 모든 QA를 지정 MenuItem에 연결
# - 소스 자체가 특정 도메인에 귀속되는 경우 사용
# - 태그 기반 매핑과 MERGE로 병합되므로 중복 없음
SOURCE_FORCED_MENUS: dict[str, list[str]] = {
    "투표qna": ["전자투표"],   # 876개 전부 입주자 > 입주처리 > 전자투표
}

# MenuItem 이름 매칭 실패 시 sub_menu 전체로 fallback
# {source: (main_menu, sub_menu)}
SOURCE_FORCED_SUBMENU_FALLBACK: dict[str, tuple[str, str]] = {
    "투표qna": ("입주자", "입주처리"),  # 전자투표가 DB에 없으므로 입주처리 하위 전체
}


# ── Neo4j 데이터 로드 ─────────────────────────────────────────────────────────

def fetch_menu_items(session) -> list[dict]:
    """모든 MenuItem 로드 (id, name, path, main_menu)"""
    result = session.run("""
        MATCH (m:MenuItem)
        RETURN m.id AS id, m.name AS name,
               m.menu_path AS path, m.main_menu AS main
        ORDER BY m.id
    """)
    return [
        {
            "id":   r["id"],
            "name": r["name"] or "",
            "path": r["path"] or "",
            "main": r["main"] or "",
        }
        for r in result
    ]


def fetch_all_qa(session) -> list[dict]:
    """모든 QA 노드 로드 (id, tags, source)"""
    result = session.run("""
        MATCH (q:QA)
        RETURN q.id AS id, q.tags AS tags, q.source AS source
        ORDER BY q.id
    """)
    return [
        {
            "id":     r["id"],
            "tags":   r["tags"] or [],
            "source": r["source"] or "",
        }
        for r in result
    ]


# ── 태그 매칭 ─────────────────────────────────────────────────────────────────

def build_tag_to_menu(menu_items: list[dict]) -> dict[str, list[dict]]:
    """
    정규화된 태그 문자열 → MenuItem 목록 맵 구성.
    정규화: 공백 제거 + 소문자화
    """
    tag_map: dict[str, list[dict]] = {}
    for m in menu_items:
        normalized = m["name"].replace(" ", "").lower()
        if normalized not in tag_map:
            tag_map[normalized] = []
        tag_map[normalized].append(m)
    return tag_map


def find_menu_matches(qa_tags: list[str], tag_to_menu: dict) -> list[dict]:
    """QA 태그 목록에서 매칭되는 MenuItem 목록 반환 (중복 제거)"""
    matched: list[dict] = []
    seen_ids: set[str] = set()
    for tag in qa_tags:
        norm = tag.replace(" ", "").lower()
        for menu in tag_to_menu.get(norm, []):
            if menu["id"] not in seen_ids:
                matched.append(menu)
                seen_ids.add(menu["id"])
    return matched


def apply_source_forced_links(session, dry_run: bool) -> dict[str, int]:
    """
    SOURCE_FORCED_MENUS 기반 강제 매핑을 Cypher로 직접 실행.
    - 1차: MenuItem 이름 매칭 (공백 제거 비교)
    - 2차: menu_path CONTAINS 키워드
    - 3차: SOURCE_FORCED_SUBMENU_FALLBACK의 (main_menu, sub_menu) 기반
    - dry_run=True 이면 MERGE 없이 예정 수만 반환
    Returns: {source: 적용된 QA 수}
    """
    result_counts: dict[str, int] = {}
    for source, menu_names in SOURCE_FORCED_MENUS.items():
        for menu_name in menu_names:
            # 1차: 공백 제거 이름 매칭
            check = session.run(
                """
                MATCH (m:MenuItem)
                WHERE replace(m.name, ' ', '') = replace($menu_name, ' ', '')
                RETURN m.id AS id, m.name AS name, m.menu_path AS path
                """,
                menu_name=menu_name,
            ).data()

            if not check:
                # 2차: menu_path CONTAINS 매칭
                check = session.run(
                    """
                    MATCH (m:MenuItem)
                    WHERE m.menu_path CONTAINS $keyword
                    RETURN m.id AS id, m.name AS name, m.menu_path AS path
                    LIMIT 5
                    """,
                    keyword=menu_name,
                ).data()

            if not check:
                # 3차: SOURCE_FORCED_SUBMENU_FALLBACK — (main_menu, sub_menu) 전체 매핑
                fallback = SOURCE_FORCED_SUBMENU_FALLBACK.get(source)
                if fallback:
                    main_menu, sub_menu = fallback
                    check = session.run(
                        """
                        MATCH (m:MenuItem)
                        WHERE m.main_menu = $main_menu AND m.sub_menu = $sub_menu
                        RETURN m.id AS id, m.name AS name, m.menu_path AS path
                        """,
                        main_menu=main_menu,
                        sub_menu=sub_menu,
                    ).data()
                    if check:
                        print(
                            f"  '{menu_name}' → MenuItem 없음, "
                            f"sub_menu fallback 사용: {main_menu} > {sub_menu} "
                            f"({len(check)}개 MenuItem)"
                        )

            if not check:
                print(f"  ⚠️  '{menu_name}' MenuItem 없음 (강제 매핑 스킵)")
                continue

            menu_ids = [r["id"] for r in check]
            if not SOURCE_FORCED_SUBMENU_FALLBACK.get(source):
                # 이름/경로 매칭 성공 시에만 경로 출력 (fallback은 위에서 이미 출력)
                print(f"  '{menu_name}' → 매칭된 MenuItem: {[r['path'] for r in check]}")

            if dry_run:
                count = session.run(
                    "MATCH (q:QA {source: $src}) RETURN count(q) AS c",
                    src=source,
                ).single()["c"]
                result_counts[source] = result_counts.get(source, 0) + count * len(menu_ids)
            else:
                merged = session.run(
                    """
                    MATCH (q:QA {source: $src})
                    MATCH (m:MenuItem) WHERE m.id IN $menu_ids
                    MERGE (q)-[:IN_MENU]->(m)
                    WITH q RETURN count(DISTINCT q) AS c
                    """,
                    src=source,
                    menu_ids=menu_ids,
                ).single()["c"]
                result_counts[source] = result_counts.get(source, 0) + merged

    return result_counts


# ── Neo4j 엣지 조작 ───────────────────────────────────────────────────────────

def clear_existing_links(session) -> int:
    """기존 IN_MENU 엣지 전체 삭제. 삭제 수 반환."""
    result = session.run("""
        MATCH ()-[r:IN_MENU]->()
        DELETE r
        RETURN count(r) AS c
    """)
    return result.single()["c"]


def create_links(session, links: list[tuple[str, str]]) -> None:
    """(qa_id, menu_id) 쌍 목록으로 IN_MENU 엣지 MERGE"""
    for qa_id, menu_id in links:
        session.run(
            """
            MATCH (q:QA {id: $qa_id})
            MATCH (m:MenuItem {id: $menu_id})
            MERGE (q)-[:IN_MENU]->(m)
            """,
            qa_id=qa_id,
            menu_id=menu_id,
        )


# ── 검증 출력 ─────────────────────────────────────────────────────────────────

def verify(session, total_qa: int) -> None:
    """최종 IN_MENU 결과 검증 및 통계 출력"""
    total_edges = session.run(
        "MATCH ()-[r:IN_MENU]->() RETURN count(r) AS c"
    ).single()["c"]
    linked_qa = session.run(
        "MATCH (q:QA)-[:IN_MENU]->() RETURN count(DISTINCT q) AS c"
    ).single()["c"]
    linked_menu = session.run(
        "MATCH ()-[:IN_MENU]->(m:MenuItem) RETURN count(DISTINCT m) AS c"
    ).single()["c"]

    print(f"\n  총 IN_MENU 엣지  : {total_edges:,}개")
    pct = linked_qa / total_qa * 100 if total_qa > 0 else 0
    print(f"  연결된 QA        : {linked_qa:,}개 / {total_qa:,}개 ({pct:.1f}%)")
    print(f"  연결된 MenuItem  : {linked_menu}개")

    # 소스별 커버리지
    rows = session.run("""
        MATCH (q:QA)
        OPTIONAL MATCH (q)-[:IN_MENU]->(m:MenuItem)
        RETURN q.source AS src,
               count(DISTINCT q) AS total,
               count(DISTINCT CASE WHEN m IS NOT NULL THEN q END) AS linked
        ORDER BY src
    """)
    print(f"\n  소스별 커버리지:")
    print(f"  {'소스':<20} {'전체':>6} {'연결':>6} {'커버리지':>8}")
    print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*8}")
    for r in rows:
        p = r["linked"] / r["total"] * 100 if r["total"] > 0 else 0
        print(f"  {(r['src'] or ''):<20} {r['total']:>6} {r['linked']:>6} {p:>7.1f}%")

    # MenuItem별 TOP 10
    top = session.run("""
        MATCH ()-[:IN_MENU]->(m:MenuItem)
        RETURN m.menu_path AS path, count(*) AS cnt
        ORDER BY cnt DESC LIMIT 10
    """)
    print(f"\n  MenuItem별 연결 QA 수 TOP 10:")
    print(f"  {'메뉴 경로':<50} {'QA수':>5}")
    print(f"  {'-'*50} {'-'*5}")
    for r in top:
        print(f"  {(r['path'] or ''):<50} {r['cnt']:>5}")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QA → MenuItem 태그 기반 IN_MENU 엣지 생성"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="DB 수정 없이 매칭 결과만 출력"
    )
    parser.add_argument(
        "--clear-existing", action="store_true",
        help="기존 IN_MENU 엣지 전체 삭제 후 재생성"
    )
    args = parser.parse_args()

    print("=" * 65)
    print("  QA → MenuItem 태그 기반 IN_MENU 매핑")
    print(f"  dry-run: {args.dry_run}  /  clear-existing: {args.clear_existing}")
    print("=" * 65)

    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        return

    with driver.session() as session:

        # ── 1. 데이터 로드 ──────────────────────────────────────────────────────
        print("📥 데이터 로드 중...")
        menu_items = fetch_menu_items(session)
        all_qa     = fetch_all_qa(session)
        total_qa   = len(all_qa)
        print(f"  MenuItem: {len(menu_items)}개  /  QA: {total_qa}개\n")

        if not menu_items:
            print("❌ MenuItem 없음. init_db.py 먼저 실행하세요.")
            driver.close()
            return
        if not all_qa:
            print("❌ QA 없음. add_qa.py 먼저 실행하세요.")
            driver.close()
            return

        # ── 2. 태그 → MenuItem 맵 구성 ──────────────────────────────────────────
        tag_to_menu = build_tag_to_menu(menu_items)
        print(f"📌 MenuItem 정규화 맵: {len(tag_to_menu)}개 고유 태그\n")

        # ── 3. QA ↔ MenuItem 태그 매칭 ─────────────────────────────────────────────
        links: list[tuple[str, str]] = []
        matched_qa_count = 0
        source_stats: dict = defaultdict(lambda: {"total": 0, "matched": 0, "links": 0})

        for qa in all_qa:
            src = qa["source"]
            source_stats[src]["total"] += 1
            matches = find_menu_matches(qa["tags"], tag_to_menu)
            if matches:
                matched_qa_count += 1
                source_stats[src]["matched"] += 1
                for m in matches:
                    links.append((qa["id"], m["id"]))
                    source_stats[src]["links"] += 1

        pct = matched_qa_count / total_qa * 100 if total_qa > 0 else 0
        print(f"📊 태그 매칭 결과:")
        print(f"  생성할 IN_MENU 엣지  : {len(links):,}개")
        print(f"  연결 QA              : {matched_qa_count:,}개 / {total_qa:,}개 ({pct:.1f}%)\n")

        # ── 4. 소스별 강제 매핑 (Cypher 직접 실행) ─────────────────────────────────
        if SOURCE_FORCED_MENUS:
            print(f"🔗 소스 강제 매핑:")
            forced_counts = apply_source_forced_links(session, dry_run=args.dry_run)
            for src, cnt in sorted(forced_counts.items()):
                print(f"  {src}: 약 {cnt:,}개 엣지 추가 예정" if args.dry_run else f"  {src}: {cnt:,}개 QA 연결 완료")
            print()

        print(f"  소스별 매칭 현황 (태그 기반):")
        print(f"  {'소스':<20} {'전체':>6} {'매칭':>6} {'엣지':>6} {'커버리지':>8}")
        print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*6} {'-'*8}")
        for src, s in sorted(source_stats.items()):
            p = s["matched"] / s["total"] * 100 if s["total"] > 0 else 0
            forced_marker = " (+강제)" if src in SOURCE_FORCED_MENUS else ""
            print(f"  {src:<20} {s['total']:>6} {s['matched']:>6} {s['links']:>6} {p:>7.1f}%{forced_marker}")

        # 샘플 출력 (처음 10개)
        if links:
            qa_by_id   = {q["id"]: q for q in all_qa}
            menu_by_id = {m["id"]: m for m in menu_items}
            print(f"\n  매칭 샘플 (처음 10개):")
            for qa_id, menu_id in links[:10]:
                q = qa_by_id.get(qa_id, {})
                m = menu_by_id.get(menu_id, {})
                tags_str = ", ".join(q.get("tags", []))[:40]
                print(f"    {qa_id} [{tags_str}] → {m.get('path', '')}")

        if args.dry_run:
            print(f"\n[DRY-RUN] 실제 적용:")
            print(f"  .venv\\Scripts\\python.exe link_qa_tag.py")
            driver.close()
            return

        if not links and not SOURCE_FORCED_MENUS:
            print("\n⚠️  매칭 없음. update_qa_tags.py를 먼저 실행하세요.")
            driver.close()
            return

        # ── 5. 기존 엣지 삭제 (--clear-existing, 강제 매핑 이전에 실행) ───────────
        if args.clear_existing:
            print(f"\n🗑️  기존 IN_MENU 엣지 삭제 중...")
            deleted = clear_existing_links(session)
            print(f"  {deleted}개 삭제 완료")

        # ── 6. IN_MENU 엣지 생성 (태그 기반) ────────────────────────────────────
        print(f"\n🔗 IN_MENU 엣지 생성 중 ({len(links):,}개)...")
        chunk = 200
        for i in range(0, len(links), chunk):
            create_links(session, links[i:i + chunk])
            done = min(i + chunk, len(links))
            print(f"  진행: {done:,}/{len(links):,}", end="\r")
        print()
        print(f"✅ {len(links):,}개 IN_MENU 엣지 생성/업데이트 완료\n")

        # ── 6. 최종 검증 ─────────────────────────────────────────────────────────
        print("📊 최종 결과 검증:")
        verify(session, total_qa)

    driver.close()
    print("\n🎉 완료!")


if __name__ == "__main__":
    main()
