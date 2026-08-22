"""ORM model registry imported by Alembic before reading ``Base.metadata``.

Import future modules that define mapped ``Base`` subclasses here.
"""

from app.models.asset import Asset
from app.models.conversation import Conversation
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.message import Message, MessageRole
from app.models.message_asset import MessageAsset
from app.models.message_citation import MessageCitation
from app.models.memory import Memory, MemoryCategory, MemorySetting
from app.models.user import User

__all__ = [
    "Asset",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "Message",
    "MessageAsset",
    "MessageCitation",
    "Memory",
    "MemoryCategory",
    "MemorySetting",
    "MessageRole",
    "User",
]
