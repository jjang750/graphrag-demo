"""
QA 텍스트 파일 파싱 유틸리티
- add_qa.py, update_qa_tags.py 에서 공통 사용
- Q/A/T 라인 정규식 통일 (콜론/탭 구분자 모두 지원)
"""

import re
from pathlib import Path

# Q/A/T 라인 정규식 (비탐욕적 매칭, 양쪽 따옴표 처리)
RE_Q = re.compile(r'^Q\d+[\s:]+\.?\s*"?(.*?)(?:"?\s*)?$')
RE_A = re.compile(r'^A\d+[\s:]+\.?\s*"?(.*?)(?:"?\s*)?$')
RE_T = re.compile(r'^(T\d+[\s:]+)(#.*)')
RE_TAG = re.compile(r'#([\w가-힣/]+)')


def parse_qa_file(filepath) -> list[dict]:
    """
    QA 텍스트 파일을 파싱하여 QA 항목 리스트 반환.

    Returns:
        [{"question": str, "answer": str, "tags": [str], "source": str}, ...]
    """
    source = Path(filepath).stem
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    entries = []
    for block in re.split(r'\n\s*\n', content):
        block = block.strip()
        if not block or block.startswith('#META'):
            continue

        q_text = a_text = None
        tags = []

        for line in block.splitlines():
            ls = line.strip()
            if not ls:
                continue

            qm = RE_Q.match(ls)
            am = RE_A.match(ls)
            tm = RE_T.match(ls)

            if qm and q_text is None:
                q_text = qm.group(1).strip().strip('"')
            elif am and q_text and a_text is None:
                a_text = am.group(1).strip().strip('"')
            elif tm and q_text:
                tags = RE_TAG.findall(tm.group(2))

        if q_text and a_text:
            entries.append({
                "question": q_text,
                "answer": a_text,
                "tags": tags,
                "source": source,
            })

    return entries


def load_all_qa(folder: str) -> list[dict]:
    """폴더 내 모든 .txt QA 파일을 파싱하여 합친 리스트 반환."""
    all_entries = []
    for filepath in sorted(Path(folder).glob("*.txt")):
        entries = parse_qa_file(filepath)
        print(f"  {filepath.name:<25} {len(entries):>4}개")
        all_entries.extend(entries)
    return all_entries


def parse_qa_blocks(content: str) -> list[dict]:
    """
    파일 내용에서 QA 블록 파싱 (원본 raw 텍스트 보존).
    update_qa_tags.py의 in-place 치환용.

    Returns:
        [{"type": "qa"|"skip", "raw": str, "question": str, "answer": str,
          "tags": [str], "t_line_str": str|None}, ...]
    """
    blocks = []
    for raw in re.split(r'\n\s*\n', content):
        stripped = raw.strip()
        if not stripped or stripped.startswith('#META'):
            blocks.append({"type": "skip", "raw": raw})
            continue

        q_text = a_text = t_line_str = None
        tags = []

        for line in stripped.splitlines():
            ls = line.strip()
            if not ls:
                continue

            qm = RE_Q.match(ls)
            am = RE_A.match(ls)
            tm = RE_T.match(ls)

            if qm and q_text is None:
                q_text = qm.group(1).strip().strip('"')
            elif am and q_text and a_text is None:
                a_text = am.group(1).strip().strip('"')
            elif tm and q_text:
                t_line_str = ls
                tags = RE_TAG.findall(tm.group(2))

        if q_text and a_text:
            blocks.append({
                "type": "qa",
                "raw": raw,
                "question": q_text,
                "answer": a_text,
                "tags": tags,
                "t_line_str": t_line_str,
            })
        else:
            blocks.append({"type": "skip", "raw": raw})

    return blocks
