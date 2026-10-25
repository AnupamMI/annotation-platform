"""
Real identities behind pseudonymous node IDs (e.g. patient name for patient P-0042).

Lives in the separate `restricted` schema. Only admin code paths may read it.
The LLM's database role is never granted access.
"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt


class IdentityMapping(CreatedAt, Base):
    __tablename__ = "identity_mappings"
    __table_args__ = {"schema": "restricted"}

    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True
    )
    real_identifier: Mapped[str] = mapped_column(String(500))
