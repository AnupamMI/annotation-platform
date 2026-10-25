"""Tasks, reviews, sessions and the event log that powers analytics."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, StrEnum, UUIDPk


class TaskStage(StrEnum):
    annotate = "annotate"
    review = "review"
    done = "done"


class Task(UUIDPk, CreatedAt, Base):
    """A unit of assigned work: one node (e.g. a slide) or one item (e.g. a tile)."""

    __tablename__ = "tasks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    stage: Mapped[TaskStage] = mapped_column(
        Enum(TaskStage, name="task_stage"), default=TaskStage.annotate, index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ReviewVerdict(StrEnum):
    approved = "approved"
    rejected = "rejected"


class Review(UUIDPk, CreatedAt, Base):
    __tablename__ = "reviews"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    verdict: Mapped[ReviewVerdict] = mapped_column(Enum(ReviewVerdict, name="review_verdict"))
    comment: Mapped[str | None] = mapped_column(Text)


class UserSession(UUIDPk, Base):
    """A login session. Ended on logout or after inactivity."""

    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client: Mapped[str | None] = mapped_column(String(200))


class Event(CreatedAt, Base):
    """
    Append-only activity log. Every meaningful action and every active-time heartbeat.
    Analytics (time per item, throughput, progress) are computed from here.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_project_created", "project_id", "created_at"),
        Index("ix_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="SET NULL")
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("items.id", ondelete="SET NULL"), index=True
    )
    # e.g. "item.opened", "annotation.created", "heartbeat", "review.submitted"
    action: Mapped[str] = mapped_column(String(100), index=True)
    # Active (non-idle) milliseconds covered by this event; used for time-per-item.
    active_ms: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
