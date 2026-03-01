"""
QA → MenuItem 임베딩 유사도 기반 자동 연결 스크립트
- 각 QA 임베딩 ↔ MenuItem 임베딩 코사인 유사도 계산
- 유사도 임계값 이상인 쌍에 (QA)-[:IN_MENU]->(MenuItem) 엣지 생성 (MERGE)
- 기존 link_qa_menu.py(텍스트 패턴) 결과와 병행 사용 가능 (중복 없음)

실행:
    .venv\\Scripts\\python.exe link_qa_menu_embed.py --dry-run
    .venv\\Scripts\\python.exe link_qa_menu_embed.py --threshold 0.75
    .venv\\Scripts\\python.exe link_qa_menu_embed.py --threshold 0.72 --top-k 2
"""

import os
import argparse
import numpy as np
from dotenv import load_dotenv
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")


def normalize(vectors: np.ndarray) -> np.ndarray:
    """L2 정규화 (코사인 유사도를 내적으로 계산하기 위한 사전 처리)"""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


def fetch_menu_items(session) -> list:
    """MenuItem 임베딩 전체 로드"""
    result = session.run("""
        MATCH (m:MenuItem)
        WHERE m.embedding IS NOT NULL
        RETURN m.id AS id, m.name AS name, m.menu_path AS path, m.embedding AS embedding
        ORDER BY m.id
    """)
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "path": r["path"] or "",
            "embedding": list(r["embedding"]),
        }
        for r in result
    ]


def count_qa_total(session) -> int:
    return session.run("MATCH (q:QA) RETURN count(q) AS c").single()["c"]


def count_qa_with_embedding(session) -> int:
    return session.run(
        "MATCH (q:QA) WHERE q.embedding IS NOT NULL RETURN count(q) AS c"
    ).single()["c"]


def fetch_qa_batch(session, skip: int, limit: int) -> list:
    """QA 배치 로드 (임베딩 있는 것만)"""
    result = session.run("""
        MATCH (q:QA)
        WHERE q.embedding IS NOT NULL
        RETURN q.id AS id, q.question AS question, q.embedding AS embedding
        ORDER BY q.id
        SKIP $skip LIMIT $limit
    """, skip=skip, limit=limit)
    return [
        {
            "id": r["id"],
            "question": (r["question"] or "")[:60],
            "embedding": list(r["embedding"]),
        }
        for r in result
    ]


def create_links_batch(session, links: list[tuple]):
    """(qa_id, menu_item_id) 쌍 목록으로 IN_MENU 엣지 생성 (MERGE)"""
    for qa_id, menu_item_id in links:
        session.run("""
            MATCH (q:QA {id: $qa_id})
            MATCH (m:MenuItem {id: $menu_id})
            MERGE (q)-[:IN_MENU]->(m)
        """, qa_id=qa_id, menu_id=menu_item_id)


def verify(session, total_qa: int):
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
    print(f"  연결된 QA        : {result_qa:,}개 / {total_qa:,}개 ({result_qa / total_qa * 100:.1f}%)")
    print(f"  연결된 MenuItem  : {result_menu}개")

    top = session.run("""
        MATCH ()-[:IN_MENU]->(m:MenuItem)
        RETURN m.menu_path AS path, count(*) AS cnt
        ORDER BY cnt DESC LIMIT 10
    """)
    print(f"\n📋 MenuItem별 연결 QA 수 TOP 10:\n")
    print(f"  {'메뉴 경로':<45} {'QA수':>5}")
    print(f"  {'-'*45} {'-'*5}")
    for r in top:
        print(f"  {(r['path'] or ''):<45} {r['cnt']:>5}")


def main():
    parser = argparse.ArgumentParser(
        description="QA → MenuItem 임베딩 유사도 기반 자동 연결"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.75,
        help="코사인 유사도 임계값 (기본: 0.75)"
    )
    parser.add_argument(
        "--top-k", type=int, default=1,
        help="QA당 연결할 최대 MenuItem 수 (기본: 1)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="QA 처리 배치 크기 (기본: 500)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="DB 수정 없이 유사도 분석 결과만 출력"
    )
    args = parser.parse_args()

    print("=" * 65)
    print("  QA → MenuItem 임베딩 유사도 자동 연결")
    print(f"  임계값: {args.threshold}  /  top-k: {args.top_k}  /  dry-run: {args.dry_run}")
    print("=" * 65)

    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        return

    with driver.session() as session:
        # ── 1. MenuItem 임베딩 로드 ──────────────────────────────────────
        print("📥 MenuItem 임베딩 로드 중...")
        menu_items = fetch_menu_items(session)

        if not menu_items:
            print("❌ MenuItem 임베딩 없음. init_db.py 먼저 실행하세요.")
            driver.close()
            return

        total_menu = len(menu_items)
        menu_ids = [m["id"] for m in menu_items]
        menu_matrix = np.array([m["embedding"] for m in menu_items], dtype=np.float32)
        menu_matrix_norm = normalize(menu_matrix)
        print(f"  MenuItem {total_menu}개 / 임베딩 차원: {menu_matrix.shape[1]}\n")

        # ── 2. QA 현황 확인 ──────────────────────────────────────────────
        total_qa = count_qa_total(session)
        qa_embed_count = count_qa_with_embedding(session)
        print(f"📊 QA 현황: 전체 {total_qa:,}개 / 임베딩 있음 {qa_embed_count:,}개\n")

        if qa_embed_count == 0:
            print("❌ QA 임베딩 없음. add_qa.py 먼저 실행하세요.")
            driver.close()
            return

        # ── 3. 배치별 유사도 계산 ────────────────────────────────────────
        all_links: list[tuple] = []   # (qa_id, menu_item_id, score)
        processed = 0
        skip = 0

        print(f"🔍 유사도 계산 중... (배치 크기: {args.batch_size})\n")

        while True:
            batch = fetch_qa_batch(session, skip, args.batch_size)
            if not batch:
                break

            qa_ids = [q["id"] for q in batch]
            qa_matrix = np.array([q["embedding"] for q in batch], dtype=np.float32)
            qa_matrix_norm = normalize(qa_matrix)

            # 코사인 유사도 행렬: (batch_size × total_menu)
            sim_matrix = np.dot(qa_matrix_norm, menu_matrix_norm.T)

            for i, qa_id in enumerate(qa_ids):
                row = sim_matrix[i]
                above = np.where(row >= args.threshold)[0]
                if len(above) == 0:
                    continue
                # 유사도 내림차순 → top-k 선택
                top_idx = above[np.argsort(row[above])[::-1]][: args.top_k]
                for menu_idx in top_idx:
                    all_links.append((qa_id, menu_ids[menu_idx], float(row[menu_idx])))

            processed += len(batch)
            linked_so_far = len(set(qa_id for qa_id, _, _ in all_links))
            print(
                f"  처리: {processed:,}/{qa_embed_count:,} "
                f"({processed / qa_embed_count * 100:.1f}%)  "
                f"누적 링크: {len(all_links):,}개  연결 QA: {linked_so_far:,}개",
                end="\r",
            )

            skip += args.batch_size
            if len(batch) < args.batch_size:
                break

        print()  # 진행 표시 줄바꿈

        # ── 4. 결과 요약 ─────────────────────────────────────────────────
        linked_qa_count = len(set(qa_id for qa_id, _, _ in all_links))

        print(f"\n{'='*65}")
        print(f"📊 유사도 분석 결과 (임계값 {args.threshold} / top-k {args.top_k})")
        print(f"{'='*65}")
        print(f"  생성할 IN_MENU 링크  : {len(all_links):,}개")
        print(f"  연결 대상 QA         : {linked_qa_count:,}개 / {total_qa:,}개 ({linked_qa_count / total_qa * 100:.1f}%)")
        if linked_qa_count > 0:
            print(f"  QA당 평균 연결 수    : {len(all_links) / linked_qa_count:.2f}개")

        # 유사도 분포 분석
        if all_links:
            scores = np.array([s for _, _, s in all_links])
            print(f"\n  유사도 분포:")
            print(f"    평균: {scores.mean():.4f}  최소: {scores.min():.4f}  최대: {scores.max():.4f}  중앙값: {np.median(scores):.4f}")
            print()
            thresholds = [0.70, 0.72, 0.75, 0.78, 0.80, 0.85, 0.90]
            print(f"  {'임계값':>8}  {'링크수':>8}  {'QA수':>8}  {'커버리지':>8}")
            print(f"  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
            for t in thresholds:
                cnt = int((scores >= t).sum())
                qa_cnt = len(set(qa_id for qa_id, _, s in all_links if s >= t))
                marker = " ◀ 현재" if abs(t - args.threshold) < 0.001 else ""
                print(f"  {t:>8.2f}  {cnt:>8,}  {qa_cnt:>8,}  {qa_cnt / total_qa * 100:>7.1f}%{marker}")

        # 상위 매칭 샘플
        print(f"\n  유사도 TOP 15 샘플:")
        sample = sorted(all_links, key=lambda x: -x[2])[:15]
        print(f"  {'QA ID':<8}  {'메뉴 경로':<40}  {'유사도':>6}")
        print(f"  {'-'*8}  {'-'*40}  {'-'*6}")
        for qa_id, menu_id, score in sample:
            menu_info = next((m for m in menu_items if m["id"] == menu_id), {})
            menu_path = (menu_info.get("path") or menu_info.get("name") or menu_id)[:38]
            print(f"  {str(qa_id):<8}  {menu_path:<40}  {score:>6.4f}")

        if args.dry_run:
            print(f"\n[DRY-RUN] 실제 적용하려면 --dry-run 없이 실행:")
            print(f"  .venv\\Scripts\\python.exe link_qa_menu_embed.py --threshold {args.threshold} --top-k {args.top_k}")
            driver.close()
            return

        if len(all_links) == 0:
            print(f"\n⚠️  임계값 {args.threshold} 이상 매칭 없음. --threshold를 낮춰보세요.")
            driver.close()
            return

        # ── 5. 실제 적용 ─────────────────────────────────────────────────
        unique_pairs = [(qa_id, menu_id) for qa_id, menu_id, _ in all_links]
        print(f"\n🔗 IN_MENU 엣지 생성 중... ({len(unique_pairs):,}개)")

        chunk = 200
        for i in range(0, len(unique_pairs), chunk):
            create_links_batch(session, unique_pairs[i: i + chunk])
            done = min(i + chunk, len(unique_pairs))
            print(f"  진행: {done:,}/{len(unique_pairs):,}", end="\r")

        print(f"\n✅ {len(unique_pairs):,}개 IN_MENU 엣지 생성/업데이트 완료\n")

        # ── 6. 최종 검증 ─────────────────────────────────────────────────
        print("📊 최종 결과 검증:")
        verify(session, total_qa)

    driver.close()
    print("\n🎉 완료!")


if __name__ == "__main__":
    main()
