"""
Run a question's SQL safely.

Layers of protection (any one of them alone would stop a write):
1. Text check: one statement, starting with SELECT or WITH, no write keywords.
2. The transaction is READ ONLY.
3. It runs as role `ask_readonly`, which can read the ask.* views and nothing else.
4. A statement timeout and a row limit.
Results are scoped to one project through the app.project_id setting the views read.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import engine

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|grant|revoke|truncate|copy|call|do|vacuum|analyze|reindex|"
    # set_config / current_setting: the project scope lives in a setting, so queries may not touch settings at all
    r"cluster|lock|listen|notify|prepare|execute|reset|set|security|refresh|import|load|dblink|"
    r"set_config|current_setting|pg_\w+|lo_\w+|query_to_xml\w*|table_to_xml\w*|xpath\w*)\b",
    re.I,
)


class UnsafeQuery(ValueError):
    pass


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list]
    truncated: bool


def check(sql: str) -> str:
    """Returns the cleaned SQL or raises UnsafeQuery with a reason a person can read."""
    s = re.sub(r"--[^\n]*", " ", sql)  # strip comments
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S).strip().rstrip(";").strip()
    if not s:
        raise UnsafeQuery("The query is empty.")
    if ";" in s:
        raise UnsafeQuery("Only one statement is allowed.")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise UnsafeQuery("Only SELECT queries are allowed.")
    # keywords inside quoted strings are harmless, so look at the query without them
    # dollar quoting and backslash escapes could hide text from the keyword check below
    if "$" in s or "\\" in s:
        raise UnsafeQuery("Dollar quoting and backslashes are not allowed in a question query.")
    if re.search(r"\bu&\s*[\"']", s, re.I):
        raise UnsafeQuery("Unicode-escaped names are not allowed.")
    # one left-to-right pass, so a quote inside a "quoted name" can't start a fake string;
    # quoted names keep their text (so "set_config" is still caught)
    unquoted = re.sub(
        r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"",
        lambda m: "''" if m.group(0)[0] == "'" else " " + m.group(0)[1:-1].replace("'", " ").replace('"', " ") + " ",
        s,
    )
    if "'" in unquoted.replace("''", "") or unquoted.count('"'):
        raise UnsafeQuery("The query has an unclosed quote.")
    if m := FORBIDDEN.search(unquoted):
        raise UnsafeQuery(f'"{m.group(0).upper()}" is not allowed in a question query.')
    if len(s) > 6000:
        raise UnsafeQuery("The query is too long.")
    return s


def _cell(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return v


async def run(project_id: uuid.UUID, sql: str) -> QueryResult:
    s = get_settings()
    clean = check(sql)
    limit = s.ask_row_limit
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = {int(s.ask_timeout_ms)}"))
            await conn.execute(text("SELECT set_config('app.project_id', :p, true)"), {"p": str(project_id)})
            await conn.execute(text("SET LOCAL ROLE ask_readonly"))
            try:
                # the query becomes a subquery, so it can't smuggle in a second statement or skip the limit
                res = await conn.execute(text(f"SELECT * FROM ({clean}) AS q LIMIT {limit + 1}"))
            except DBAPIError as exc:
                msg = str(exc.orig).split("\n")[0] if exc.orig else "query failed"
                if "statement timeout" in msg or "canceling statement" in msg:
                    raise UnsafeQuery("The query took too long and was stopped.") from exc
                if "permission denied" in msg:
                    raise UnsafeQuery("Questions can only read the ask.* views.") from exc
                raise UnsafeQuery(f"The query failed: {msg.split(':', 1)[-1].strip()}") from exc
            rows = [[_cell(v) for v in r] for r in res.fetchall()]
            cols = list(res.keys())
    return QueryResult(clean, cols, rows[:limit], len(rows) > limit)
