"""Import every model so Base.metadata knows all tables."""

from app.models.agent import AgentRun, AgentRunStatus
from app.models.auth import AuthSession
from app.models.annotation import (
    Annotation,
    AnnotationSource,
    ImportBatch,
    ImportStatus,
    ShapeType,
)
from app.models.base import Base
from app.models.core import (
    HierarchyLevel,
    Item,
    ItemStatus,
    LabelClass,
    Node,
    Project,
    Role,
    User,
)
from app.models.docs import Document, DocumentChunk
from app.models.dataset import Dataset, DatasetAnnotation, DatasetItem, Split
from app.models.restricted import IdentityMapping
from app.models.work import Event, Review, ReviewVerdict, Task, TaskStage, UserSession

__all__ = [
    "AgentRun", "AgentRunStatus", "AuthSession", "Annotation", "AnnotationSource", "Base", "Dataset",
    "DatasetAnnotation", "DatasetItem", "Document", "DocumentChunk", "Event", "HierarchyLevel", "IdentityMapping",
    "ImportBatch", "ImportStatus", "Item", "ItemStatus", "LabelClass", "Node", "Project",
    "Review", "ReviewVerdict", "Role", "ShapeType", "Split", "Task", "TaskStage", "User",
    "UserSession",
]
