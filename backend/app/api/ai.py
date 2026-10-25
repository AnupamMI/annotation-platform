"""Ask, agents, datasets and guideline documents."""

import re
import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import agents, guidelines, llm
from app.ai.ask import ask as run_ask
from app.ai.ask import run_out
from app.api.deps import current_user, get_project, require
from app.db import get_session
from app.models import AgentRun, AgentRunStatus, Dataset, DatasetItem, Event, User
from app.models.docs import Document, DocumentChunk
from app.schemas import ApiModel
from app.storage import get_storage

router = APIRouter(tags=["ask and agents"])

MAX_DOC_MB = 25
DOC_TYPES = (".md", ".txt", ".pdf")


class AskIn(ApiModel):
    question: str


@router.get("/ai/status")
async def ai_status():
    d = llm.describe()
    return {"provider": d["provider"], "model": d["model"], "freeForm": llm.configured()}


@router.post("/projects/{project_id}/ask")
async def ask(project_id: uuid.UUID, body: AskIn, session: AsyncSession = Depends(get_session),
              user: User = Depends(current_user)):
    project = await get_project(project_id, session)
    try:
        return await run_ask(session, project, user, body.question)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _run_for(run_id: uuid.UUID, session: AsyncSession) -> AgentRun:
    run = await session.get(AgentRun, run_id)
    if not run or run.kind not in agents.RUNNERS:
        raise HTTPException(404, "Plan not found.")
    return run


@router.get("/agent-runs/{run_id}")
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return run_out(await _run_for(run_id, session))


@router.get("/projects/{project_id}/agent-runs")
async def list_runs(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(AgentRun).where(AgentRun.project_id == project_id).order_by(AgentRun.created_at.desc()).limit(50)
    )).scalars().all()
    return [run_out(r) if r.kind in agents.RUNNERS else
            {"id": str(r.id), "kind": r.kind, "request": r.request, "status": r.status.value, "result": r.result,
             "createdAt": r.created_at.isoformat()} for r in rows]


@router.post("/agent-runs/{run_id}/approve")
async def approve(run_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    run = await _run_for(run_id, session)
    try:
        run = await agents.approve(session, run, user)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except agents.AgentProblem as exc:
        raise HTTPException(409, str(exc)) from exc
    return run_out(run)


@router.post("/agent-runs/{run_id}/cancel")
async def cancel(run_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    run = await _run_for(run_id, session)
    if run.user_id != user.id and user.role.value != "admin":
        raise HTTPException(403, "Only the person who asked, or an admin, can cancel this plan.")
    if run.status != AgentRunStatus.awaiting_approval:
        raise HTTPException(409, "Only a plan that is waiting for approval can be cancelled.")
    run.status = AgentRunStatus.cancelled
    await session.commit()
    return run_out(run)


# ---------------- datasets ----------------
@router.get("/projects/{project_id}/datasets")
async def list_datasets(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.created_at.desc()))).scalars().all()
    out = []
    for d in rows:
        splits = (await session.execute(select(DatasetItem.split).where(DatasetItem.dataset_id == d.id))).scalars().all()
        out.append({"id": str(d.id), "name": d.name, "format": d.export_format, "createdAt": d.created_at.isoformat(),
                    "images": len(splits), "splits": {s: sum(1 for x in splits if x.value == s) for s in ("train", "val", "test")},
                    "download": f"/api/datasets/{d.id}/download" if d.export_key else None})
    return out


@router.get("/datasets/{dataset_id}/download")
async def download_dataset(dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                           user: User = Depends(require("reviewer"))):
    d = await session.get(Dataset, dataset_id)
    if not d or not d.export_key:
        raise HTTPException(404, "Dataset not found.")
    data = get_storage().get(d.export_key)
    session.add(Event(project_id=d.project_id, user_id=user.id, action="export.created", payload={"detail": f"dataset {d.name}"}))
    await session.commit()
    name = PurePosixPath(d.export_key).name
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------- guideline documents ----------------
def _doc_out(d: Document, sections: int) -> dict:
    return {"id": str(d.id), "title": d.title, "fileName": d.file_name, "sections": sections, "createdAt": d.created_at.isoformat()}


@router.get("/projects/{project_id}/documents")
async def list_documents(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    docs = await guidelines.documents(session, project_id)
    out = []
    for d in docs:
        n = len((await session.execute(select(DocumentChunk.id).where(DocumentChunk.document_id == d.id))).scalars().all())
        out.append(_doc_out(d, n))
    return out


@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_document(project_id: uuid.UUID, file: UploadFile = File(...), title: str = Form(""),
                          session: AsyncSession = Depends(get_session), user: User = Depends(require("reviewer"))):
    await get_project(project_id, session)
    name = PurePosixPath(file.filename or "guideline.txt").name
    if not name.lower().endswith(DOC_TYPES):
        raise HTTPException(422, "Upload a Markdown, text or PDF file.")
    data = await file.read(MAX_DOC_MB * 1024 * 1024 + 1)
    if len(data) > MAX_DOC_MB * 1024 * 1024:
        raise HTTPException(413, f"Guideline files can be up to {MAX_DOC_MB} MB.")
    doc = Document(project_id=project_id, title=(title.strip() or re.sub(r"\.[^.]+$", "", name))[:300], file_name=name,
                   storage_key="", uploaded_by=user.id)
    session.add(doc)
    await session.flush()
    doc.storage_key = f"projects/{project_id}/documents/{doc.id}/{re.sub(r'[^A-Za-z0-9._-]+', '-', name)}"
    try:
        n = await guidelines.add(session, doc, data)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    get_storage().put(doc.storage_key, data, file.content_type or "application/octet-stream")
    session.add(Event(project_id=project_id, user_id=user.id, action="guideline.added", payload={"detail": doc.title}))
    await session.commit()
    return _doc_out(doc, n)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                          user: User = Depends(require("reviewer"))):
    doc = await session.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    key = doc.storage_key
    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    session.add(Event(project_id=doc.project_id, user_id=user.id, action="guideline.removed", payload={"detail": doc.title}))
    await session.delete(doc)
    await session.commit()
    try:
        get_storage().delete(key)
    except Exception:
        pass
    return Response(status_code=204)
