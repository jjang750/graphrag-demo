"""
XPERP QA 데이터 로딩 스크립트
- manuals/ 폴더의 모든 .txt QA 파일을 파싱
- QA 노드 생성 및 임베딩 생성 (기존 메뉴 데이터 유지)
- qa_vector_index 생성

실행 방법:
    .venv\\Scripts\\python.exe add_qa.py
"""

import argparse
import time

import neo4j
from gemini_embedder import GeminiEmbedder

from config import (
    NEO4J_URI, NEO4J_AUTH, GOOGLE_API_KEY,
    QA_INDEX_NAME, EMBEDDING_DIM, MANUALS_DIR,
)
from utils.qa_parser import load_all_qa


def clear_existing_qa(session):
    """기존 QA 노드만 삭제 (메뉴 데이터 유지)"""
    result = session.run("MATCH (q:QA) DETACH DELETE q RETURN count(q) AS c")
    deleted = result.single()["c"]
    if deleted > 0:
        print(f"  기존 QA {deleted}개 삭제")


def create_qa_constraint(session):
    try:
        session.run(
            "CREATE CONSTRAINT qa_id IF NOT EXISTS FOR (q:QA) REQUIRE q.id IS UNIQUE"
        )
    except Exception as e:
        print(f"  제약 조건 이미 존재: {e}")


def embed_with_retry(embedder, text, max_retries=6):
    """임베딩 생성 - 오류 종류에 따라 자동 재시도"""
    for attempt in range(max_retries):
        try:
            return embedder.embed_query(text)
        except RuntimeError as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                # 분당 한도 초과: 60초 대기 후 재시도
                wait = 60 * (attempt + 1)
                print(f"\n  ⚠️  API 한도 초과(429) - {wait}초 대기 후 재시도 ({attempt+1}/{max_retries})", flush=True)
                time.sleep(wait)
            elif "503" in err or "UNAVAILABLE" in err:
                # 서비스 일시 불가: 지수 백오프
                wait = 2 ** attempt
                print(f"\n  ⚠️  서비스 일시 불가(503) - {wait}초 후 재시도 ({attempt+1}/{max_retries})", end=" ", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"임베딩 {max_retries}회 재시도 후 실패")


def get_existing_qa_ids(session):
    """이미 임베딩이 완료된 QA ID 목록 조회 (이어서 실행용)"""
    result = session.run(
        "MATCH (q:QA) WHERE q.embedding IS NOT NULL RETURN q.id AS id"
    )
    return {record["id"] for record in result}


def insert_qa_data(session, embedder, entries):
    """QA 노드 삽입 및 임베딩 생성 (이어서 실행 지원)"""
    # 이미 처리된 항목 확인
    existing_ids = get_existing_qa_ids(session)
    skipped = 0

    total = len(entries)
    for i, entry in enumerate(entries, 1):
        qa_id = f"{entry['source']}_{i}"
        question = entry['question']
        answer = entry['answer']
        tags = entry.get('tags', [])

        # 이미 임베딩 완료된 항목은 건너뜀
        if qa_id in existing_ids:
            skipped += 1
            continue

        print(f"[{i}/{total}] {question[:45]}", end=" ... ", flush=True)

        # Q+A 합쳐서 임베딩 (문맥 포함), 503 오류 시 재시도
        embed_text = f"{question}\n{answer}"
        embedding = embed_with_retry(embedder, embed_text)

        session.run(
            """
            MERGE (qa:QA {id: $id})
            SET qa.question = $question,
                qa.answer = $answer,
                qa.tags = $tags,
                qa.source = $source,
                qa.embedding = $embedding
            """,
            id=qa_id, question=question, answer=answer,
            tags=tags, source=entry['source'], embedding=embedding,
        )

        print(f"✅ (dim={len(embedding)})")

    newly_added = total - skipped
    print(f"\n총 {total}개 중 {skipped}개 건너뜀, {newly_added}개 신규 삽입 완료")


def create_qa_index(session):
    """QA 벡터 인덱스 생성"""
    try:
        session.run(f"DROP INDEX {QA_INDEX_NAME} IF EXISTS")
    except Exception:
        pass

    session.run(
        f"""
        CREATE VECTOR INDEX {QA_INDEX_NAME} IF NOT EXISTS
        FOR (q:QA) ON q.embedding
        OPTIONS {{
            indexConfig: {{
                `vector.dimensions`: {EMBEDDING_DIM},
                `vector.similarity_function`: 'cosine'
            }}
        }}
        """
    )


def verify_qa(session):
    """QA 데이터 검증"""
    count = session.run("MATCH (q:QA) RETURN count(q) AS c").single()["c"]
    embedded = session.run(
        "MATCH (q:QA) WHERE q.embedding IS NOT NULL RETURN count(q) AS c"
    ).single()["c"]
    indexes = session.run(
        "SHOW INDEXES WHERE name = $name", name=QA_INDEX_NAME
    ).data()
    print(f"  QA 노드       : {count}개 (임베딩: {embedded}개)")
    print(f"  QA 벡터 인덱스 : {'✅ 존재' if indexes else '❌ 없음'}")


def main():
    parser = argparse.ArgumentParser(description="XPERP QA 데이터 로딩")
    parser.add_argument("--dir", default=MANUALS_DIR,
                        help=f"QA 파일 디렉터리 (기본값: {MANUALS_DIR})")
    args = parser.parse_args()

    qa_dir = args.dir

    print("=" * 60)
    print("  XPERP QA 데이터 로딩")
    print("=" * 60)
    print(f"  Neo4j URI  : {NEO4J_URI}")
    print(f"  QA Index   : {QA_INDEX_NAME}")
    print(f"  Manuals Dir: {qa_dir}/")
    print("=" * 60)

    print("\n📋 QA 파일 파싱 중...")
    all_entries = load_all_qa(qa_dir)
    print(f"\n→ 총 {len(all_entries)}개 QA 항목 로드 완료\n")

    if not all_entries:
        print("❌ 파싱된 QA 항목이 없습니다. 파일 형식을 확인하세요.")
        return

    print("📡 Gemini 임베더 초기화...")
    embedder = GeminiEmbedder(model="gemini-embedding-001", api_key=GOOGLE_API_KEY)
    print("✅ 임베더 초기화 완료\n")

    print(f"🔌 Neo4j 연결 중: {NEO4J_URI}")
    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    try:
        driver.verify_connectivity()
        print("✅ Neo4j 연결 성공\n")
    except Exception as e:
        print(f"❌ Neo4j 연결 실패: {e}")
        return

    with driver.session() as session:
        # 이미 처리된 항목 수 확인
        already_done = session.run(
            "MATCH (q:QA) WHERE q.embedding IS NOT NULL RETURN count(q) AS c"
        ).single()["c"]

        if already_done > 0:
            print(f"⏩ 이전 실행 데이터 감지: {already_done}개 이미 완료 → 이어서 실행\n")
        else:
            print("🗑️  기존 QA 노드 초기화...")
            clear_existing_qa(session)

        print("📌 QA 제약 조건 생성...")
        create_qa_constraint(session)
        print("✅ 완료\n")

        print("📥 QA 데이터 삽입 및 임베딩 생성 중...")
        insert_qa_data(session, embedder, all_entries)

        print("\n🔍 QA 벡터 인덱스 생성 중...")
        create_qa_index(session)
        print("✅ 인덱스 생성 완료\n")

        print("📊 최종 검증:")
        verify_qa(session)

    driver.close()
    print("\n🎉 QA 로딩 완료! 서버를 재시작하세요:")
    print("   .venv\\Scripts\\uvicorn app:app --host 0.0.0.0 --port 8800 --reload")


if __name__ == "__main__":
    main()
