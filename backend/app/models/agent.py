"""Every agent run: what was asked, the plan, who approved it, and what it did."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, StrEnum, UUIDPk


class AgentRunStatus(StrEnum):
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    running = "running"
    completed = "completed"
    cancelled = "cancelled"
    failed = "failed"


class AgentRun(UUIDPk, CreatedAt, Base):
    __tablename__ = "agent_runs"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # "ask", "dataset_builder", "consistency_checker", "import_assistant", ...
    kind: Mapped[str] = mapped_column(String(50), index=True)
    request: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status"), default=AgentRunStatus.planning
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Ordered record of every tool call: name, arguments, result summary, duration.
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    llm_provider: Mapped[str | None] = mapped_column(String(50))
    llm_model: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
