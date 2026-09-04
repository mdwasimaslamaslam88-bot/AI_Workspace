"""ORM model registry imported by Alembic before reading ``Base.metadata``.

Import future modules that define mapped ``Base`` subclasses here.
"""

from app.models.asset import Asset, AssetProvenanceKind
from app.models.conversation import Conversation
from app.models.connector import (
    Connector,
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecution,
    ConnectorExecutionStatus,
    ConnectorHealthStatus,
    ConnectorKind,
)
from app.models.creative import (
    CreativeExperience,
    CreativeExperienceMode,
    CreativeExperienceStatus,
    CreativeTurn,
)
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.finance import (
    BrokerOrderRecord,
    BrokerOrderStatus,
    FinanceArtifact,
    FinanceArtifactKind,
    FinanceWorkspace,
    MarketAlert,
    MarketAlertCondition,
    MarketAlertStatus,
    MarketAssetClass,
    MarketWatchItem,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperPosition,
    TradingExecutionMode,
    TradingSafetyAction,
    TradingSafetyEvent,
    TradingSafetyPolicy,
)
from app.models.message import Message, MessageRole
from app.models.learning import (
    LearningActivity,
    LearningActivityKind,
    LearningAttempt,
    LearningEvent,
    LearningGradingMode,
    LearningLesson,
    LearningLessonStatus,
    LearningProgram,
    LearningProgramStatus,
    LearningReviewItem,
    LearningSession,
    LearningSessionStatus,
    LearningSkill,
    LearningSource,
    LearningTeachingMode,
)
from app.models.message_asset import MessageAsset
from app.models.message_citation import MessageCitation
from app.models.memory import Memory, MemoryCategory, MemorySetting
from app.models.marketing import (
    MarketingCampaign,
    MarketingCampaignStatus,
    MarketingStage,
    MarketingStageKind,
    MarketingStageStatus,
)
from app.models.tool import ToolExecution, ToolExecutionStatus
from app.models.user import User
from app.models.user_session import UserSession
from app.models.workflow import Workflow, WorkflowStatus, WorkflowStep

__all__ = [
    "Asset",
    "AssetProvenanceKind",
    "Conversation",
    "Connector",
    "ConnectorAction",
    "ConnectorAuthKind",
    "ConnectorExecution",
    "ConnectorExecutionStatus",
    "ConnectorHealthStatus",
    "ConnectorKind",
    "CreativeExperience",
    "CreativeExperienceMode",
    "CreativeExperienceStatus",
    "CreativeTurn",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "FinanceArtifact",
    "FinanceArtifactKind",
    "FinanceWorkspace",
    "BrokerOrderRecord",
    "BrokerOrderStatus",
    "MarketAlert",
    "MarketAlertCondition",
    "MarketAlertStatus",
    "MarketAssetClass",
    "MarketWatchItem",
    "LearningActivity",
    "LearningActivityKind",
    "LearningAttempt",
    "LearningEvent",
    "LearningGradingMode",
    "LearningLesson",
    "LearningLessonStatus",
    "LearningProgram",
    "LearningProgramStatus",
    "LearningReviewItem",
    "LearningSession",
    "LearningSessionStatus",
    "LearningSkill",
    "LearningSource",
    "LearningTeachingMode",
    "PaperOrder",
    "PaperOrderSide",
    "PaperOrderStatus",
    "PaperPosition",
    "TradingExecutionMode",
    "TradingSafetyAction",
    "TradingSafetyEvent",
    "TradingSafetyPolicy",
    "Message",
    "MessageAsset",
    "MessageCitation",
    "Memory",
    "MemoryCategory",
    "MemorySetting",
    "MarketingCampaign",
    "MarketingCampaignStatus",
    "MarketingStage",
    "MarketingStageKind",
    "MarketingStageStatus",
    "ToolExecution",
    "ToolExecutionStatus",
    "MessageRole",
    "User",
    "UserSession",
    "Workflow",
    "WorkflowStatus",
    "WorkflowStep",
]
