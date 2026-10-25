"""
Ask: a question in plain words -> an answer from the project's data.

Routing:
1. Requests for an agent (build a dataset, run a consistency check) become a plan
   that waits for approval. This never depends on the language model.
2. With a language model configured, it writes one read-only query against the
   ask.* views (or picks a guideline search). The query goes through sql.run, so
   the same safety layers apply whatever the model writes. One retry on error.
3. Without a model (or if it is unreachable), common questions are answered by
   fixed queries, and guideline questions by full-text search.

Every question is logged in agent_runs (kind "ask") with the query that ran.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import agents, guidelines, llm, sql
from app.ai.views import SCHEMA_DOC
from app.models import AgentRun, AgentRunStatus, Project, User

AGENT_NAMES = {"dataset_builder": "Dataset builder", "consistency_checker": "Consistency checker"}


@dataclass
class Answer:
    text: str
    sql: str | None = None
    columns: list[str] | None = None
    rows: list[list] | None = None
    truncated: bool = False
    sources: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    via: str = "rules"

    def out(self) -> dict:
        table = None
        if self.columns is not None:
            # image ids become links; the table shows readable columns only
            keep = [i for i, c in enumerate(self.columns) if not c.endswith("_id")]
            table = {"cols": [_title(self.columns[i]) for i in keep], "rows": [[r[i] for i in keep] for r in self.rows or []],
                     "truncated": self.truncated}
            if "image_id" in self.columns and not self.links:
                ii = self.columns.index("image_id")
                ni = self.columns.index("image_name") if "image_name" in self.columns else None
                seen = set()
                for r in self.rows or []:
                    if r[ii] and r[ii] not in seen and len(seen) < 12:
                        seen.add(r[ii])
                        self.links.append({"label": f"Open {r[ni] if ni is not None else 'image'}", "itemId": r[ii]})
        return {"kind": "answer", "text": self.text, "sql": self.sql, "table": table, "sources": self.sources,
                "links": self.links, "via": self.via}


# ---------------- entry point ----------------
async def ask(session: AsyncSession, project: Project, user: User, question: str) -> dict:
    q = question.strip()
    if not q:
        raise ValueError("Type a question first.")
    if len(q) > 2000:
        raise ValueError("Keep questions under 2000 characters.")

    agent = agent_for(q)
    if agent:
        run = await agents.PLANNERS[agent](session, project, user, q)
        return {"kind": "plan", "run": run_out(run)}

    t0 = time.monotonic()
    calls: list[dict] = []
    answer = None
    if llm.configured():
        try:
            answer = await _with_llm(session, project, q, calls)
        except (llm.LLMUnavailable, ValueError) as exc:  # unreachable, or a reply that isn't usable JSON
            calls.append({"tool": "language_model", "error": str(exc)[:200]})
    if answer is None:
        answer = await _with_rules(session, project, q, calls)

    d = llm.describe()
    session.add(AgentRun(
        project_id=project.id, user_id=user.id, kind="ask", request=q, status=AgentRunStatus.completed, plan={},
        tool_calls=calls + [{"tool": "total", "ms": int((time.monotonic() - t0) * 1000)}],
        result={"text": answer.text, "sql": answer.sql, "via": answer.via},
        llm_provider=d["provider"] if answer.via == "model" else "none", llm_model=d["model"] if answer.via == "model" else None,
    ))
    await session.commit()
    return answer.out()


def agent_for(q: str) -> str | None:
    s = q.lower()
    if re.search(r"\b(build|make|create|prepare|export|generate)\b.*\b(dataset|training set|train(ing)? data|split)\b", s) or \
            re.search(r"\b(train|training)\b.*\bsplit\b|\bsplit\b.*\bby (patient|slide|case)\b", s):
        return "dataset_builder"
    if re.search(r"consisten|\b(run|do)\b.*\b(check|audit)\b|\baudit\b|\bqc\b|quality check", s):
        return "consistency_checker"
    return None


# ---------------- without a model ----------------
RULE_QUERIES = {
    "progress": (
        r"\b(left|remain|remaining|progress|how many|status|done|finished|to do)\b",
        "SELECT status, count(*) AS images FROM ask.images GROUP BY status ORDER BY images DESC",
    ),
    "time": (
        r"\b(time|slow|slowest|fast|fastest|per image|how long|speed)\b",
        """SELECT user_name AS annotator, count(*) AS images, round(avg(active_minutes), 2) AS avg_active_minutes
FROM ask.activity
WHERE action = 'item.submitted' AND happened_at > now() - interval '14 days'
GROUP BY user_name
ORDER BY avg_active_minutes DESC""",
    ),
    "warnings": (
        r"\b(warning|warnings|rule|rules|outside|inside|containment|broken|break)\b",
        """SELECT image_id, image_name, count(*) AS shapes_to_check
FROM ask.rule_problems
GROUP BY image_id, image_name
ORDER BY shapes_to_check DESC, image_name""",
    ),
    "labels": (
        r"\b(labels?|classes|class counts?|how many (cells|nuclei))\b",
        "SELECT label, count(*) AS annotations FROM ask.annotations GROUP BY label ORDER BY annotations DESC",
    ),
    "reviews": (
        r"\b(reject|rejected|rejections|review(ed|s)?|approved)\b",
        """SELECT image_id, image_name, reviewer, verdict, comment, reviewed_at
FROM ask.reviews ORDER BY reviewed_at DESC LIMIT 20""",
    ),
}

GUIDELINE_WORDS = r"\b(guideline|guidelines|protocol|sop|manual|according to|how (should|do) (i|we)|when (should|do) (i|we)|define|definition)\b"


async def _with_rules(session: AsyncSession, project: Project, q: str, calls: list[dict]) -> Answer:
    s = q.lower()
    if re.search(GUIDELINE_WORDS, s):
        return await _guideline(session, project, q, calls, use_model=False)
    for name in ("warnings", "reviews", "time", "labels", "progress"):
        pattern, query = RULE_QUERIES[name]
        if re.search(pattern, s):
            res = await _run(project, query, calls)
            return Answer(_describe(name, res), res.sql, res.columns, res.rows, res.truncated)
    # last try: maybe it's in the guidelines
    hits = await guidelines.search(session, project.id, q, limit=3)
    if hits:
        return await _guideline(session, project, q, calls, use_model=False, hits=hits)
    note = "" if llm.configured() else " Free-form questions need a language model, which isn't set up on this server yet (see LLM_PROVIDER in .env)."
    return Answer(
        "I can answer questions about progress, time per annotator, labels, reviews and rule warnings, look things up in "
        f"your guidelines, and run agents that build datasets or check consistency.{note}"
    )


def _describe(name: str, r: sql.QueryResult) -> str:
    rows = r.rows
    if name == "progress":
        counts = {row[0]: row[1] for row in rows}
        total = sum(counts.values())
        need = sum(counts.get(k, 0) for k in ("new", "in_progress", "rejected"))
        rev = counts.get("in_review", 0)
        return f"{need} of {total} images still need annotation, {rev} {'is' if rev == 1 else 'are'} waiting for review and {counts.get('done', 0)} {'is' if counts.get('done', 0) == 1 else 'are'} approved."
    if name == "time":
        if not rows:
            return "No images were submitted in the last 14 days."
        return (f"Over the last 14 days, average active time per submitted image ranges from {rows[-1][2]} to {rows[0][2]} minutes. "
                "Time stops counting after a minute without input, so breaks are not included.")
    if name == "warnings":
        return (f"{len(rows)} {'image has' if len(rows) == 1 else 'images have'} shapes that break a containment rule."
                if rows else "No image breaks a containment rule right now.")
    if name == "labels":
        return f"There are {sum(r[1] for r in rows)} current annotations across {len(rows)} labels." if rows else "There are no annotations yet."
    if name == "reviews":
        rej = sum(1 for r in rows if r[3] == "rejected")
        return f"The {len(rows)} most recent reviews: {len(rows) - rej} approved, {rej} rejected." if rows else "No reviews yet."
    return f"{len(rows)} rows."


# ---------------- with a model ----------------
ROUTER_PROMPT = f"""You answer questions about one image-annotation project by writing PostgreSQL queries.
You can read only these views (already limited to the current project):

{SCHEMA_DOC}

Reply with one JSON object and nothing else, one of:
{{"type": "sql", "sql": "<one SELECT query using only the ask.* views>"}}
{{"type": "guideline", "query": "<search words>"}}   -- the question is about how to annotate, definitions, or the lab's protocol
{{"type": "other", "reply": "<one or two sentences>"}}   -- the question can't be answered from this data

Rules for SQL: one SELECT (or WITH) statement; only ask.* views; no other schemas or tables; include image_id and image_name when listing images;
prefer readable columns; aggregate where it helps; add ORDER BY; at most 200 rows. "done" means approved by a reviewer.
Never guess labels or diagnoses: you report data, people decide."""

ANSWER_PROMPT = """Answer the question in one to three plain sentences using only the query result given.
Use exact numbers from the result. Don't mention SQL, views or columns. If the result is empty, say so plainly.
Don't give medical opinions or suggest what a label should be."""


async def _with_llm(session: AsyncSession, project: Project, q: str, calls: list[dict]) -> Answer:
    t0 = time.monotonic()
    route = llm.extract_json(await llm.complete(ROUTER_PROMPT, q, max_tokens=700))
    calls.append({"tool": "language_model", "args": {"step": "route"}, "ms": _ms(t0), "summary": route.get("type")})
    kind = route.get("type")
    if kind == "guideline":
        return await _guideline(session, project, str(route.get("query") or q), calls, use_model=True, question=q)
    if kind == "other":
        return Answer(str(route.get("reply") or "I can't answer that from this project's data."), via="model")
    if kind != "sql" or not isinstance(route.get("sql"), str):
        raise llm.LLMUnavailable("The model's reply wasn't usable.")

    query = route["sql"]
    try:
        res = await _run(project, query, calls)
    except sql.UnsafeQuery as exc:
        # one retry, telling the model what went wrong
        t1 = time.monotonic()
        retry = llm.extract_json(await llm.complete(
            ROUTER_PROMPT, f"{q}\n\nYour previous query was rejected: {exc}\nPrevious query:\n{query}\nReply with a corrected JSON object.",
            max_tokens=700))
        calls.append({"tool": "language_model", "args": {"step": "retry"}, "ms": _ms(t1)})
        if retry.get("type") != "sql" or not isinstance(retry.get("sql"), str):
            return Answer(f"I couldn't answer that safely: {exc}", sql=query, via="model")
        try:
            res = await _run(project, retry["sql"], calls)
        except sql.UnsafeQuery as exc2:
            return Answer(f"I couldn't answer that safely: {exc2}", sql=retry["sql"], via="model")

    t2 = time.monotonic()
    preview = {"columns": res.columns, "rows": res.rows[:40], "total_rows": len(res.rows), "truncated": res.truncated}
    try:
        text = (await llm.complete(ANSWER_PROMPT, f"Question: {q}\nResult: {json.dumps(preview, default=str)}", max_tokens=300)).strip()
    except llm.LLMUnavailable:
        text = f"{len(res.rows)} row{'s' if len(res.rows) != 1 else ''}{' (first ' + str(len(res.rows)) + ' shown)' if res.truncated else ''}."
    calls.append({"tool": "language_model", "args": {"step": "phrase"}, "ms": _ms(t2)})
    return Answer(text, res.sql, res.columns, res.rows, res.truncated, via="model")


# ---------------- guidelines ----------------
async def _guideline(session, project, query, calls, use_model, question=None, hits=None) -> Answer:
    t0 = time.monotonic()
    hits = hits if hits is not None else await guidelines.search(session, project.id, query, limit=4)
    calls.append({"tool": "search_guidelines", "args": {"query": query}, "ms": _ms(t0), "summary": f"{len(hits)} sections"})
    if not hits:
        docs = await guidelines.documents(session, project.id)
        return Answer(
            "I didn't find that in the uploaded guidelines." if docs
            else "No guidelines have been uploaded for this project yet. An admin or reviewer can add them in Setup.",
            via="model" if use_model else "rules")
    sources = [{"title": h["title"], "section": h["section"], "page": h["page"], "excerpt": _excerpt(h["text"])} for h in hits]
    if use_model:
        context = "\n\n".join(f"[{i + 1}] {h['title']}, {h['section']}{', page ' + str(h['page']) if h['page'] else ''}:\n{h['text'][:1500]}"
                              for i, h in enumerate(hits))
        try:
            text = (await llm.complete(
                "Answer the annotator's question using only the guideline excerpts given. Quote or closely paraphrase them and cite "
                "the excerpt number like [1]. If they don't answer it, say the guidelines don't cover it. Never decide a label for them.",
                f"Question: {question or query}\n\nExcerpts:\n{context}", max_tokens=400)).strip()
            return Answer(text, sources=sources, via="model")
        except llm.LLMUnavailable:
            pass
    top = hits[0]
    return Answer(f"From {top['title']}, {top['section']}: “{_excerpt(top['text'], 420)}”", sources=sources)


# ---------------- helpers ----------------
async def _run(project: Project, query: str, calls: list[dict]) -> sql.QueryResult:
    t0 = time.monotonic()
    try:
        res = await sql.run(project.id, query)
    except sql.UnsafeQuery as exc:
        calls.append({"tool": "run_query", "args": {"sql": query}, "ms": _ms(t0), "error": str(exc)})
        raise
    calls.append({"tool": "run_query", "args": {"sql": res.sql}, "ms": _ms(t0), "summary": f"{len(res.rows)} rows"})
    return res


def run_out(run: AgentRun) -> dict:
    plan = run.plan or {}
    return {
        "id": str(run.id), "kind": run.kind, "agent": AGENT_NAMES.get(run.kind, run.kind), "status": run.status.value,
        "request": run.request, "summary": plan.get("summary", ""), "steps": plan.get("steps", []), "warning": plan.get("warning"),
        "result": run.result or {}, "error": run.error, "toolCalls": run.tool_calls or [],
        "createdAt": run.created_at.isoformat() if run.created_at else None, "userId": str(run.user_id) if run.user_id else None,
        "approvedBy": str(run.approved_by) if run.approved_by else None,
    }


def _excerpt(t: str, n: int = 300) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n].rsplit(" ", 1)[0] + "…"


def _title(col: str) -> str:
    return col.replace("_", " ").capitalize()


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)

