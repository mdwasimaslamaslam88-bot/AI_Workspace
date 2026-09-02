from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.runtime import ConnectorRuntime
from app.connectors.service import ConnectorPermissionError, ConnectorService
from app.models.connector import ConnectorAction
from app.models.marketing import (
    MAX_MARKETING_CAMPAIGNS_PER_OWNER,
    MarketingCampaign,
    MarketingCampaignStatus,
    MarketingStage,
    MarketingStageKind,
    MarketingStageStatus,
)
from app.repositories.connector import ConnectorRepository
from app.repositories.marketing import MarketingCampaignRepository


MARKETING_CHANNELS = frozenset({"email", "social", "search", "web"})
MARKETING_STAGE_ORDER = tuple(MarketingStageKind)


class MarketingCampaignNotFoundError(RuntimeError):
    """The campaign does not exist or belongs to another owner."""


class MarketingCampaignConflictError(RuntimeError):
    """The campaign cannot perform the requested lifecycle transition."""


class MarketingCampaignInputError(ValueError):
    """The campaign definition or source data violates a fixed contract."""


@dataclass(frozen=True, slots=True)
class MarketingSourceFact:
    source_reference: str
    fact: str


@dataclass(frozen=True, slots=True)
class MarketingAnalyticsInput:
    source_reference: str
    observed_at: datetime
    impressions: int
    clicks: int
    conversions: int
    spend_minor: int
    revenue_minor: int


@dataclass(frozen=True, slots=True)
class MarketingStageView:
    id: UUID
    position: int
    kind: MarketingStageKind
    status: MarketingStageStatus
    output: str | None
    output_sha256: str | None
    model_id: str | None
    connector_execution_id: UUID | None
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class MarketingCampaignView:
    id: UUID
    name: str
    objective: str
    product: str
    audience: str
    channels: tuple[str, ...]
    source_facts: tuple[MarketingSourceFact, ...]
    publisher_connector_id: UUID | None
    publish_path: str | None
    status: MarketingCampaignStatus
    current_stage: MarketingStageKind | None
    analytics: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    approved_at: datetime | None
    published_at: datetime | None
    completed_at: datetime | None
    stages: tuple[MarketingStageView, ...]


def canonical_json(value: Any, maximum: int) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise MarketingCampaignInputError("marketing data is invalid") from exc
    if len(encoded) > maximum:
        raise MarketingCampaignInputError("marketing data exceeds its bound")
    return encoded


def output_digest(output: str) -> str:
    return hashlib.sha256(output.encode("utf-8")).hexdigest()


def _exact_text(value: str, maximum: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 and character not in "\n\t" for character in value)
    ):
        raise MarketingCampaignInputError(f"marketing {field} is invalid")
    return value


def _source_payload(sources: tuple[MarketingSourceFact, ...]) -> list[dict[str, str]]:
    if not 1 <= len(sources) <= 16:
        raise MarketingCampaignInputError("marketing source count is invalid")
    values: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        if not isinstance(source, MarketingSourceFact):
            raise MarketingCampaignInputError("marketing source is invalid")
        reference = _exact_text(source.source_reference, 512, "source reference")
        fact = _exact_text(source.fact, 2000, "source fact")
        identity = (reference, fact)
        if identity in seen:
            raise MarketingCampaignInputError("marketing sources must be unique")
        seen.add(identity)
        values.append({"source_reference": reference, "fact": fact})
    return values


def _sources_from_json(encoded: str) -> tuple[MarketingSourceFact, ...]:
    payload = json.loads(encoded)
    if not isinstance(payload, list):
        raise RuntimeError("persisted marketing sources are invalid")
    return tuple(
        MarketingSourceFact(
            source_reference=item["source_reference"],
            fact=item["fact"],
        )
        for item in payload
    )


def _stage_view(stage: MarketingStage) -> MarketingStageView:
    return MarketingStageView(
        id=stage.id,
        position=stage.position,
        kind=stage.kind,
        status=stage.status,
        output=stage.output,
        output_sha256=stage.output_sha256,
        model_id=stage.model_id,
        connector_execution_id=stage.connector_execution_id,
        error_code=stage.error_code,
        started_at=stage.started_at,
        completed_at=stage.completed_at,
        duration_ms=stage.duration_ms,
    )


def campaign_view(campaign: MarketingCampaign) -> MarketingCampaignView:
    channels = json.loads(campaign.channels_json)
    if not isinstance(channels, list):
        raise RuntimeError("persisted marketing channels are invalid")
    analytics = (
        json.loads(campaign.analytics_json)
        if campaign.analytics_json is not None
        else None
    )
    return MarketingCampaignView(
        id=campaign.id,
        name=campaign.name,
        objective=campaign.objective,
        product=campaign.product,
        audience=campaign.audience,
        channels=tuple(channels),
        source_facts=_sources_from_json(campaign.source_facts_json),
        publisher_connector_id=campaign.publisher_connector_id,
        publish_path=campaign.publish_path,
        status=campaign.status,
        current_stage=campaign.current_stage,
        analytics=analytics,
        error_code=campaign.error_code,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        started_at=campaign.started_at,
        approved_at=campaign.approved_at,
        published_at=campaign.published_at,
        completed_at=campaign.completed_at,
        stages=tuple(_stage_view(stage) for stage in campaign.stages),
    )


class MarketingCampaignService:
    def __init__(
        self,
        session: AsyncSession,
        connector_runtime: ConnectorRuntime | None = None,
    ) -> None:
        self.session = session
        self.repository = MarketingCampaignRepository(session)
        self.connector_runtime = connector_runtime

    async def create_for_owner(
        self,
        owner_id: UUID,
        *,
        name: str,
        objective: str,
        product: str,
        audience: str,
        channels: tuple[str, ...],
        source_facts: tuple[MarketingSourceFact, ...],
        publisher_connector_id: UUID | None,
        publish_path: str | None,
    ) -> MarketingCampaignView:
        name = _exact_text(name, 120, "name")
        objective = _exact_text(objective, 2000, "objective")
        product = _exact_text(product, 500, "product")
        audience = _exact_text(audience, 1000, "audience")
        if (
            not isinstance(channels, tuple)
            or not 1 <= len(channels) <= len(MARKETING_CHANNELS)
            or len(set(channels)) != len(channels)
            or any(channel not in MARKETING_CHANNELS for channel in channels)
        ):
            raise MarketingCampaignInputError("marketing channels are invalid")
        source_payload = _source_payload(source_facts)
        sources_json = canonical_json(source_payload, 32_768)
        channels_json = canonical_json(sorted(channels), 256)
        if (publisher_connector_id is None) != (publish_path is None):
            raise MarketingCampaignInputError(
                "publisher connector and path must be configured together"
            )
        try:
            count = await self.repository.lock_owner_and_count_campaigns(owner_id)
            if count is None:
                raise MarketingCampaignNotFoundError("campaign owner not found")
            if count >= MAX_MARKETING_CAMPAIGNS_PER_OWNER:
                raise MarketingCampaignConflictError("campaign history is full")
            if publisher_connector_id is not None:
                if self.connector_runtime is None:
                    raise MarketingCampaignInputError(
                        "publisher connector runtime is unavailable"
                    )
                connector = await ConnectorRepository(self.session).get_for_owner(
                    owner_id, publisher_connector_id
                )
                if connector is None:
                    raise MarketingCampaignInputError("publisher connector is invalid")
                try:
                    ConnectorService._authorize(
                        connector,
                        "POST",
                        publish_path or "",
                        action=ConnectorAction.EXECUTE,
                    )
                except ConnectorPermissionError as exc:
                    raise MarketingCampaignInputError(
                        "publisher connector is not authorized"
                    ) from exc
            campaign = MarketingCampaign(
                owner_id=owner_id,
                name=name,
                objective=objective,
                product=product,
                audience=audience,
                channels_json=channels_json,
                source_facts_json=sources_json,
                publisher_connector_id=publisher_connector_id,
                publish_path=publish_path,
                status=MarketingCampaignStatus.PENDING,
            )
            campaign.stages = [
                MarketingStage(
                    owner_id=owner_id,
                    position=position,
                    kind=kind,
                    status=MarketingStageStatus.PENDING,
                )
                for position, kind in enumerate(MARKETING_STAGE_ORDER, start=1)
            ]
            self.session.add(campaign)
            await self.session.flush()
            await self.session.refresh(campaign)
            value = campaign_view(campaign)
            await self.session.commit()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    async def get_for_owner(
        self, owner_id: UUID, campaign_id: UUID
    ) -> MarketingCampaignView | None:
        try:
            campaign = await self.repository.get_for_owner(owner_id, campaign_id)
            value = None if campaign is None else campaign_view(campaign)
            await self.session.rollback()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int = 20
    ) -> tuple[MarketingCampaignView, ...]:
        try:
            campaigns = await self.repository.list_for_owner(owner_id, limit=limit)
            values = tuple(campaign_view(campaign) for campaign in campaigns)
            await self.session.rollback()
            return values
        except BaseException:
            await self.session.rollback()
            raise

    @staticmethod
    def analyze(metrics: MarketingAnalyticsInput) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(metrics, MarketingAnalyticsInput):
            raise MarketingCampaignInputError("marketing analytics are invalid")
        source = _exact_text(metrics.source_reference, 512, "analytics source")
        if metrics.observed_at.tzinfo is None:
            raise MarketingCampaignInputError("analytics timestamp requires a timezone")
        integer_values = (
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.spend_minor,
            metrics.revenue_minor,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            raise MarketingCampaignInputError("analytics values must be integers")
        if any(value < 0 or value > 10**15 for value in integer_values):
            raise MarketingCampaignInputError("analytics values are outside their bound")
        if metrics.clicks > metrics.impressions or metrics.conversions > metrics.clicks:
            raise MarketingCampaignInputError("analytics funnel values are inconsistent")

        def percent(numerator: int, denominator: int) -> str | None:
            if denominator == 0:
                return None
            value = Decimal(numerator * 100) / Decimal(denominator)
            return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        analytics = {
            "source_reference": source,
            "observed_at": metrics.observed_at.isoformat(),
            "impressions": metrics.impressions,
            "clicks": metrics.clicks,
            "conversions": metrics.conversions,
            "spend_minor": metrics.spend_minor,
            "revenue_minor": metrics.revenue_minor,
            "ctr_percent": percent(metrics.clicks, metrics.impressions),
            "conversion_rate_percent": percent(metrics.conversions, metrics.clicks),
            "cost_per_conversion_minor": (
                None
                if metrics.conversions == 0
                else str(
                    (Decimal(metrics.spend_minor) / Decimal(metrics.conversions)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
            ),
            "return_on_ad_spend": (
                None
                if metrics.spend_minor == 0
                else str(
                    (Decimal(metrics.revenue_minor) / Decimal(metrics.spend_minor)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
            ),
        }
        recommendations: list[str] = []
        if metrics.impressions == 0:
            recommendations.append("Collect sufficient impressions before optimization.")
        else:
            ctr = Decimal(metrics.clicks * 100) / Decimal(metrics.impressions)
            if ctr < Decimal("1"):
                recommendations.append("Test the audience targeting and opening hook.")
        if metrics.clicks > 0:
            conversion_rate = Decimal(metrics.conversions * 100) / Decimal(metrics.clicks)
            if conversion_rate < Decimal("2"):
                recommendations.append("Test the offer and landing-page conversion path.")
        if metrics.spend_minor > 0 and metrics.revenue_minor < metrics.spend_minor:
            recommendations.append("Reduce exposure until a controlled variant improves return.")
        if not recommendations:
            recommendations.append("Maintain the baseline and run one controlled variant test.")
        optimization = {
            "basis": "deterministic_rules_from_submitted_metrics",
            "recommendations": recommendations,
        }
        return analytics, optimization
