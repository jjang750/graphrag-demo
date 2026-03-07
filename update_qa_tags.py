"""
QA .txt 파일 태그 정규화 스크립트
- T 라인에 MenuItem 이름 태그 추가 (Gemini LLM 활용)
- DOMAIN_MAP으로 소스별 후보 MenuItem 필터링 (오탐 방지)
- 기존 태그 보존 / 이미 MenuItem 태그 있으면 스킵
- 수정 전 .bak 백업 생성
- Neo4j QA 노드의 tags 속성도 업데이트

실행:
    .venv\\Scripts\\python.exe update_qa_tags.py --dry-run
    .venv\\Scripts\\python.exe update_qa_tags.py
    .venv\\Scripts\\python.exe update_qa_tags.py --source 검침qna
    .venv\\Scripts\\python.exe update_qa_tags.py --skip-neo4j
"""

import os
import re
import csv
import time
import argparse
import shutil
from pathlib import Path

from dotenv import load_dotenv
from google import genai
import neo4j

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

MANUALS_DIR = "manuals"
MENU_LIST_CSV = "docs/xperp_menu_list_all.csv"
LLM_MODEL = "gemini-3-flash-preview"
BATCH_SIZE = 5  # LLM 1회 호출당 QA 수

# 소스 파일명 → 대분류 매핑
# None = 전체 도메인 (기타qna, qna)
DOMAIN_MAP: dict[str, list[str] | None] = {
    "검침qna":    ["검침"],
    "수납qna":    ["수납"],
    "부과qna":    ["부과"],
    "회계qna":    ["회계"],
    "입주자qna":  ["입주자"],
    "단지qna":    ["단지관리"],
    "인사급여qna": ["인사/급여"],
    "투표qna":    ["입주자"],   # 전자투표는 입주자 하위
    "아이디qna":  ["시스템"],
    "기타qna":    None,
    "qna":        None,
    # QA_20260306 신규 소스 (파일명 기반)
    "부과":       ["부과"],
    "회계":       ["회계"],
    "인사급여":   ["인사/급여"],
    "단지":       ["단지관리"],
    "민원":       ["단지관리"],
}


# ── 데이터 로드 ────────────────────────────────────────────────────────────────

def load_menu_by_domain(csv_path: str) -> dict[str, list[str]]:
    """CSV → {대분류: [MenuItem 이름, ...]} 딕셔너리"""
    domain_map: dict[str, list[str]] = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            main = (row.get("대분류") or "").strip()
            sub  = (row.get("중분류") or "").strip()
            item = (row.get("소분류") or "").strip()
            if not main and not sub:
                continue
            name = item if item else sub
            if not name:
                continue
            if main not in domain_map:
                domain_map[main] = []
            if name not in domain_map[main]:
                domain_map[main].append(name)
    return domain_map


def get_candidates(source: str, domain_map: dict[str, list[str]]) -> list[str]:
    """소스 파일명 → 후보 MenuItem 이름 목록"""
    domains = DOMAIN_MAP.get(source)
    if domains is None:
        # None = 전체 도메인
        return [n for names in domain_map.values() for n in names]
    candidates: list[str] = []
    for d in domains:
        candidates.extend(domain_map.get(d, []))
    return candidates


# ── 태그 유틸 ──────────────────────────────────────────────────────────────────

def name_to_tag(name: str) -> str:
    """MenuItem 이름 → 태그 형식 (공백 제거)"""
    return "#" + name.replace(" ", "")


def already_has_menu_tag(tags: list[str], candidates: list[str]) -> bool:
    """기존 태그 중 이미 MenuItem 이름 태그가 있는지 확인"""
    if not tags or not candidates:
        return False
    cand_norm = {c.replace(" ", "").lower() for c in candidates}
    tag_norm  = {t.lower() for t in tags}
    return bool(tag_norm & cand_norm)


# ── 파싱 ──────────────────────────────────────────────────────────────────────

def parse_qa_blocks(content: str) -> list[dict]:
    """
    파일 내용에서 QA 블록 파싱.
    각 블록의 원본 raw 텍스트를 보존하여 in-place 치환에 사용.
    """
    blocks: list[dict] = []
    raw_blocks = re.split(r'\n\s*\n', content)

    for raw in raw_blocks:
        stripped = raw.strip()
        if not stripped or stripped.startswith('#META'):
            blocks.append({"type": "skip", "raw": raw})
            continue

        q_text = a_text = t_line_str = None
        tags: list[str] = []

        for line in stripped.splitlines():
            ls = line.strip()
            if not ls:
                continue
            # Q 라인 — ': ' (콜론) 또는 '\t' (탭) 구분자 모두 지원
            qm = re.match(r'^Q\d+[\s:]+\.?\s*"?(.*?)(?:"?\s*)?$', ls)
            # A 라인
            am = re.match(r'^A\d+[\s:]+\.?\s*"?(.*?)(?:"?\s*)?$', ls)
            # T 라인
            tm = re.match(r'^(T\d+[\s:]+)(#.*)', ls)

            if qm and q_text is None:
                q_text = qm.group(1).strip().strip('"')
            elif am and q_text and a_text is None:
                a_text = am.group(1).strip().strip('"')
            elif tm and q_text:
                t_line_str = ls                      # "T1: #tag1 #tag2" 전체
                tags = re.findall(r'#([\w가-힣/]+)', tm.group(2))

        if q_text and a_text:
            blocks.append({
                "type": "qa",
                "raw": raw,
                "question": q_text,
                "answer": a_text,
                "tags": tags,
                "t_line_str": t_line_str,  # None이면 T 라인 없음
            })
        else:
            blocks.append({"type": "skip", "raw": raw})

    return blocks


# ── LLM ───────────────────────────────────────────────────────────────────────

def call_llm_batch(
    client,
    qa_list: list[dict],
    candidates: list[str],
) -> list[list[str]]:
    """
    배치 QA에 대해 Gemini LLM으로 관련 MenuItem 이름 선택.
    Returns: 각 QA에 대한 MenuItem 이름 리스트 (최대 3개)
    """
    if not candidates:
        return [[] for _ in qa_list]

    cand_text = "\n".join(f"  {i+1}. {n}" for i, n in enumerate(candidates))
    qa_parts = []
    for idx, qa in enumerate(qa_list, 1):
        q = qa["question"][:120]
        a = qa["answer"][:200]
        qa_parts.append(f"[QA{idx}]\nQ: {q}\nA: {a}")

    prompt = (
        "아래 QA 목록에서 각 항목과 가장 관련 있는 메뉴를 목록에서 1~3개 선택하세요.\n\n"
        f"메뉴 목록:\n{cand_text}\n\n"
        + "\n\n".join(qa_parts)
        + "\n\n응답 형식 (번호만, / 로 구분): QA1:1,3 / QA2:2 / QA3:\n"
        "관련 메뉴 없으면 해당 QA 번호 뒤를 빈 값으로 두세요."
    )

    try:
        resp = client.models.generate_content(model=LLM_MODEL, contents=prompt)
        text = resp.text.strip()

        results: list[list[str]] = [[] for _ in qa_list]
        for m in re.finditer(r'QA(\d+)\s*:\s*([0-9,\s]*)', text):
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(qa_list):
                nums = [x.strip() for x in m.group(2).split(",") if x.strip()]
                names: list[str] = []
                for num_str in nums:
                    try:
                        ni = int(num_str) - 1
                        if 0 <= ni < len(candidates):
                            names.append(candidates[ni])
                    except ValueError:
                        pass
                results[idx] = names
        return results

    except Exception as e:
        print(f"      ⚠️  LLM 오류: {e}")
        return [[] for _ in qa_list]


# ── 파일 업데이트 ──────────────────────────────────────────────────────────────

def apply_tags_to_block(raw: str, b: dict, new_names: list[str]) -> str:
    """블록 raw 텍스트에 새 MenuItem 태그를 추가하여 반환"""
    new_tag_str = " ".join(name_to_tag(n) for n in new_names)

    if b["t_line_str"]:
        # 기존 T 라인 끝에 이어 붙이기
        new_t = b["t_line_str"] + " " + new_tag_str
        return raw.replace(b["t_line_str"], new_t, 1)
    else:
        # T 라인 없음: 블록 끝에 새 T 라인 추가
        q_num_m = re.search(r'Q(\d+)', raw)
        n = q_num_m.group(1) if q_num_m else "1"
        stripped_raw = raw.rstrip('\n')
        trailing = raw[len(stripped_raw):]
        new_t_line = f"\nT{n}: {new_tag_str}"
        return stripped_raw + new_t_line + trailing


# ── 파일 처리 ──────────────────────────────────────────────────────────────────

def process_file(
    filepath: Path,
    domain_map: dict[str, list[str]],
    client,
    dry_run: bool,
    skip_neo4j: bool,
    neo4j_driver=None,
) -> tuple[int, int]:
    """
    단일 QA .txt 파일 처리.
    Returns: (총 QA 블록 수, 업데이트된 블록 수)
    """
    source = filepath.stem
    candidates = get_candidates(source, domain_map)

    if not candidates:
        print(f"  {filepath.name}: 후보 MenuItem 없음 (스킵)")
        return 0, 0

    print(f"\n  [{filepath.name}]  후보 MenuItem {len(candidates)}개")

    content = filepath.read_text(encoding="utf-8")
    blocks = parse_qa_blocks(content)

    qa_blocks = [b for b in blocks if b["type"] == "qa"]
    total = len(qa_blocks)

    if total == 0:
        print(f"    QA 블록 없음")
        return 0, 0

    # 업데이트 필요 여부 필터링
    to_update = [
        b for b in qa_blocks
        if not already_has_menu_tag(b["tags"], candidates)
    ]

    if not to_update:
        print(f"    모든 {total}개 블록 이미 MenuItem 태그 있음 (스킵)")
        return total, 0

    print(f"    업데이트 필요: {len(to_update)}/{total}개")

    # 배치 LLM 호출
    new_names_map: dict[int, list[str]] = {}  # id(block) → [MenuItem names]

    for i in range(0, len(to_update), BATCH_SIZE):
        batch = to_update[i:i + BATCH_SIZE]
        batch_n = i // BATCH_SIZE + 1
        total_n = (len(to_update) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"    배치 {batch_n}/{total_n}... ", end="", flush=True)

        results = call_llm_batch(
            client,
            [{"question": b["question"], "answer": b["answer"]} for b in batch],
            candidates,
        )

        for b, names in zip(batch, results):
            if names:
                new_names_map[id(b)] = names
                print("✅", end=" ", flush=True)
            else:
                print("-", end=" ", flush=True)
        print()

        time.sleep(0.5)  # API rate limit 방지

    updated = len(new_names_map)

    if dry_run:
        print(f"    [DRY-RUN] {updated}개 블록 업데이트 예정:")
        for b in to_update:
            if id(b) in new_names_map:
                names = new_names_map[id(b)]
                print(f"      Q: {b['question'][:60]}")
                print(f"         추가 태그: {', '.join(names)}")
        return total, updated

    # ── 파일 실제 업데이트 ──────────────────────────────────────────────────────
    backup = filepath.with_suffix(".txt.bak")
    shutil.copy2(filepath, backup)

    new_content = content
    for b in qa_blocks:
        if id(b) not in new_names_map:
            continue
        old_raw = b["raw"]
        new_raw = apply_tags_to_block(old_raw, b, new_names_map[id(b)])
        new_content = new_content.replace(old_raw, new_raw, 1)

    filepath.write_text(new_content, encoding="utf-8")
    print(f"    ✅ {updated}개 업데이트 완료 (백업: {backup.name})")

    # ── Neo4j 태그 업데이트 ─────────────────────────────────────────────────────
    if not skip_neo4j and neo4j_driver:
        _update_neo4j_tags(neo4j_driver, source, qa_blocks, new_names_map)

    return total, updated


def _update_neo4j_tags(
    driver,
    source: str,
    qa_blocks: list[dict],
    new_names_map: dict[int, list[str]],
) -> None:
    """Neo4j QA 노드의 tags 속성 업데이트"""
    updated = 0
    with driver.session() as session:
        for b in qa_blocks:
            if id(b) not in new_names_map:
                continue
            names = new_names_map[id(b)]
            # 이름에서 공백 제거하여 태그 문자열로 변환
            new_tag_strs = [n.replace(" ", "") for n in names]
            existing = b["tags"]
            all_tags = existing + [t for t in new_tag_strs if t not in existing]
            session.run(
                """
                MATCH (q:QA {source: $source})
                WHERE q.question = $question
                SET q.tags = $tags
                """,
                source=source,
                question=b["question"],
                tags=all_tags,
            )
            updated += 1
    print(f"    Neo4j 업데이트: {updated}개")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def list_available_models(client) -> None:
    """generateContent를 지원하는 사용 가능한 모델 목록 출력"""
    print("\n사용 가능한 생성 모델 목록:")
    try:
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            if "generateContent" in str(actions):
                print(f"  {m.name}")
    except Exception as e:
        print(f"  ⚠️  모델 목록 조회 실패: {e}")


def validate_model(client, model_name: str) -> bool:
    """모델이 실제로 사용 가능한지 테스트 프롬프트로 검증"""
    try:
        resp = client.models.generate_content(model=model_name, contents="안녕")
        _ = resp.text
        return True
    except Exception as e:
        print(f"  ❌ 모델 '{model_name}' 사용 불가: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="QA 태그 정규화 (MenuItem 이름 추가)")
    parser.add_argument("--dry-run", action="store_true", help="파일 수정 없이 미리 보기")
    parser.add_argument("--source", help="특정 소스만 처리 (예: 검침qna)")
    parser.add_argument("--skip-neo4j", action="store_true", help="Neo4j tags 업데이트 건너뜀")
    parser.add_argument("--list-models", action="store_true", help="사용 가능한 모델 목록만 출력하고 종료")
    parser.add_argument("--dir", default=MANUALS_DIR,
                        help=f"QA 파일 디렉터리 (기본값: {MANUALS_DIR})")
    args = parser.parse_args()

    qa_dir = args.dir

    print("=" * 65)
    print("  QA 태그 정규화: MenuItem 이름 태그 추가")
    print(f"  dry-run: {args.dry_run}  /  source: {args.source or '전체'}")
    print(f"  dir    : {qa_dir}")
    print("=" * 65)

    # ── 1. CSV 로드 ─────────────────────────────────────────────────────────────
    print(f"\n📋 메뉴 목록 로드: {MENU_LIST_CSV}")
    domain_map = load_menu_by_domain(MENU_LIST_CSV)
    total_items = sum(len(v) for v in domain_map.values())
    print(f"  대분류 {len(domain_map)}개 / MenuItem {total_items}개 로드 완료\n")

    # ── 2. Gemini LLM 초기화 + 모델 검증 ────────────────────────────────────────
    print(f"🤖 Gemini 클라이언트 초기화 (모델: {LLM_MODEL})")
    client = genai.Client(api_key=GOOGLE_API_KEY)

    if args.list_models:
        list_available_models(client)
        return

    print(f"  모델 검증 중...", end=" ", flush=True)
    if not validate_model(client, LLM_MODEL):
        list_available_models(client)
        print(f"\n❌ update_qa_tags.py 상단의 LLM_MODEL을 위 목록 중 하나로 변경하세요.")
        return
    print("✅ 완료\n")

    # ── 3. Neo4j 연결 (필요한 경우) ─────────────────────────────────────────────
    neo4j_driver = None
    if not args.skip_neo4j and not args.dry_run:
        print(f"🔌 Neo4j 연결: {NEO4J_URI}")
        try:
            neo4j_driver = neo4j.GraphDatabase.driver(
                NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD)
            )
            neo4j_driver.verify_connectivity()
            print("  ✅ 연결 성공\n")
        except Exception as e:
            print(f"  ⚠️  연결 실패 (Neo4j 업데이트 건너뜀): {e}\n")
            neo4j_driver = None

    # ── 4. 파일 처리 ────────────────────────────────────────────────────────────
    manuals = Path(qa_dir)
    if not manuals.exists():
        print(f"❌ {qa_dir}/ 디렉터리 없음")
        return

    files = sorted(manuals.glob("*.txt"))
    if args.source:
        files = [f for f in files if f.stem == args.source]
        if not files:
            print(f"❌ {args.source}.txt 파일 없음 (디렉터리: {qa_dir})")
            return

    print(f"📁 처리 파일: {len(files)}개\n")

    grand_total = grand_updated = 0
    for fp in files:
        t, u = process_file(
            fp, domain_map, client,
            args.dry_run, args.skip_neo4j, neo4j_driver,
        )
        grand_total  += t
        grand_updated += u

    # ── 5. 결과 요약 ────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"📊 완료: 총 QA {grand_total}개 처리 / {grand_updated}개 업데이트")
    if not args.dry_run and grand_updated > 0:
        print(f"  백업: manuals/*.txt.bak")
    print(f"\n다음 단계:")
    print(f"  .venv\\Scripts\\python.exe link_qa_tag.py --dry-run")
    print(f"{'='*65}")

    if neo4j_driver:
        neo4j_driver.close()


if __name__ == "__main__":
    main()
