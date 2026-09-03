from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_os.orchestrator import AgentOrchestrator
from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.connectors import ConnectorRuntime, ConnectorService
from app.connectors.service import (
    ConnectorConflictError,
    ConnectorExecutionError,
    ConnectorNotFoundError,
)
from app.finance import FinanceService, MarketIntelligenceAgent, MarketIntelligenceError
from app.finance.market_data import MarketDataGateway
from app.finance.service import (
    FinanceConflictError,
    FinanceInputError,
    FinanceNotFoundError,
    MarketBar,
    MarketQuote,
    MarketSourceFact,
    PaperOrderInput,
)
from app.finance.trading import BrokerTradingService, LiveOrderCommand
from app.models.finance import TradingSafetyPolicy
from app.models.user import User
from app.schemas.finance import (
    BacktestRequest,
    BrokerOrderRecordResponse,
    FinanceArtifactResponse,
    FinanceWorkspaceCreateRequest,
    FinanceWorkspacePageResponse,
    FinanceWorkspaceResponse,
    MarketAlertCreateRequest,
    MarketAlertEvaluationRequest,
    MarketAlertEvaluationResponse,
    MarketAlertResponse,
    MarketDataQuoteRequest,
    MarketResearchRequest,
    LiveBrokerOrderRequest,
    NormalizedMarketQuoteResponse,
    PaperOrderRequest,
    PaperOrderResponse,
    PortfolioAnalysisRequest,
    PortfolioAnalysisResponse,
    TradingJournalCreateRequest,
    TradingSafetyPolicyConfigureRequest,
    TradingSafetyAuditResponse,
    TradingSafetyEventResponse,
    TradingSafetyPolicyResponse,
    TradingSafetyToggleRequest,
    WatchItemCreateRequest,
)


router = APIRouter(prefix="/finance", tags=["Finance"])


def _market_agent(request: Request) -> MarketIntelligenceAgent | None:
    orchestrator = getattr(request.app.state, "agent_orchestrator", None)
    return (
        MarketIntelligenceAgent(orchestrator)
        if isinstance(orchestrator, AgentOrchestrator)
        else None
    )


def _workspace(value) -> FinanceWorkspaceResponse:
    result = FinanceWorkspaceResponse.model_validate(value, from_attributes=True)
    policy = getattr(value, "trading_policy", None)
    if policy is None:
        return result
    return result.model_copy(
        update={
            "live_broker_status": (
                "live_enabled"
                if policy.live_trading_enabled and not policy.kill_switch_active
                else "configured_disabled"
            )
        }
    )


def _artifact(value) -> FinanceArtifactResponse:
    return FinanceArtifactResponse.model_validate(value, from_attributes=True)


def _raise_finance_error(exc: Exception) -> None:
    if isinstance(exc, FinanceNotFoundError):
        raise HTTPException(status_code=404, detail="Finance resource not found") from None
    if isinstance(exc, FinanceConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from None
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Finance request is invalid",
    ) from None


def _connector_service(request: Request, session: AsyncSession) -> ConnectorService:
    runtime = getattr(request.app.state, "connector_runtime", None)
    if not isinstance(runtime, ConnectorRuntime):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finance provider runtime is not configured",
        )
    return ConnectorService(session, runtime)


@asynccontextmanager
async def _broker_service(request: Request, session: AsyncSession):
    runtime = getattr(request.app.state, "connector_runtime", None)
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not isinstance(runtime, ConnectorRuntime) or session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Broker provider runtime is not configured",
        )
    async with session_factory() as connector_session:
        yield BrokerTradingService(
            session, ConnectorService(connector_session, runtime)
        )


def _raise_provider_error(exc: Exception) -> None:
    if isinstance(exc, ConnectorNotFoundError):
        raise HTTPException(status_code=404, detail="Finance provider not found") from None
    if isinstance(exc, (ConnectorConflictError, ConnectorExecutionError)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finance provider request failed closed",
        ) from None
    _raise_finance_error(exc)


def _policy(value: TradingSafetyPolicy) -> TradingSafetyPolicyResponse:
    import json

    return TradingSafetyPolicyResponse(
        workspace_id=value.workspace_id,
        execution_mode=value.execution_mode,
        broker_connector_id=value.broker_connector_id,
        broker_account_verified=value.broker_account_sha256 is not None,
        live_trading_enabled=value.live_trading_enabled,
        kill_switch_active=value.kill_switch_active,
        owner_authorized_at=value.owner_authorized_at,
        session_valid_until=value.session_valid_until,
        max_order_value_minor=value.max_order_value_minor,
        max_position_value_minor=value.max_position_value_minor,
        daily_loss_limit_minor=value.daily_loss_limit_minor,
        per_symbol_exposure_limit_minor=value.per_symbol_exposure_limit_minor,
        total_exposure_limit_minor=value.total_exposure_limit_minor,
        max_open_orders=value.max_open_orders,
        allowed_instruments=json.loads(value.allowed_instruments_json),
        allowed_venues=json.loads(value.allowed_venues_json),
        updated_at=value.updated_at,
    )


@router.get("/workspaces", response_model=FinanceWorkspacePageResponse)
async def list_finance_workspaces(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceWorkspacePageResponse:
    values = await FinanceService(session).list_workspaces(current_user.id)
    return FinanceWorkspacePageResponse(items=[_workspace(value) for value in values])


@router.post(
    "/workspaces",
    response_model=FinanceWorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_finance_workspace(
    payload: FinanceWorkspaceCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceWorkspaceResponse:
    try:
        value = await FinanceService(session).create_workspace(
            current_user.id,
            name=payload.name,
            base_currency=payload.base_currency,
            initial_cash_minor=payload.initial_cash_minor,
            max_order_bps=payload.max_order_bps,
            max_position_bps=payload.max_position_bps,
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return _workspace(value)


@router.get(
    "/workspaces/{workspace_id}", response_model=FinanceWorkspaceResponse
)
async def get_finance_workspace(
    workspace_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceWorkspaceResponse:
    value = await FinanceService(session).get_workspace(current_user.id, workspace_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Finance resource not found")
    return _workspace(value)


@router.post("/market-data/quotes/resolve", response_model=NormalizedMarketQuoteResponse)
async def resolve_market_data_quote(
    payload: MarketDataQuoteRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> NormalizedMarketQuoteResponse:
    try:
        value = await MarketDataGateway(_connector_service(request, session)).quote(
            current_user.id,
            payload.connector_id,
            path=payload.path,
            max_age_seconds=payload.max_age_seconds,
        )
    except (
        FinanceNotFoundError,
        FinanceConflictError,
        FinanceInputError,
        ConnectorNotFoundError,
        ConnectorConflictError,
        ConnectorExecutionError,
    ) as exc:
        _raise_provider_error(exc)
    return NormalizedMarketQuoteResponse.model_validate(value, from_attributes=True)


@router.put(
    "/workspaces/{workspace_id}/trading-safety",
    response_model=TradingSafetyPolicyResponse,
)
async def configure_trading_safety(
    workspace_id: UUID,
    payload: TradingSafetyPolicyConfigureRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TradingSafetyPolicyResponse:
    try:
        async with _broker_service(request, session) as service:
            value = await service.configure_policy(
                current_user.id,
                workspace_id,
                broker_connector_id=payload.broker_connector_id,
                account_path=payload.account_path,
                order_path=payload.order_path,
                order_status_prefix=payload.order_status_prefix,
                max_order_value_minor=payload.max_order_value_minor,
                max_position_value_minor=payload.max_position_value_minor,
                daily_loss_limit_minor=payload.daily_loss_limit_minor,
                per_symbol_exposure_limit_minor=payload.per_symbol_exposure_limit_minor,
                total_exposure_limit_minor=payload.total_exposure_limit_minor,
                max_open_orders=payload.max_open_orders,
                allowed_instruments=tuple(payload.allowed_instruments),
                allowed_venues=tuple(payload.allowed_venues),
                owner_confirmation=payload.owner_confirmation,
            )
    except (
        FinanceNotFoundError,
        FinanceConflictError,
        FinanceInputError,
        ConnectorNotFoundError,
        ConnectorConflictError,
        ConnectorExecutionError,
    ) as exc:
        _raise_provider_error(exc)
    return _policy(value)


@router.get(
    "/workspaces/{workspace_id}/trading-safety",
    response_model=TradingSafetyPolicyResponse,
)
async def get_trading_safety(
    workspace_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TradingSafetyPolicyResponse:
    workspace = await FinanceService(session).get_workspace(current_user.id, workspace_id)
    if workspace is None or workspace.trading_policy is None:
        raise HTTPException(status_code=404, detail="Trading safety policy not configured")
    return _policy(workspace.trading_policy)


@router.get(
    "/workspaces/{workspace_id}/trading-safety/audit",
    response_model=TradingSafetyAuditResponse,
)
async def get_trading_safety_audit(
    workspace_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TradingSafetyAuditResponse:
    workspace = await FinanceService(session).get_workspace(current_user.id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Finance resource not found")
    return TradingSafetyAuditResponse(
        events=[
            TradingSafetyEventResponse.model_validate(value, from_attributes=True)
            for value in workspace.trading_safety_events[:250]
        ],
        broker_orders=[
            BrokerOrderRecordResponse.model_validate(value, from_attributes=True)
            for value in workspace.broker_orders[:250]
        ],
    )


@router.post(
    "/workspaces/{workspace_id}/trading-safety/live",
    response_model=TradingSafetyPolicyResponse,
)
async def set_live_trading(
    workspace_id: UUID,
    payload: TradingSafetyToggleRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TradingSafetyPolicyResponse:
    try:
        async with _broker_service(request, session) as service:
            value = await service.set_live_enabled(
                current_user.id,
                workspace_id,
                enabled=payload.enabled,
                owner_confirmation=payload.owner_confirmation,
            )
    except (
        FinanceNotFoundError,
        FinanceConflictError,
        FinanceInputError,
        ConnectorNotFoundError,
        ConnectorConflictError,
        ConnectorExecutionError,
    ) as exc:
        _raise_provider_error(exc)
    return _policy(value)


@router.post(
    "/workspaces/{workspace_id}/trading-safety/kill-switch",
    response_model=TradingSafetyPolicyResponse,
)
async def activate_trading_kill_switch(
    workspace_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TradingSafetyPolicyResponse:
    try:
        async with _broker_service(request, session) as service:
            value = await service.activate_kill_switch(current_user.id, workspace_id)
    except (
        FinanceNotFoundError,
        FinanceConflictError,
        FinanceInputError,
        ConnectorNotFoundError,
        ConnectorConflictError,
        ConnectorExecutionError,
    ) as exc:
        _raise_provider_error(exc)
    return _policy(value)


@router.post(
    "/workspaces/{workspace_id}/broker-orders",
    response_model=BrokerOrderRecordResponse,
)
async def place_verified_broker_order(
    workspace_id: UUID,
    payload: LiveBrokerOrderRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> BrokerOrderRecordResponse:
    try:
        async with _broker_service(request, session) as service:
            value = await service.place_live_order(
                current_user.id,
                workspace_id,
                LiveOrderCommand(
                    asset_class=payload.asset_class,
                    symbol=payload.symbol,
                    venue=payload.venue,
                    currency=payload.currency,
                    side=payload.side,
                    quantity_micros=payload.quantity_micros,
                    limit_price_minor=payload.limit_price_minor,
                    client_order_key=payload.client_order_key,
                    market_data_connector_id=payload.market_data_connector_id,
                    quote_path=payload.quote_path,
                    max_quote_age_seconds=payload.max_quote_age_seconds,
                ),
            )
    except (
        FinanceNotFoundError,
        FinanceConflictError,
        FinanceInputError,
        ConnectorNotFoundError,
        ConnectorConflictError,
        ConnectorExecutionError,
    ) as exc:
        _raise_provider_error(exc)
    return BrokerOrderRecordResponse.model_validate(value, from_attributes=True)


@router.post(
    "/workspaces/{workspace_id}/watchlist",
    response_model=FinanceWorkspaceResponse,
)
async def add_watch_item(
    workspace_id: UUID,
    payload: WatchItemCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceWorkspaceResponse:
    try:
        value = await FinanceService(session).add_watch_item(
            current_user.id,
            workspace_id,
            asset_class=payload.asset_class,
            symbol=payload.symbol,
            display_name=payload.display_name,
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return _workspace(value)


@router.delete(
    "/workspaces/{workspace_id}/watchlist/{item_id}",
    response_model=FinanceWorkspaceResponse,
)
async def remove_watch_item(
    workspace_id: UUID,
    item_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceWorkspaceResponse:
    try:
        value = await FinanceService(session).remove_watch_item(
            current_user.id, workspace_id, item_id
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return _workspace(value)


@router.post(
    "/workspaces/{workspace_id}/research",
    response_model=FinanceArtifactResponse,
)
async def run_market_research(
    workspace_id: UUID,
    payload: MarketResearchRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceArtifactResponse:
    owner_id = current_user.id
    try:
        value = await FinanceService(session, _market_agent(request)).run_research(
            owner_id,
            workspace_id,
            kind=payload.kind,
            asset_class=payload.asset_class,
            subject=payload.subject,
            source_reference=payload.source_reference,
            sources=tuple(
                MarketSourceFact(value.source_reference, value.fact)
                for value in payload.source_facts
            ),
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    except MarketIntelligenceError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Verified local market research failed",
        ) from None
    return _artifact(value)


@router.post(
    "/workspaces/{workspace_id}/backtests",
    response_model=FinanceArtifactResponse,
)
async def run_market_backtest(
    workspace_id: UUID,
    payload: BacktestRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceArtifactResponse:
    try:
        value = await FinanceService(session).run_backtest(
            current_user.id,
            workspace_id,
            asset_class=payload.asset_class,
            symbol=payload.symbol,
            source_reference=payload.source_reference,
            bars=tuple(MarketBar(value.observed_at, value.close_minor) for value in payload.bars),
            fast_window=payload.fast_window,
            slow_window=payload.slow_window,
            initial_cash_minor=payload.initial_cash_minor,
            fee_bps=payload.fee_bps,
            slippage_bps=payload.slippage_bps,
            position_size_bps=payload.position_size_bps,
            stop_loss_bps=payload.stop_loss_bps,
            take_profit_bps=payload.take_profit_bps,
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return _artifact(value)


@router.post(
    "/workspaces/{workspace_id}/paper-orders",
    response_model=PaperOrderResponse,
)
async def execute_paper_order(
    workspace_id: UUID,
    payload: PaperOrderRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PaperOrderResponse:
    try:
        value = await FinanceService(session).execute_paper_order(
            current_user.id,
            workspace_id,
            PaperOrderInput(
                asset_class=payload.asset_class,
                symbol=payload.symbol,
                side=payload.side,
                quantity_micros=payload.quantity_micros,
                price_minor=payload.price_minor,
                observed_at=payload.observed_at,
                source_reference=payload.source_reference,
                owner_confirmed=payload.owner_confirmed,
            ),
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return PaperOrderResponse.model_validate(value, from_attributes=True)


@router.post(
    "/workspaces/{workspace_id}/portfolio-analysis",
    response_model=PortfolioAnalysisResponse,
)
async def analyze_paper_portfolio(
    workspace_id: UUID,
    payload: PortfolioAnalysisRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PortfolioAnalysisResponse:
    try:
        portfolio, risk = await FinanceService(session).analyze_portfolio(
            current_user.id,
            workspace_id,
            source_reference=payload.source_reference,
            quotes=tuple(
                MarketQuote(
                    value.asset_class,
                    value.symbol,
                    value.price_minor,
                    value.observed_at,
                    value.source_reference,
                )
                for value in payload.quotes
            ),
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return PortfolioAnalysisResponse(
        portfolio=_artifact(portfolio), risk=_artifact(risk)
    )


@router.post(
    "/workspaces/{workspace_id}/alerts",
    response_model=MarketAlertResponse,
)
async def create_market_alert(
    workspace_id: UUID,
    payload: MarketAlertCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketAlertResponse:
    try:
        value = await FinanceService(session).create_alert(
            current_user.id,
            workspace_id,
            asset_class=payload.asset_class,
            symbol=payload.symbol,
            condition=payload.condition,
            threshold_minor=payload.threshold_minor,
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return MarketAlertResponse.model_validate(value, from_attributes=True)


@router.post(
    "/workspaces/{workspace_id}/alerts/evaluate",
    response_model=MarketAlertEvaluationResponse,
)
async def evaluate_market_alerts(
    workspace_id: UUID,
    payload: MarketAlertEvaluationRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketAlertEvaluationResponse:
    quote = payload.quote
    try:
        values = await FinanceService(session).evaluate_alerts(
            current_user.id,
            workspace_id,
            MarketQuote(
                quote.asset_class,
                quote.symbol,
                quote.price_minor,
                quote.observed_at,
                quote.source_reference,
            ),
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return MarketAlertEvaluationResponse(
        items=[
            MarketAlertResponse.model_validate(value, from_attributes=True)
            for value in values
        ]
    )


@router.post(
    "/workspaces/{workspace_id}/journal",
    response_model=FinanceArtifactResponse,
)
async def add_trading_journal_entry(
    workspace_id: UUID,
    payload: TradingJournalCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinanceArtifactResponse:
    try:
        value = await FinanceService(session).add_journal_entry(
            current_user.id,
            workspace_id,
            title=payload.title,
            note=payload.note,
            source_reference=payload.source_reference,
        )
    except (FinanceNotFoundError, FinanceConflictError, FinanceInputError) as exc:
        _raise_finance_error(exc)
    return _artifact(value)
