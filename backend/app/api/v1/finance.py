from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_os.orchestrator import AgentOrchestrator
from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.finance import FinanceService, MarketIntelligenceAgent, MarketIntelligenceError
from app.finance.service import (
    FinanceConflictError,
    FinanceInputError,
    FinanceNotFoundError,
    MarketBar,
    MarketQuote,
    MarketSourceFact,
    PaperOrderInput,
)
from app.models.user import User
from app.schemas.finance import (
    BacktestRequest,
    FinanceArtifactResponse,
    FinanceWorkspaceCreateRequest,
    FinanceWorkspacePageResponse,
    FinanceWorkspaceResponse,
    MarketAlertCreateRequest,
    MarketAlertEvaluationRequest,
    MarketAlertEvaluationResponse,
    MarketAlertResponse,
    MarketResearchRequest,
    PaperOrderRequest,
    PaperOrderResponse,
    PortfolioAnalysisRequest,
    PortfolioAnalysisResponse,
    TradingJournalCreateRequest,
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
    return FinanceWorkspaceResponse.model_validate(value, from_attributes=True)


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
