"""
XPERP 메뉴 업데이트 스크립트
- docs/xperp_menu_list_all.csv 를 읽어 Neo4j 메뉴 노드를 갱신
- 신규 메뉴 추가 (description/embedding 은 빈 값으로 초기화)
- 기존 메뉴 경로 정보 갱신 (description·embedding 은 보존)
- 삭제된 메뉴 보고 (--delete 옵션으로 실제 삭제)

실행 방법:
    .venv\\Scripts\\python.exe update_menu.py
    .venv\\Scripts\\python.exe update_menu.py --delete   # 제거된 메뉴 삭제까지
"""

import csv
import sys

import neo4j

from config import NEO4J_URI, NEO4J_AUTH, MENU_CSV_PATH as CSV_PATH


# ─────────────────────────────────────────
# CSV 로드
# ─────────────────────────────────────────

def load_csv(filepath: str) -> list[dict]:
    items = []
    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            main = (row.get("대분류") or "").strip()
            sub  = (row.get("중분류") or "").strip()
            item = (row.get("소분류") or "").strip()
            if not main or not sub:
                continue
            items.append({
                "main_menu": main,
                "sub_menu":  sub,
                "menu_item": item if item else None,
            })
    return items


def build_id_and_path(record: dict) -> tuple[str, str]:
    main = record["main_menu"]
    sub  = record["sub_menu"]
    item = record["menu_item"]
    if item:
        return f"{main}::{sub}::{item}", f"{main} > {sub} > {item}"
    else:
        return f"{main}::{sub}", f"{main} > {sub}"


# ─────────────────────────────────────────
# Neo4j 헬퍼
# ─────────────────────────────────────────

def get_existing_ids(session) -> set[str]:
    result = session.run("MATCH (i:MenuItem) RETURN i.id AS id")
    return {r["id"] for r in result}


def get_qa_linked_ids(session) -> set[str]:
    """IN_MENU 에지가 하나라도 있는 MenuItem id 집합"""
    result = session.run(
        "MATCH (q:QA)-[:IN_MENU]->(m:MenuItem) RETURN DISTINCT m.id AS id"
    )
    return {r["id"] for r in result}


def ensure_menu_node(session, main: str):
    session.run("MERGE (m:Menu {name: $name})", name=main)


def ensure_submenu_node(session, main: str, sub: str):
    sub_id = f"{main}::{sub}"
    session.run(
        "MERGE (s:SubMenu {id: $id}) SET s.name = $name, s.main_menu = $main",
        id=sub_id, name=sub, main=main,
    )
    session.run(
        """
        MATCH (m:Menu {name: $main})
        MATCH (s:SubMenu {id: $sub_id})
        MERGE (m)-[:HAS_SUBMENU]->(s)
        """,
        main=main, sub_id=sub_id,
    )


def upsert_menuitem(session, record: dict, item_id: str, menu_path: str, is_new: bool):
    main = record["main_menu"]
    sub  = record["sub_menu"]
    item = record["menu_item"]
    name = item if item else sub

    if is_new:
        # 신규: 빈 description으로 생성 (embedding 없음)
        session.run(
            """
            MERGE (i:MenuItem {id: $id})
            ON CREATE SET
                i.name        = $name,
                i.main_menu   = $main,
                i.sub_menu    = $sub,
                i.menu_path   = $menu_path,
                i.description = ''
            """,
            id=item_id, name=name, main=main, sub=sub, menu_path=menu_path,
        )
        # 엣지 연결
        if item:
            sub_id = f"{main}::{sub}"
            session.run(
                """
                MATCH (s:SubMenu {id: $sub_id})
                MATCH (i:MenuItem {id: $item_id})
                MERGE (s)-[:HAS_ITEM]->(i)
                """,
                sub_id=sub_id, item_id=item_id,
            )
        else:
            session.run(
                """
                MATCH (m:Menu {name: $main})
                MATCH (i:MenuItem {id: $item_id})
                MERGE (m)-[:HAS_ITEM]->(i)
                """,
                main=main, item_id=item_id,
            )
    else:
        # 기존: 경로/이름만 갱신, description·embedding 보존
        session.run(
            """
            MATCH (i:MenuItem {id: $id})
            SET i.name      = $name,
                i.main_menu = $main,
                i.sub_menu  = $sub,
                i.menu_path = $menu_path
            """,
            id=item_id, name=name, main=main, sub=sub, menu_path=menu_path,
        )


def delete_menuitem(session, item_id: str):
    session.run(
        "MATCH (i:MenuItem {id: $id}) DETACH DELETE i",
        id=item_id,
    )


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────

def main():
    do_delete = "--delete" in sys.argv

    print("=" * 60)
    print("  XPERP 메뉴 업데이트")
    print("=" * 60)
    print(f"  CSV   : {CSV_PATH}")
    print(f"  Neo4j : {NEO4J_URI}")
    print(f"  삭제  : {'켜짐 (--delete)' if do_delete else '꺼짐 (보고만 함)'}")
    print("=" * 60)

    # CSV 로드
    print("\n📋 CSV 로드 중...")
    records = load_csv(CSV_PATH)
    print(f"   {len(records)}개 항목 읽음")

    csv_map: dict[str, dict] = {}
    for rec in records:
        id_, path = build_id_and_path(rec)
        csv_map[id_] = {**rec, "id": id_, "menu_path": path}

    print(f"   고유 메뉴 ID: {len(csv_map)}개")

    # Neo4j 연결
    print(f"\n🔌 Neo4j 연결 중...")
    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    try:
        driver.verify_connectivity()
        print("   ✅ 연결 성공")
    except Exception as e:
        print(f"   ❌ 연결 실패: {e}")
        return

    with driver.session() as session:
        existing_ids = get_existing_ids(session)
        qa_linked_ids = get_qa_linked_ids(session)

        new_ids     = set(csv_map) - existing_ids
        removed_ids = existing_ids - set(csv_map)
        updated_ids = existing_ids & set(csv_map)

        print(f"\n📊 비교 결과:")
        print(f"   기존 MenuItem  : {len(existing_ids)}개")
        print(f"   CSV 항목       : {len(csv_map)}개")
        print(f"   ➕ 신규 추가   : {len(new_ids)}개")
        print(f"   ✏️  경로 갱신   : {len(updated_ids)}개")
        print(f"   ➖ 제거 대상   : {len(removed_ids)}개")

        # 신규 추가
        if new_ids:
            print(f"\n➕ 신규 메뉴 추가 중... ({len(new_ids)}개)")
            added = 0
            for item_id in sorted(new_ids):
                rec = csv_map[item_id]
                ensure_menu_node(session, rec["main_menu"])
                if rec["menu_item"]:
                    ensure_submenu_node(session, rec["main_menu"], rec["sub_menu"])
                upsert_menuitem(session, rec, item_id, rec["menu_path"], is_new=True)
                print(f"   ✅ {rec['menu_path']}")
                added += 1
            print(f"   → {added}개 추가 완료")

        # 기존 경로 갱신
        if updated_ids:
            print(f"\n✏️  기존 메뉴 경로 갱신 중... ({len(updated_ids)}개)")
            for item_id in sorted(updated_ids):
                rec = csv_map[item_id]
                upsert_menuitem(session, rec, item_id, rec["menu_path"], is_new=False)
            print(f"   → {len(updated_ids)}개 갱신 완료")

        # 제거 대상 처리
        if removed_ids:
            print(f"\n➖ 제거 대상 메뉴 ({len(removed_ids)}개):")
            has_qa = [id_ for id_ in sorted(removed_ids) if id_ in qa_linked_ids]
            no_qa  = [id_ for id_ in sorted(removed_ids) if id_ not in qa_linked_ids]

            for item_id in no_qa:
                marker = ""
                print(f"   {marker} {item_id}")
            for item_id in has_qa:
                print(f"   ⚠️  {item_id}  ← QA 연결 있음")

            if do_delete:
                print(f"\n🗑️  삭제 실행 중... (QA 연결 메뉴는 건너뜀)")
                for item_id in sorted(no_qa):
                    delete_menuitem(session, item_id)
                    print(f"   ✅ 삭제: {item_id}")
                if has_qa:
                    print(f"\n   ⏭️  QA 연결 메뉴 {len(has_qa)}개 건너뜀 (보존):")
                    for item_id in has_qa:
                        print(f"      ↳ {item_id}")
                print(f"\n   → {len(no_qa)}개 삭제 완료, {len(has_qa)}개 보존")
            else:
                print(f"\n   ℹ️  실제 삭제는 --delete 옵션으로 실행하세요.")
                if has_qa:
                    print(f"   ℹ️  QA 연결된 {len(has_qa)}개는 --delete 실행 시에도 자동으로 보존됩니다.")

    driver.close()

    # 최종 요약
    print("\n" + "=" * 60)
    print("  완료")
    print(f"  신규: {len(new_ids)}개 추가")
    print(f"  갱신: {len(updated_ids)}개")
    if do_delete:
        qa_linked_count = sum(1 for id_ in removed_ids if id_ in qa_linked_ids)
        deleted_count = len(removed_ids) - qa_linked_count
        print(f"  제거: {deleted_count}개 삭제, {qa_linked_count}개 보존(QA연결)")
    else:
        print(f"  제거: {len(removed_ids)}개 (미삭제)")
    print("=" * 60)
    if not do_delete and removed_ids:
        print("\n다음 명령으로 제거 항목을 삭제할 수 있습니다:")
        print("   .venv\\Scripts\\python.exe update_menu.py --delete")
        print("   ※ QA가 연결된 메뉴는 --delete 실행 시에도 자동으로 보존됩니다.")


if __name__ == "__main__":
    main()
