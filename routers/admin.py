"""
Admin API 라우터
- QA 관리 (CRUD, 연결)
- 메뉴 설명/관계 관리
- 데이터 내보내기
"""

import io
import json
import re
import zipfile
from datetime import datetime
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])


# ─────────────────────────────────────────
# Pydantic 모델
# ─────────────────────────────────────────

class QAUpdateRequest(BaseModel):
    question: str
    answer: str
    tags: List[str] = []


class LinkRequest(BaseModel):
    qa_id: str
    menuitem_id: str


class DescriptionRequest(BaseModel):
    menu_id: str
    description: str
    generate_embedding: bool = False


class MenuRelationRequest(BaseModel):
    source_id: str
    target_id: str
    rel_type: Literal["관련메뉴", "선행업무", "후속업무"]
    memo: str = ""


def _driver(request: Request):
    return request.app.state.driver


def _embedder(request: Request):
    return request.app.state.embedder


# ─────────────────────────────────────────
# 메뉴
# ─────────────────────────────────────────

@router.get("/menus")
def get_menus(request: Request, q: str = Query("", description="메뉴 검색어")):
    """메뉴 트리 + 각 MenuItem의 연결 QA 수 반환"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (m:MenuItem)
                WHERE $q = '' OR m.name CONTAINS $q OR m.menu_path CONTAINS $q
                OPTIONAL MATCH (qa:QA)-[:IN_MENU]->(m)
                WITH m, count(qa) AS qa_count
                OPTIONAL MATCH (s:SubMenu)-[:HAS_ITEM]->(m)
                OPTIONAL MATCH (menu:Menu)-[:HAS_SUBMENU]->(s)
                OPTIONAL MATCH (menu2:Menu)-[:HAS_ITEM]->(m)
                RETURN
                    m.id            AS id,
                    m.name          AS name,
                    m.menu_path     AS menu_path,
                    m.main_menu     AS main_menu,
                    m.sub_menu      AS sub_menu,
                    qa_count
                ORDER BY m.main_menu, m.sub_menu, m.name
            """, q=q)
            items = [dict(r) for r in result]
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
# QA CRUD
# ─────────────────────────────────────────

@router.get("/qa")
def get_qa(
    request: Request,
    menuitem_id: Optional[str] = Query(None, description="연결된 QA 조회 (MenuItem.id)"),
    q: Optional[str] = Query(None, description="QA 검색어"),
    source: Optional[str] = Query(None, description="소스 파일 필터"),
    unlinked: bool = Query(False, description="미연결 QA만 조회"),
    limit: int = Query(50, le=200)
):
    """QA 목록 조회 — 필터: menuitem_id | 검색어 | 소스 | 미연결"""
    try:
        with _driver(request).session() as session:
            if menuitem_id:
                result = session.run("""
                    MATCH (q:QA)-[:IN_MENU]->(m:MenuItem {id: $mid})
                    RETURN q.id AS id, q.question AS question,
                           q.answer AS answer, q.tags AS tags, q.source AS source
                    ORDER BY q.id
                    LIMIT $limit
                """, mid=menuitem_id, limit=limit)
            elif unlinked:
                where = "WHERE NOT (q)-[:IN_MENU]->()"
                if source:
                    where += " AND q.source = $source"
                if q:
                    where += " AND (q.question CONTAINS $q OR q.answer CONTAINS $q)"
                result = session.run(f"""
                    MATCH (q:QA) {where}
                    RETURN q.id AS id, q.question AS question,
                           q.answer AS answer, q.tags AS tags, q.source AS source
                    ORDER BY q.id
                    LIMIT $limit
                """, q=q or "", source=source or "", limit=limit)
            else:
                where_clauses = []
                if source:
                    where_clauses.append("q.source = $source")
                if q:
                    where_clauses.append("(q.question CONTAINS $q OR q.answer CONTAINS $q)")
                where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                result = session.run(f"""
                    MATCH (q:QA) {where}
                    RETURN q.id AS id, q.question AS question,
                           q.answer AS answer, q.tags AS tags, q.source AS source
                    ORDER BY q.id
                    LIMIT $limit
                """, q=q or "", source=source or "", limit=limit)

            rows = []
            for r in result:
                rows.append({
                    "id": r["id"],
                    "question": r["question"],
                    "answer": r["answer"],
                    "tags": r["tags"] or [],
                    "source": r["source"],
                })
        return {"items": rows, "count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qa/{qa_id}/detail")
def get_qa_detail(request: Request, qa_id: str):
    """QA 단건 조회"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (q:QA {id: $qa_id})
                RETURN q.id AS id, q.question AS question,
                       q.answer AS answer, q.tags AS tags, q.source AS source
            """, qa_id=qa_id)
            row = result.single()
            if not row:
                raise HTTPException(status_code=404, detail="QA를 찾을 수 없습니다")
            return {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "tags": row["tags"] or [],
                "source": row["source"],
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/qa/{qa_id}")
def update_qa(request: Request, qa_id: str, req: QAUpdateRequest):
    """QA 질문·답변·태그 수정"""
    try:
        with _driver(request).session() as session:
            row = session.run("""
                MATCH (q:QA {id: $id})
                SET q.question = $question,
                    q.answer   = $answer,
                    q.tags     = $tags
                RETURN q.id AS id
            """, id=qa_id, question=req.question,
                 answer=req.answer, tags=req.tags).single()
        if not row:
            raise HTTPException(status_code=404, detail="QA를 찾을 수 없습니다")
        return {"status": "ok", "id": row["id"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qa/{qa_id}/menus")
def get_qa_menus(request: Request, qa_id: str):
    """특정 QA에 연결된 MenuItem 목록"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (q:QA {id: $qa_id})-[:IN_MENU]->(m:MenuItem)
                RETURN m.id AS id, m.name AS name, m.menu_path AS menu_path
            """, qa_id=qa_id)
            items = [dict(r) for r in result]
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
# QA ↔ MenuItem 연결
# ─────────────────────────────────────────

@router.post("/qa-link")
def create_link(request: Request, req: LinkRequest):
    """QA ↔ MenuItem 연결 생성"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (q:QA {id: $qa_id})
                MATCH (m:MenuItem {id: $mid})
                MERGE (q)-[r:IN_MENU]->(m)
                RETURN q.id AS qa_id, m.id AS menu_id, m.menu_path AS menu_path
            """, qa_id=req.qa_id, mid=req.menuitem_id)
            row = result.single()
            if not row:
                raise HTTPException(status_code=404, detail="QA 또는 MenuItem을 찾을 수 없습니다")
        return {"status": "ok", "qa_id": row["qa_id"], "menu_path": row["menu_path"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/qa-link")
def delete_link(request: Request, req: LinkRequest):
    """QA ↔ MenuItem 연결 해제"""
    try:
        with _driver(request).session() as session:
            session.run("""
                MATCH (q:QA {id: $qa_id})-[r:IN_MENU]->(m:MenuItem {id: $mid})
                DELETE r
            """, qa_id=req.qa_id, mid=req.menuitem_id)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
# 메뉴 설명
# ─────────────────────────────────────────

@router.get("/menu-desc-list")
def menu_desc_list(request: Request, no_desc_only: bool = Query(False)):
    """MenuItem 목록 + description/embedding 보유 여부"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (m:MenuItem)
                WHERE $no_desc_only = false
                   OR m.description IS NULL
                   OR m.description = ''
                RETURN
                    m.id          AS id,
                    m.name        AS name,
                    m.menu_path   AS menu_path,
                    m.main_menu   AS main_menu,
                    m.sub_menu    AS sub_menu,
                    m.description AS description,
                    m.embedding IS NOT NULL AS has_embedding
                ORDER BY m.main_menu, m.sub_menu, m.name
            """, no_desc_only=no_desc_only)
            items = [dict(r) for r in result]
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/menu-description")
def update_menu_description(request: Request, req: DescriptionRequest):
    """MenuItem description 저장 (옵션: Gemini 임베딩 생성)"""
    try:
        embedding = None
        if req.generate_embedding:
            if not req.description.strip():
                raise HTTPException(status_code=400, detail="임베딩 생성에는 description이 필요합니다")
            embedding = _embedder(request).embed_query(req.description)

        with _driver(request).session() as session:
            if embedding is not None:
                row = session.run("""
                    MATCH (m:MenuItem {id: $id})
                    SET m.description = $desc, m.embedding = $embedding
                    RETURN m.menu_path AS path
                """, id=req.menu_id, desc=req.description, embedding=embedding).single()
            else:
                row = session.run("""
                    MATCH (m:MenuItem {id: $id})
                    SET m.description = $desc
                    RETURN m.menu_path AS path
                """, id=req.menu_id, desc=req.description).single()

        if not row:
            raise HTTPException(status_code=404, detail="MenuItem을 찾을 수 없습니다")
        return {
            "status": "ok",
            "path": row["path"],
            "has_embedding": embedding is not None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
# 메뉴 관계
# ─────────────────────────────────────────

@router.get("/menu-relations")
def get_menu_relations(request: Request):
    """MenuItem 간 RELATED_MENU 관계 목록 조회"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (a:MenuItem)-[r:RELATED_MENU]->(b:MenuItem)
                RETURN
                    a.id AS source_id, a.name AS source_name, a.menu_path AS source_path,
                    r.type AS rel_type, r.memo AS memo,
                    b.id AS target_id, b.name AS target_name, b.menu_path AS target_path
                ORDER BY a.menu_path
            """)
            items = [dict(r) for r in result]
        return {"items": items, "count": len(items)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/menu-relation")
def create_menu_relation(request: Request, req: MenuRelationRequest):
    """MenuItem ↔ MenuItem 관계 생성"""
    if req.source_id == req.target_id:
        raise HTTPException(status_code=400, detail="소스와 대상 메뉴가 동일합니다")
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (a:MenuItem {id: $source_id}), (b:MenuItem {id: $target_id})
                MERGE (a)-[r:RELATED_MENU {type: $rel_type}]->(b)
                SET r.memo = $memo, r.created_at = datetime()
                RETURN a.menu_path AS source_path, b.menu_path AS target_path
            """, source_id=req.source_id, target_id=req.target_id,
                rel_type=req.rel_type, memo=req.memo)
            row = result.single()
            if not row:
                raise HTTPException(status_code=404, detail="MenuItem을 찾을 수 없습니다")
        return {"status": "ok", "source_path": row["source_path"], "target_path": row["target_path"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/menu-relation")
def delete_menu_relation(request: Request, req: MenuRelationRequest):
    """MenuItem ↔ MenuItem 관계 삭제"""
    try:
        with _driver(request).session() as session:
            session.run("""
                MATCH (a:MenuItem {id: $source_id})-[r:RELATED_MENU {type: $rel_type}]->(b:MenuItem {id: $target_id})
                DELETE r
            """, source_id=req.source_id, target_id=req.target_id, rel_type=req.rel_type)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────
# 내보내기 / 통계
# ─────────────────────────────────────────

@router.get("/export/menu-qa")
def export_menu_qa(request: Request):
    """메뉴별 연결된 QA를 JSON 파일로 묶어 ZIP 다운로드"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (m:MenuItem)
                OPTIONAL MATCH (q:QA)-[:IN_MENU]->(m)
                WITH m, collect(q) AS raw_qas
                WITH m, [qa IN raw_qas WHERE qa IS NOT NULL] AS qas
                WHERE size(qas) > 0
                RETURN
                    m.id        AS menu_id,
                    m.name      AS menu_name,
                    m.menu_path AS menu_path,
                    m.main_menu AS main_menu,
                    m.sub_menu  AS sub_menu,
                    [qa IN qas | {
                        id:       qa.id,
                        question: qa.question,
                        answer:   qa.answer,
                        tags:     qa.tags,
                        source:   qa.source
                    }] AS qa_items
                ORDER BY m.menu_path
            """)
            rows = [dict(r) for r in result]

        exported_at = datetime.now().isoformat()
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for row in rows:
                raw_name = row["menu_path"] or row["menu_name"] or row["menu_id"]
                safe_name = raw_name.replace(" > ", "_").replace("/", "_").replace("\\", "_")
                safe_name = re.sub(r'[<>:"|?*\s]', "_", safe_name)
                filename = f"{safe_name}.json"

                content = {
                    "menu": {
                        "id":        row["menu_id"],
                        "name":      row["menu_name"],
                        "menu_path": row["menu_path"],
                        "main_menu": row["main_menu"],
                        "sub_menu":  row["sub_menu"],
                    },
                    "qa_count":   len(row["qa_items"]),
                    "qa_items":   row["qa_items"],
                    "exported_at": exported_at,
                }
                zf.writestr(filename, json.dumps(content, ensure_ascii=False, indent=2))

        zip_buffer.seek(0)
        date_str = datetime.now().strftime("%Y%m%d")
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="menu_qa_export_{date_str}.zip"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/menu-md")
def export_menu_md(request: Request, main_menu: str = Query(..., description="내보낼 대분류명")):
    """특정 대분류의 메뉴 설명 + 관련 메뉴를 단일 Markdown 파일로 내보내기 (외부 RAG 문서용)"""
    arrow = {"선행업무": "→ 선행업무", "후속업무": "← 후속업무", "관련메뉴": "↔ 관련메뉴"}
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (m:MenuItem)
                WHERE m.main_menu = $main
                OPTIONAL MATCH (m)-[r:RELATED_MENU]->(t:MenuItem)
                WITH m, collect(CASE WHEN t IS NULL THEN NULL
                     ELSE {type: r.type, path: coalesce(t.menu_path, t.name)} END) AS outs
                OPTIONAL MATCH (m)<-[r2:RELATED_MENU {type:'관련메뉴'}]-(s:MenuItem)
                WITH m, outs, collect(CASE WHEN s IS NULL THEN NULL
                     ELSE {type: '관련메뉴', path: coalesce(s.menu_path, s.name)} END) AS ins
                RETURN
                    coalesce(m.menu_path, m.name) AS menu_path,
                    m.description AS description,
                    [x IN outs WHERE x IS NOT NULL] AS out_rels,
                    [x IN ins  WHERE x IS NOT NULL] AS in_rels
                ORDER BY m.menu_path
            """, main=main_menu)
            rows = [dict(r) for r in result]

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"# {main_menu} 메뉴 관계 문서", f"> 생성일: {now} · 대분류: {main_menu}", ""]
        section_count = 0
        for row in rows:
            desc = (row.get("description") or "").strip()
            rel_lines, seen = [], set()
            for rel in (row.get("out_rels") or []) + (row.get("in_rels") or []):
                label = arrow.get(rel["type"], "↔ 관련메뉴")
                text = f"- {label}: {rel['path']}"
                if text not in seen:
                    seen.add(text)
                    rel_lines.append(text)
            if not desc and not rel_lines:
                continue
            section_count += 1
            lines.append(f"## {row['menu_path']}")
            if desc:
                lines += ["**설명**", desc, ""]
            if rel_lines:
                lines += ["**관련 메뉴**", *rel_lines, ""]
        if section_count == 0:
            lines.append(f"_'{main_menu}' 대분류에 설명·관계가 있는 메뉴가 없습니다._")

        md = "\n".join(lines) + "\n"
        buf = io.BytesIO(md.encode("utf-8"))
        date_str = datetime.now().strftime("%Y%m%d")
        fname_ascii = f"menu_relations_{date_str}.md"
        fname_utf8 = quote(f"메뉴관계_{main_menu}_{date_str}.md")
        return StreamingResponse(
            buf, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{fname_ascii}"; filename*=UTF-8\'\'{fname_utf8}'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sources")
def get_sources(request: Request):
    """QA source 목록 + 수량"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (q:QA)
                RETURN q.source AS source, count(q) AS cnt
                ORDER BY cnt DESC
            """)
            items = [{"source": r["source"], "count": r["cnt"]} for r in result]
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review")
def review(
    request: Request,
    source: str = Query(..., description="검토할 QA 소스"),
    limit: int = Query(200, le=500),
):
    """소스별 연결된 QA + 메뉴 한 번에 조회 (오탐 검토용)"""
    try:
        with _driver(request).session() as session:
            result = session.run("""
                MATCH (q:QA {source: $source})-[:IN_MENU]->(m:MenuItem)
                WITH q, collect({
                    id: m.id,
                    name: m.name,
                    path: m.menu_path,
                    main: m.main_menu
                }) AS menus
                RETURN q.id AS id, q.question AS question,
                       q.source AS source, q.tags AS tags, menus
                ORDER BY q.id
                LIMIT $limit
            """, source=source, limit=limit)
            items = []
            for r in result:
                items.append({
                    "id": r["id"],
                    "question": r["question"],
                    "source": r["source"],
                    "tags": r["tags"] or [],
                    "menus": list(r["menus"]),
                })
        return {"items": items, "count": len(items)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
