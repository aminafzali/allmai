"""Import registry so Alembic ``target_metadata`` sees every model."""

from app.common.base import Base  # noqa: F401
from app.admin.models import AISetting  # noqa: F401
from app.agents.definitions_models import (  # noqa: F401
    AgentDefinition,
    AgentDefinitionKnowledge,
    AgentKnowledgeAssignment,
)
from app.agents.models import Agent  # noqa: F401
from app.agents.teacher.models import LessonPlan  # noqa: F401
from app.auth.models import RefreshToken  # noqa: F401
from app.common.audit import AuditLog  # noqa: F401
from app.conversations.models import Conversation, Message  # noqa: F401
from app.knowledge.models import (  # noqa: F401
    Chunk,
    Concept,
    Document,
    KnowledgeBase,
    PageSegment,
    Relation,
    Source,
)
from app.memory.models import ConversationSummary, MemoryFact  # noqa: F401
from app.users.models import User  # noqa: F401
from app.workspaces.models import Workspace, WorkspaceMember  # noqa: F401
