"""
Neo4j 마이그레이션: Concept → Task 레이블/관계 변경
- :Concept 노드 라벨 → :Task
- RELATED_CONCEPT 엣지 → RELATED_TASK

실행:
    .venv\\Scripts\\python.exe migrate_concept_to_task.py --dry-run
    .venv\\Scripts\\python.exe migrate_concept_to_task.py
"""

import os
import argparse
from dotenv import load_dotenv
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")


def count_nodes(session, label: str) -> int:
    return session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]


def count_rels(session, rel_type: str) -> int:
    return session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c").single()["c"]


def step1_create_related_task(session) -> int:
    """RELATED_CONCEPT → RELATED_TASK 엣지 복사 (MERGE로 중복 방지)"""
    result = session.run("""
        MATCH (m:MenuItem)-[:RELATED_CONCEPT]->(c)
        MERGE (m)-[:RELATED_TASK]->(c)
        RETURN count(*) AS c
    """)
    return result.single()["c"]


def step2_delete_related_concept(session) -> int:
    """구 RELATED_CONCEPT 엣지 삭제"""
    result = session.run("""
        MATCH ()-[r:RELATED_CONCEPT]->()
        DELETE r
        RETURN count(r) AS c
    """)
    return result.single()["c"]


def step3_rename_concept_to_task(session) -> int:
    """Concept 라벨 제거 후 Task 라벨 추가 (배치 처리)"""
    result = session.run("""
        MATCH (n:Concept)
        REMOVE n:Concept
        SET n:Task
        RETURN count(n) AS c
    """)
    return result.single()["c"]


def verify(session):
    """마이그레이션 결과 검증"""
    task_count    = count_nodes(session, "Task")
    concept_count = count_nodes(session, "Concept")
    rel_task      = count_rels(session, "RELATED_TASK")
    rel_concept   = count_rels(session, "RELATED_CONCEPT")

    print("\n📊 마이그레이션 결과:")
    print(f"  :Task 노드          : {task_count:,}개")
    print(f"  :Concept 노드 (잔여): {concept_count:,}개  ← 0이어야 정상")
    print(f"  RELATED_TASK 엣지   : {rel_task:,}개")
    print(f"  RELATED_CONCEPT 엣지: {rel_concept:,}개  ← 0이어야 정상")

    if concept_count == 0 and rel_concept == 0:
        print("\n✅ 마이그레이션 정상 완료")
    else:
        print("\n⚠️  미완료 항목이 있습니다. 다시 실행하거나 확인하세요.")


def main():
    parser = argparse.ArgumentParser(
        description="Neo4j Concept→Task 라벨/관계 마이그레이션"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="DB 수정 없이 현재 상태만 출력"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Neo4j 마이그레이션: Concept → Task")
    print(f"  dry-run: {args.dry_run}")
    print("=" * 60)

    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        return

    with driver.session() as session:
        # 현재 상태 출력
        concept_cnt = count_nodes(session, "Concept")
        task_cnt    = count_nodes(session, "Task")
        rel_old     = count_rels(session, "RELATED_CONCEPT")
        rel_new     = count_rels(session, "RELATED_TASK")

        print("📋 현재 상태:")
        print(f"  :Concept 노드       : {concept_cnt:,}개")
        print(f"  :Task 노드          : {task_cnt:,}개")
        print(f"  RELATED_CONCEPT 엣지: {rel_old:,}개")
        print(f"  RELATED_TASK 엣지   : {rel_new:,}개")

        if concept_cnt == 0 and rel_old == 0:
            print("\n✅ 이미 마이그레이션 완료 상태입니다.")
            driver.close()
            return

        if args.dry_run:
            print(f"\n[DRY-RUN] 실제 적용하려면 --dry-run 없이 실행:")
            print(f"  .venv\\Scripts\\python.exe migrate_concept_to_task.py")
            driver.close()
            return

        # ── Step 1: RELATED_TASK 엣지 생성 ────────────────────────
        print(f"\n[1/3] RELATED_CONCEPT → RELATED_TASK 엣지 복사 중...")
        created = step1_create_related_task(session)
        print(f"  → RELATED_TASK 엣지 {created:,}개 생성/확인")

        # ── Step 2: RELATED_CONCEPT 엣지 삭제 ─────────────────────
        print(f"\n[2/3] 구 RELATED_CONCEPT 엣지 삭제 중...")
        deleted_rel = step2_delete_related_concept(session)
        print(f"  → RELATED_CONCEPT 엣지 {deleted_rel:,}개 삭제")

        # ── Step 3: Concept → Task 라벨 변경 ──────────────────────
        print(f"\n[3/3] :Concept 라벨 → :Task 라벨 변경 중...")
        renamed = step3_rename_concept_to_task(session)
        print(f"  → {renamed:,}개 노드 라벨 변경")

        # ── 검증 ──────────────────────────────────────────────────
        verify(session)

    driver.close()
    print("\n🎉 완료!")


if __name__ == "__main__":
    main()
