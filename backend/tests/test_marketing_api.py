from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.marketing as marketing_module
from app.api.dependencies import get_current_user
from app.api.v1.marketing import router
from app.db.dependencies import get_db_session
from app.marketing.service import (
    MarketingCampaignConflictError,
    MarketingCampaignNotFoundError,
    MarketingCampaignView,
    MarketingSourceFact,
    MarketingStageView,
)
from app.models.marketing import (
    MarketingCampaignStatus,
    MarketingStageKind,
    MarketingStageStatus,
)
from app.models.user import User


@pytest.fixture
def marketing_api(monkeypatch):
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock()
    service.list_for_owner = AsyncMock(return_value=())
    service.create_for_owner = AsyncMock()
    service.get_for_owner = AsyncMock(return_value=None)
    runner = Mock()
    runner.start_for_owner = AsyncMock()
    runner.approve_and_publish = AsyncMock()
    runner.submit_analytics = AsyncMock()
    runner.cancel_for_owner = AsyncMock()
    monkeypatch.setattr(
        marketing_module, "MarketingCampaignService", Mock(return_value=service)
    )
    monkeypatch.setattr(marketing_module, "_runner", lambda _request: runner)

    async def database_override():
        yield session

    async def user_override():
        return user

    application.dependency_overrides[get_db_session] = database_override
    application.dependency_overrides[get_current_user] = user_override
    with TestClient(application) as client:
        yield client, user, session, service, runner


def _campaign(*, status=MarketingCampaignStatus.PENDING):
    now = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
    return MarketingCampaignView(
        id=uuid4(),
        name="Verified launch",
        objective="Create a source-grounded launch campaign.",
        product="AI OS",
        audience="Technical founders",
        channels=("email", "web"),
        source_facts=(MarketingSourceFact("brief.md#L1", "AI OS is local-first."),),
        publisher_connector_id=None,
        publish_path=None,
        status=status,
        current_stage=None,
        analytics=None,
        error_code=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        approved_at=None,
        published_at=None,
        completed_at=None,
        stages=tuple(
            MarketingStageView(
                id=uuid4(),
                position=position,
                kind=kind,
                status=MarketingStageStatus.PENDING,
                output=None,
                output_sha256=None,
                model_id=None,
                connector_execution_id=None,
                error_code=None,
                started_at=None,
                completed_at=None,
                duration_ms=None,
            )
            for position, kind in enumerate(MarketingStageKind, start=1)
        ),
    )


def _create_payload():
    return {
        "name": "Verified launch",
        "objective": "Create a source-grounded launch campaign.",
        "product": "AI OS",
        "audience": "Technical founders",
        "channels": ["email", "web"],
        "source_facts": [
            {"source_reference": "brief.md#L1", "fact": "AI OS is local-first."}
        ],
    }


def test_create_preserves_grounded_sources_and_authenticated_owner(marketing_api):
    client, user, _session, service, _runner = marketing_api
    campaign = _campaign()
    service.create_for_owner.return_value = campaign

    response = client.post("/api/v1/marketing/campaigns", json=_create_payload())

    assert response.status_code == 201
    assert response.json()["source_facts"] == _create_payload()["source_facts"]
    call = service.create_for_owner.await_args
    assert call.args == (user.id,)
    assert call.kwargs["source_facts"] == campaign.source_facts
    assert "owner_id" not in response.text


def test_start_approve_analytics_and_cancel_are_owner_scoped(marketing_api):
    client, user, session, _service, runner = marketing_api
    campaign = _campaign()
    runner.start_for_owner.return_value = campaign
    runner.approve_and_publish.return_value = replace(
        campaign, status=MarketingCampaignStatus.AWAITING_ANALYTICS
    )
    runner.submit_analytics.return_value = replace(
        campaign, status=MarketingCampaignStatus.COMPLETED
    )
    runner.cancel_for_owner.return_value = replace(
        campaign, status=MarketingCampaignStatus.CANCELLED
    )

    assert client.post(f"/api/v1/marketing/campaigns/{campaign.id}/start").status_code == 202
    assert client.post(f"/api/v1/marketing/campaigns/{campaign.id}/approve").status_code == 200
    analytics = client.post(
        f"/api/v1/marketing/campaigns/{campaign.id}/analytics",
        json={
            "source_reference": "provider-export.csv#row=2",
            "observed_at": "2026-09-02T12:00:00Z",
            "impressions": 100,
            "clicks": 10,
            "conversions": 1,
            "spend_minor": 100,
            "revenue_minor": 200,
        },
    )
    assert analytics.status_code == 200
    assert client.delete(f"/api/v1/marketing/campaigns/{campaign.id}").status_code == 200
    runner.start_for_owner.assert_awaited_once_with(user.id, campaign.id)
    runner.approve_and_publish.assert_awaited_once_with(user.id, campaign.id)
    assert runner.submit_analytics.await_args.args[:2] == (user.id, campaign.id)
    runner.cancel_for_owner.assert_awaited_once_with(user.id, campaign.id)
    assert session.rollback.await_count == 4


def test_lifecycle_captures_owner_before_authentication_session_rollback(marketing_api):
    client, user, session, _service, runner = marketing_api
    campaign = _campaign()
    runner.start_for_owner.return_value = campaign
    runner.approve_and_publish.return_value = replace(
        campaign, status=MarketingCampaignStatus.AWAITING_ANALYTICS
    )
    runner.submit_analytics.return_value = replace(
        campaign, status=MarketingCampaignStatus.COMPLETED
    )
    runner.cancel_for_owner.return_value = replace(
        campaign, status=MarketingCampaignStatus.CANCELLED
    )

    expected_owner_ids = []

    async def expire_authenticated_user():
        expected_owner_ids.append(user.id)
        user.id = uuid4()

    session.rollback.side_effect = expire_authenticated_user

    assert client.post(f"/api/v1/marketing/campaigns/{campaign.id}/start").status_code == 202
    assert runner.start_for_owner.await_args.args[0] == expected_owner_ids[-1]
    assert client.post(f"/api/v1/marketing/campaigns/{campaign.id}/approve").status_code == 200
    assert runner.approve_and_publish.await_args.args[0] == expected_owner_ids[-1]
    analytics = client.post(
        f"/api/v1/marketing/campaigns/{campaign.id}/analytics",
        json={
            "source_reference": "provider-export.csv#row=2",
            "observed_at": "2026-09-02T12:00:00Z",
            "impressions": 100,
            "clicks": 10,
            "conversions": 1,
            "spend_minor": 100,
            "revenue_minor": 200,
        },
    )
    assert analytics.status_code == 200
    assert runner.submit_analytics.await_args.args[0] == expected_owner_ids[-1]
    assert client.delete(f"/api/v1/marketing/campaigns/{campaign.id}").status_code == 200
    assert runner.cancel_for_owner.await_args.args[0] == expected_owner_ids[-1]


def test_foreign_campaign_returns_fixed_not_found(marketing_api):
    client, _user, _session, _service, runner = marketing_api
    runner.start_for_owner.side_effect = MarketingCampaignNotFoundError(
        "PRIVATE_SENTINEL"
    )

    response = client.post(f"/api/v1/marketing/campaigns/{uuid4()}/start")

    assert response.status_code == 404
    assert response.json() == {"detail": "Marketing campaign not found"}
    assert "PRIVATE_SENTINEL" not in response.text


def test_unconfigured_publisher_is_an_explicit_conflict(marketing_api):
    client, _user, _session, _service, runner = marketing_api
    runner.approve_and_publish.side_effect = MarketingCampaignConflictError(
        "publisher connector is an external boundary"
    )

    response = client.post(f"/api/v1/marketing/campaigns/{uuid4()}/approve")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "publisher connector is an external boundary"
    }


def test_request_schema_rejects_duplicate_channels_before_service(marketing_api):
    client, _user, _session, service, _runner = marketing_api
    payload = _create_payload()
    payload["channels"] = ["email", "email"]

    response = client.post("/api/v1/marketing/campaigns", json=payload)

    assert response.status_code == 422
    service.create_for_owner.assert_not_awaited()
