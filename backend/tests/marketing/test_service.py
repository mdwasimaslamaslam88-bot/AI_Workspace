from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.marketing.service import (
    MarketingAnalyticsInput,
    MarketingCampaignInputError,
    MarketingCampaignService,
    MarketingSourceFact,
    _source_payload,
    canonical_json,
    verified_publish_receipt,
)


def test_grounded_analytics_are_deterministic_and_exact():
    analytics, optimization = MarketingCampaignService.analyze(
        MarketingAnalyticsInput(
            source_reference="provider-export.csv#row=2",
            observed_at=datetime(2026, 9, 2, 12, tzinfo=timezone.utc),
            impressions=10_000,
            clicks=250,
            conversions=10,
            spend_minor=25_000,
            revenue_minor=75_000,
        )
    )

    assert analytics == {
        "source_reference": "provider-export.csv#row=2",
        "observed_at": "2026-09-02T12:00:00+00:00",
        "impressions": 10_000,
        "clicks": 250,
        "conversions": 10,
        "spend_minor": 25_000,
        "revenue_minor": 75_000,
        "ctr_percent": "2.50",
        "conversion_rate_percent": "4.00",
        "cost_per_conversion_minor": "2500.00",
        "return_on_ad_spend": "3.00",
    }
    assert optimization == {
        "basis": "deterministic_rules_from_submitted_metrics",
        "recommendations": [
            "Maintain the baseline and run one controlled variant test."
        ],
    }


@pytest.mark.parametrize(
    "metrics",
    [
        MarketingAnalyticsInput(
            "source", datetime.now(timezone.utc), 10, 11, 0, 0, 0
        ),
        MarketingAnalyticsInput(
            "source", datetime.now(timezone.utc), 10, 5, 6, 0, 0
        ),
        MarketingAnalyticsInput("source", datetime.now(), 10, 5, 1, 0, 0),
    ],
)
def test_analytics_reject_ungrounded_or_inconsistent_inputs(metrics):
    with pytest.raises(MarketingCampaignInputError):
        MarketingCampaignService.analyze(metrics)


def test_source_facts_are_exact_bounded_unique_data():
    assert _source_payload((MarketingSourceFact("source", "fact"),)) == [
        {"source_reference": "source", "fact": "fact"}
    ]
    with pytest.raises(MarketingCampaignInputError):
        _source_payload(
            (
                MarketingSourceFact("source", "fact"),
                MarketingSourceFact("source", "fact"),
            )
        )
    with pytest.raises(MarketingCampaignInputError):
        canonical_json({"value": float("nan")}, 100)


def test_publish_receipt_requires_exact_provider_confirmation():
    campaign_id = uuid4()
    verified = verified_publish_receipt(
        {
            "campaign_id": str(campaign_id),
            "provider_reference": "provider-post-123",
            "state": "published",
        },
        campaign_id,
    )
    assert verified["provider_state"] == "published"
    assert len(verified["provider_reference_sha256"]) == 64
    assert "provider-post-123" not in str(verified)

    for payload in (
        {"accepted": True},
        {
            "campaign_id": str(campaign_id),
            "provider_reference": "provider-post-123",
            "state": "accepted",
        },
        {
            "campaign_id": str(uuid4()),
            "provider_reference": "provider-post-123",
            "state": "published",
        },
        {
            "campaign_id": str(campaign_id),
            "provider_reference": "provider-post-123",
            "state": "published",
            "unverified": True,
        },
    ):
        with pytest.raises(MarketingCampaignInputError, match="receipt"):
            verified_publish_receipt(payload, campaign_id)
