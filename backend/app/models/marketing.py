from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


MAX_MARKETING_CAMPAIGNS_PER_OWNER = 50
MAX_MARKETING_STAGE_OUTPUT_CHARACTERS = 32_768


class MarketingCampaignStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    PUBLISHING = "publishing"
    AWAITING_ANALYTICS = "awaiting_analytics"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class MarketingStageKind(StrEnum):
    RESEARCH = "research"
    STRATEGY = "strategy"
    CONTENT = "content"
    CREATIVE = "creative"
    APPROVAL = "approval"
    PUBLISH = "publish"
    ANALYTICS = "analytics"
    OPTIMIZATION = "optimization"


class MarketingStageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _enum(enum_type, name: str, length: int = 32):
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda value: [member.value for member in value],
        length=length,
    )


class MarketingCampaign(Base):
    __tablename__ = "marketing_campaigns"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(name)) BETWEEN 1 AND 120",
            name="name_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(objective)) BETWEEN 1 AND 2000",
            name="objective_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(product)) BETWEEN 1 AND 500",
            name="product_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(audience)) BETWEEN 1 AND 1000",
            name="audience_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(channels_json) BETWEEN 2 AND 256",
            name="channels_json_bounded",
        ),
        CheckConstraint(
            "char_length(source_facts_json) BETWEEN 2 AND 32768",
            name="source_facts_json_bounded",
        ),
        CheckConstraint(
            "analytics_json IS NULL OR char_length(analytics_json) <= 8192",
            name="analytics_json_bounded",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'needs_approval', 'publishing', "
            "'awaiting_analytics', 'completed', 'failed', 'cancelled', 'timed_out')",
            name="status_allowed",
        ),
        CheckConstraint(
            "current_stage IS NULL OR current_stage IN ('research', 'strategy', "
            "'content', 'creative', 'approval', 'publish', 'analytics', "
            "'optimization')",
            name="current_stage_allowed",
        ),
        CheckConstraint(
            "(publisher_connector_id IS NULL AND publish_path IS NULL) OR "
            "(publisher_connector_id IS NOT NULL AND "
            "char_length(publish_path) BETWEEN 1 AND 512)",
            name="publisher_configuration_consistent",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('campaign_cancelled', "
            "'campaign_timed_out', 'agent_failed', 'verification_failed', "
            "'publisher_unavailable', 'publish_failed', 'server_restarted', "
            "'internal_failure')",
            name="error_code_allowed",
        ),
        CheckConstraint(
            "(status = 'pending' AND current_stage IS NULL AND started_at IS NULL "
            "AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'running' AND current_stage IN ('research', 'strategy', "
            "'content', 'creative') AND started_at IS NOT NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'needs_approval' AND current_stage = 'approval' AND "
            "started_at IS NOT NULL AND approved_at IS NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'publishing' AND current_stage = 'publish' AND "
            "approved_at IS NOT NULL AND completed_at IS NULL AND "
            "error_code IS NULL) OR "
            "(status = 'awaiting_analytics' AND current_stage = 'analytics' AND "
            "approved_at IS NOT NULL AND published_at IS NOT NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'completed' AND current_stage = 'optimization' AND "
            "approved_at IS NOT NULL AND published_at IS NOT NULL AND "
            "completed_at IS NOT NULL AND analytics_json IS NOT NULL AND "
            "error_code IS NULL) OR "
            "(status IN ('failed', 'cancelled', 'timed_out') AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        UniqueConstraint("id", "owner_id", name="uq_marketing_campaigns_id_owner"),
        ForeignKeyConstraint(
            ("publisher_connector_id", "owner_id"),
            ("connectors.id", "connectors.owner_id"),
            name="fk_marketing_campaigns_publisher_owner_connectors",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    objective: Mapped[str] = mapped_column(String(2000), nullable=False)
    product: Mapped[str] = mapped_column(String(500), nullable=False)
    audience: Mapped[str] = mapped_column(String(1000), nullable=False)
    channels_json: Mapped[str] = mapped_column(String(256), nullable=False)
    source_facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    publisher_connector_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    publish_path: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[MarketingCampaignStatus] = mapped_column(
        _enum(MarketingCampaignStatus, "marketing_campaign_status"),
        nullable=False,
        default=MarketingCampaignStatus.PENDING,
    )
    current_stage: Mapped[MarketingStageKind | None] = mapped_column(
        _enum(MarketingStageKind, "marketing_campaign_stage", length=24)
    )
    analytics_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stages: Mapped[list[MarketingStage]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketingStage.position",
        passive_deletes=True,
    )


class MarketingStage(Base):
    __tablename__ = "marketing_stages"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 8", name="position_bounded"),
        CheckConstraint(
            "(position = 1 AND kind = 'research') OR "
            "(position = 2 AND kind = 'strategy') OR "
            "(position = 3 AND kind = 'content') OR "
            "(position = 4 AND kind = 'creative') OR "
            "(position = 5 AND kind = 'approval') OR "
            "(position = 6 AND kind = 'publish') OR "
            "(position = 7 AND kind = 'analytics') OR "
            "(position = 8 AND kind = 'optimization')",
            name="position_kind_consistent",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'blocked', 'completed', "
            "'failed', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "output IS NULL OR char_length(output) <= 32768",
            name="output_bounded",
        ),
        CheckConstraint(
            "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
            name="output_sha256_valid",
        ),
        CheckConstraint(
            "model_id IS NULL OR char_length(model_id) BETWEEN 1 AND 96",
            name="model_id_bounded",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('campaign_cancelled', "
            "'campaign_timed_out', 'agent_failed', 'verification_failed', "
            "'publisher_unavailable', 'publish_failed', 'server_restarted', "
            "'not_run', 'internal_failure')",
            name="error_code_allowed",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND output IS NULL AND output_sha256 IS NULL AND error_code IS NULL "
            "AND duration_ms IS NULL) OR "
            "(status = 'blocked' AND kind = 'approval' AND started_at IS NULL "
            "AND completed_at IS NULL AND output IS NULL AND "
            "output_sha256 IS NULL AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND "
            "completed_at IS NULL AND output IS NULL AND output_sha256 IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL AND "
            "completed_at IS NOT NULL AND output IS NOT NULL AND "
            "output_sha256 IS NOT NULL AND error_code IS NULL AND "
            "duration_ms >= 0) OR "
            "(status IN ('failed', 'cancelled') AND completed_at IS NOT NULL "
            "AND output IS NULL AND output_sha256 IS NULL AND "
            "error_code IS NOT NULL AND duration_ms >= 0)",
            name="lifecycle_consistent",
        ),
        UniqueConstraint(
            "campaign_id", "position", name="uq_marketing_stages_position"
        ),
        ForeignKeyConstraint(
            ("campaign_id", "owner_id"),
            ("marketing_campaigns.id", "marketing_campaigns.owner_id"),
            name="fk_marketing_stages_campaign_owner_campaigns",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[MarketingStageKind] = mapped_column(
        _enum(MarketingStageKind, "marketing_stage_kind", length=24), nullable=False
    )
    status: Mapped[MarketingStageStatus] = mapped_column(
        _enum(MarketingStageStatus, "marketing_stage_status"), nullable=False
    )
    output: Mapped[str | None] = mapped_column(Text)
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(96))
    connector_execution_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("connector_executions.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)

    campaign: Mapped[MarketingCampaign] = relationship(
        back_populates="stages", lazy="raise"
    )


Index(
    "ix_marketing_campaigns_owner_created_at",
    MarketingCampaign.owner_id,
    MarketingCampaign.created_at.desc(),
)
Index(
    "ix_marketing_campaigns_owner_status",
    MarketingCampaign.owner_id,
    MarketingCampaign.status,
)
Index(
    "ix_marketing_stages_owner_campaign_position",
    MarketingStage.owner_id,
    MarketingStage.campaign_id,
    MarketingStage.position,
)
