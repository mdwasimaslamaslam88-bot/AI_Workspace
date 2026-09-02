from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_os.contracts import AgentPermission, AgentRunStatus
from app.finance.agent import MarketIntelligenceAgent, MarketIntelligenceError
from app.finance.verification import (
    SOURCE_BLOCK_END,
    SOURCE_BLOCK_START,
    verify_grounded_market_output,
)
from app.finance.service import (
    BacktestingAgent,
    FinanceInputError,
    MarketBar,
    MarketQuote,
    PortfolioAgent,
    RiskAgent,
    digest,
)
from app.models.finance import (
    FinanceArtifactKind,
    FinanceWorkspace,
    MarketAssetClass,
    PaperPosition,
)


def _market_agent(result):
    value = object.__new__(MarketIntelligenceAgent)
    value.orchestrator = SimpleNamespace(run=AsyncMock(return_value=result))
    return value


@pytest.mark.asyncio
async def test_market_agent_accepts_only_untouched_verified_output():
    output = "Grounded research with [exchange.csv#row=2]."
    result = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output=output,
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(
                    passed=True, output_sha256=digest(output)
                ),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
        failure_code=None,
    )

    agent = _market_agent(result)
    generated = await agent.generate(
        FinanceArtifactKind.RESEARCH, "Use only the supplied market fact."
    )

    assert generated.output == output
    assert generated.output_sha256 == digest(output)
    assert generated.model_id == "ollama-local/qwen3:8b"
    request = agent.orchestrator.run.await_args.args[0]
    assert request.permissions == frozenset({AgentPermission.MODEL_INFERENCE})
    assert request.require_objective_evidence is True


@pytest.mark.asyncio
async def test_market_objective_verifier_requires_sources_and_research_contract():
    instruction = (
        "Market task: research\n"
        f"{SOURCE_BLOCK_START}"
        '[{"fact":"Revenue was 100 minor units.",'
        '"source_reference":"exchange.csv#row=2"}]'
        f"{SOURCE_BLOCK_END}\n"
        "Requirement: grounded research"
    )
    step = SimpleNamespace(instruction=instruction)
    accepted = await verify_grounded_market_output(
        step,
        SimpleNamespace(
            output=(
                "Research cites exchange.csv#row=2. Uncertainties remain; "
                "counter-evidence was not supplied."
            )
        ),
    )
    missing_source = await verify_grounded_market_output(
        step,
        SimpleNamespace(
            output="Research has uncertainties and counter-evidence limitations."
        ),
    )

    assert accepted is not None and accepted.passed is True
    assert missing_source is not None and missing_source.passed is False


@pytest.mark.asyncio
async def test_market_objective_verifier_enforces_paper_strategy_contract():
    instruction = (
        "Market task: strategy\n"
        f"{SOURCE_BLOCK_START}"
        '[{"fact":"Revenue was 100 minor units.",'
        '"source_reference":"exchange.csv#row=2"}]'
        f"{SOURCE_BLOCK_END}\n"
        "Requirement: paper-only strategy"
    )
    step = SimpleNamespace(instruction=instruction)

    accepted = await verify_grounded_market_output(
        step,
        SimpleNamespace(
            output=(
                "Using exchange.csv#row=2, entry and exit are hypothetical; "
                "risk and invalidation rules apply."
            )
        ),
    )
    profit_claim = await verify_grounded_market_output(
        step,
        SimpleNamespace(
            output=(
                "Using exchange.csv#row=2, entry and exit define risk and "
                "invalidation with guaranteed profits."
            )
        ),
    )
    disclaimer = await verify_grounded_market_output(
        step,
        SimpleNamespace(
            output=(
                "Using exchange.csv#row=2, entry and exit define risk and "
                "invalidation. There are no guaranteed profits."
            )
        ),
    )

    assert accepted is not None and accepted.passed is True
    assert profit_claim is not None and profit_claim.passed is False
    assert disclaimer is not None and disclaimer.passed is True


@pytest.mark.asyncio
async def test_market_agent_rejects_changed_output_digest():
    result = SimpleNamespace(
        status=AgentRunStatus.COMPLETED,
        output="changed output",
        attempts=(
            SimpleNamespace(
                verification=SimpleNamespace(
                    passed=True, output_sha256="a" * 64
                ),
                model_id="ollama-local/qwen3:8b",
            ),
        ),
        failure_code=None,
    )

    with pytest.raises(MarketIntelligenceError, match="digest"):
        await _market_agent(result).generate(
            FinanceArtifactKind.STRATEGY, "Create a paper-only strategy."
        )


def test_backtesting_agent_is_deterministic_and_disclaims_profit():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = (100, 90, 80, 90, 100, 110, 100, 90)
    bars = tuple(
        MarketBar(started + timedelta(days=index), price)
        for index, price in enumerate(prices)
    )

    result = BacktestingAgent.run(
        bars,
        fast_window=2,
        slow_window=3,
        initial_cash_minor=100_000,
        fee_bps=10,
    )

    assert result["engine"] == "deterministic_moving_average_v1"
    assert result["final_equity_minor"] == 89_820
    assert result["return_bps"] == -1_018
    assert result["maximum_drawdown_bps"] == 1_826
    assert [trade["side"] for trade in result["trades"]] == ["buy", "sell"]
    assert result["profit_guarantee"] is False


def test_backtesting_agent_rejects_reordered_or_ungrounded_bars():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(FinanceInputError, match="strictly ordered"):
        BacktestingAgent.run(
            (
                MarketBar(now, 100),
                MarketBar(now, 101),
                MarketBar(now + timedelta(days=1), 102),
            ),
            fast_window=2,
            slow_window=3,
            initial_cash_minor=1_000,
            fee_bps=0,
        )


def test_portfolio_and_risk_agents_require_exact_source_quotes():
    workspace = FinanceWorkspace(
        name="Paper portfolio",
        base_currency="USD",
        initial_cash_minor=100_000,
        cash_minor=75_000,
        max_order_bps=1_000,
        max_position_bps=2_500,
    )
    workspace.positions = [
        PaperPosition(
            asset_class=MarketAssetClass.GLOBAL_STOCK,
            symbol="ACME",
            quantity_micros=1_000_000,
            cost_basis_minor=25_000,
        )
    ]
    quote = MarketQuote(
        MarketAssetClass.GLOBAL_STOCK,
        "ACME",
        30_000,
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        "exchange.csv#ACME",
    )

    portfolio = PortfolioAgent.analyze(workspace, (quote,))
    risk = RiskAgent.analyze(workspace, portfolio)

    assert portfolio["total_equity_minor"] == 105_000
    assert portfolio["total_return_minor"] == 5_000
    assert portfolio["positions"][0]["source_reference"] == "exchange.csv#ACME"
    assert risk["live_execution"] is False
    assert risk["profit_guarantee"] is False
    assert risk["breaches"][0]["code"] == "position_concentration"

    with pytest.raises(FinanceInputError, match="exactly cover"):
        PortfolioAgent.analyze(workspace, ())
