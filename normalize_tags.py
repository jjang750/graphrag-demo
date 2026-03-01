"""
QA 태그 정규화 스크립트
- 중복/유사 태그를 대표 태그로 통합
- 내용 없는 메타 태그 제거
- Neo4j QA 노드의 tags 속성 업데이트

실행:
    # 변경 내용 미리 보기 (DB 수정 없음)
    .venv\\Scripts\\python.exe normalize_tags.py --dry-run

    # 실제 적용
    .venv\\Scripts\\python.exe normalize_tags.py
"""

import os
import argparse
from collections import defaultdict, Counter
from dotenv import load_dotenv
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# ─────────────────────────────────────────────
# 태그 정규화 규칙: 변형 → 대표 태그
# ─────────────────────────────────────────────
TAG_NORMALIZE = {
    # ── 시스템/제품명 ──
    "ERP":          "XPERP",
    "ERP시스템":    "XPERP",
    "XP_ERP":       "XPERP",
    "XPRP":         "XPERP",
    "ERP프로그램":  "XPERP",
    "ERP로그인":    "XPERP",
    "XPHub":        "XP허브",
    "XP_Hub":       "XP허브",
    "엑스피보트":   "XP허브",

    # ── 로그인/인증 ──
    "로그인문제":   "로그인오류",
    "로그인실패":   "로그인오류",
    "접속오류":     "로그인오류",

    # ── 비밀번호 ──
    "비밀번호재설정":   "비밀번호초기화",
    "비밀번호오류":     "비밀번호초기화",

    # ── 사용자 승인 ──
    "사용자승인등록":   "사용자승인",
    "아이디승인":       "사용자승인",
    "서브아이디승인":   "사용자승인",
    "계정승인":         "사용자승인",
    "계정활성화":       "사용자승인",
    "관리자승인":       "사용자승인",

    # ── 수납 오류 ──
    "자동수납오류":         "수납오류",
    "자동수납오류처리":     "수납오류",

    # ── 퇴사/퇴직 ──
    "퇴사처리":     "퇴직처리",
    "퇴사자처리":   "퇴직처리",
    "퇴직정산":     "퇴직처리",
    "퇴직금정산":   "퇴직처리",

    # ── 재입사 ──
    "재입사처리":   "재입사",
    "재입사신청":   "재입사",

    # ── 알림톡 ──
    "알림톡발송":       "알림톡",
    "카카오톡인증":     "카카오톡",
    "문자메시지":       "문자발송",
    "SMS발송":          "문자발송",

    # ── 고지서 ──
    "고지서출력":       "고지서",
    "전자고지서":       "고지서",
    "관리비고지서":     "고지서",
    "종이고지서":       "고지서",

    # ── 세금계산서 ──
    "전자세금계산서":   "세금계산서",
    "세금계산서발행":   "세금계산서",
    "계산서발행":       "세금계산서",

    # ── 부과 ──
    "관리비부과처리":   "관리비부과",
    "부과처리":         "관리비부과",

    # ── 검침 ──
    "검침데이터":       "검침값",
    "수동입력":         "검침값",

    # ── 권한 ──
    "권한부여":         "권한설정",
    "메뉴권한":         "권한설정",
    "관리자권한":       "권한설정",
    "사용자권한":       "권한설정",
    "읽기쓰기권한":     "권한설정",
    "권한변경":         "권한설정",

    # ── 전자투표 ──
    "전자투표등록":     "투표등록",
    "투표재등록":       "투표등록",
    "재등록":           "투표등록",
    "투표수정":         "투표설정",
    "투표오류":         "전자투표오류",

    # ── 선거인명부 ──
    "선거명부":         "선거인명부",
    "선거인명 부수정":  "선거인명부",

    # ── 단지 ──
    "관리단지추가":     "단지추가",
    "단지정보":         "단지관리",

    # ── 입주자 ──
    "입주자등록":       "입주등록",
    "입주자관리":       "입주자현황",
    "소유주정보":       "소유주",
    "소유 주투표":      "소유주투표",

    # ── 급여 ──
    "임금명세서":       "급여명세서",

    # ── 회계 ──
    "회계데이터전송":   "회계데이터",
    "회계데이터":       "회계데이터",

    # ── 개인정보 ──
    "개인정보표시":     "개인정보",
    "개인정보동의":     "개인정보",
    "개인 정보동의":    "개인정보",

    # ── 데이터 오류 ──
    "데이터누락":       "데이터오류",
    "데이터불일치":     "데이터오류",
    "정보불일치":       "데이터오류",
    "정보누락":         "데이터오류",

    # ── 조회 오류 ──
    "조회오류":         "시스템오류",
    "저장오류":         "시스템오류",
    "등록오류":         "시스템오류",
    "입력오류":         "시스템오류",
    "출력오류":         "시스템오류",
    "인쇄오류":         "시스템오류",
    "발송오류":         "시스템오류",
    "프로그램오류":     "시스템오류",
}

# ─────────────────────────────────────────────
# 제거할 메타/노이즈 태그
# ─────────────────────────────────────────────
NOISE_TAGS = {
    # 행위/방법 설명 (개념 아님)
    "해결방안", "오류해결", "문제해결", "이용가이드",
    "사용방법", "확인방법", "조회방법", "출력방법",
    "설정방법", "등록방법", "신청절차", "승인절차",
    "투표절차", "투표방법", "신청정보",

    # 일반 동작 (개념 아님)
    "새로고침", "재로그인", "재설치", "초기화",
    "재발송", "재부과선택", "재마감", "재투표불가",

    # 너무 포괄적
    "문의", "문의방법", "게시판문의", "관리사무소문의",
    "고객센터", "기술지원", "전산업체", "전산업체문의",
    "고객지원", "공문요청",

    # XPERP 브랜드 (시스템 자체라 분류 의미 없음)
    "아파트아이앱", "아파트앱", "모바일앱",
}


def fetch_all_qa(session):
    """모든 QA 노드 조회"""
    result = session.run("""
        MATCH (q:QA)
        RETURN q.id AS id, q.tags AS tags
    """)
    return [(r["id"], r["tags"] or []) for r in result]


def normalize_tag_list(tags):
    """태그 목록 정규화"""
    normalized = []
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        # 노이즈 제거
        if tag in NOISE_TAGS:
            continue
        # 정규화 매핑 적용
        tag = TAG_NORMALIZE.get(tag, tag)
        normalized.append(tag)
    # 중복 제거 (순서 유지)
    seen = set()
    result = []
    for t in normalized:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def compute_changes(all_qa):
    """변경 사항 계산"""
    changes = []
    tag_change_counter = Counter()  # 어떤 변환이 얼마나 일어났는지

    for qa_id, tags in all_qa:
        new_tags = normalize_tag_list(tags)
        if new_tags != list(tags):
            removed = set(tags) - set(new_tags)
            added_canonical = {}
            for t in tags:
                canonical = TAG_NORMALIZE.get(t.strip(), t.strip())
                if canonical != t.strip() and canonical in new_tags:
                    added_canonical[t.strip()] = canonical

            for t in removed:
                if t in NOISE_TAGS:
                    tag_change_counter[f"[제거] {t}"] += 1
                elif t in TAG_NORMALIZE:
                    tag_change_counter[f"[통합] {t} → {TAG_NORMALIZE[t]}"] += 1

            changes.append((qa_id, tags, new_tags))

    return changes, tag_change_counter


def print_dry_run_report(all_qa, changes, tag_change_counter):
    """dry-run 결과 출력"""
    total = len(all_qa)
    changed = len(changes)

    print("=" * 60)
    print("  [DRY-RUN] 태그 정규화 예상 결과")
    print("=" * 60)
    print(f"  전체 QA 수         : {total:,}개")
    print(f"  변경 대상 QA 수    : {changed:,}개 ({changed/total*100:.1f}%)")
    print(f"  정규화 규칙 수     : {len(TAG_NORMALIZE)}개")
    print(f"  노이즈 태그 수     : {len(NOISE_TAGS)}개")
    print("=" * 60)

    print("\n📋 변환 규칙 적용 현황 (상위 30개)\n")
    print(f"  {'변환 내용':<40} {'적용 수':>6}")
    print(f"  {'-'*40} {'-'*6}")
    for rule, cnt in tag_change_counter.most_common(30):
        print(f"  {rule:<40} {cnt:>6}")

    print(f"\n\n📝 변경 샘플 (상위 10개)\n")
    for qa_id, old_tags, new_tags in changes[:10]:
        old_set = set(old_tags)
        new_set = set(new_tags)
        removed = old_set - new_set
        added = new_set - old_set
        print(f"  [{qa_id}]")
        if removed:
            print(f"    제거: {', '.join(sorted(removed))}")
        if added:
            print(f"    추가: {', '.join(sorted(added))}")
        print()

    print(f"\n실제 적용하려면: python normalize_tags.py (--dry-run 없이)")


def apply_changes(session, changes):
    """Neo4j QA 노드 태그 업데이트"""
    for i, (qa_id, old_tags, new_tags) in enumerate(changes, 1):
        session.run("""
            MATCH (q:QA {id: $id})
            SET q.tags = $tags
        """, id=qa_id, tags=new_tags)
        if i % 100 == 0:
            print(f"  진행 중: {i}/{len(changes)}개 완료...", flush=True)


def verify(session):
    """적용 후 검증"""
    # 정규화된 태그 집계
    result = session.run("""
        MATCH (q:QA)
        WHERE q.tags IS NOT NULL AND size(q.tags) > 0
        UNWIND q.tags AS tag
        RETURN tag, count(*) AS cnt
        ORDER BY cnt DESC
        LIMIT 20
    """)
    print("\n📊 정규화 후 TOP 20 태그\n")
    print(f"  {'태그':<25} {'빈도':>6}")
    print(f"  {'-'*25} {'-'*6}")
    for r in result:
        print(f"  {r['tag']:<25} {r['cnt']:>6}")


def main():
    parser = argparse.ArgumentParser(description="QA 태그 정규화")
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 변경 없이 미리 보기만")
    args = parser.parse_args()

    print(f"  Neo4j URI : {NEO4J_URI}")
    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ Neo4j 연결 실패: {e}")
        return

    with driver.session() as session:
        print("📥 QA 데이터 조회 중...")
        all_qa = fetch_all_qa(session)
        print(f"  → {len(all_qa):,}개 QA 로드 완료\n")

        print("🔍 변경 사항 계산 중...")
        changes, tag_change_counter = compute_changes(all_qa)

        if args.dry_run:
            print_dry_run_report(all_qa, changes, tag_change_counter)
        else:
            print_dry_run_report(all_qa, changes, tag_change_counter)
            print("\n" + "=" * 60)
            print("  Neo4j 업데이트 적용 중...")
            print("=" * 60)
            apply_changes(session, changes)
            print(f"\n✅ {len(changes):,}개 QA 태그 업데이트 완료\n")

            print("📊 결과 검증:")
            verify(session)

    driver.close()
    if not args.dry_run:
        print("\n🎉 태그 정규화 완료!")


if __name__ == "__main__":
    main()
