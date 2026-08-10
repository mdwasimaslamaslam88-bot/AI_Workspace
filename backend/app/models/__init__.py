"""ORM model registry imported by Alembic before reading ``Base.metadata``.

Import future modules that define mapped ``Base`` subclasses here.
"""

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.user import User

__all__ = ["Conversation", "Message", "MessageRole", "User"]
