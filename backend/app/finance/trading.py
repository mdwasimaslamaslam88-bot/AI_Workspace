from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any
from uuid import UUID

from app.connectors.service import ConnectorConnectionStatus, ConnectorService
from app.finance.market_data import MarketDataGateway, NormalizedMarketQuote
from app.finance.service import (
    FinanceConflictError,
    FinanceInputError,
    FinanceNotFoundError,
    _notional,
    canonical_json,
    digest,
)
from app.models.finance import (
    BrokerOrderRecord,
    BrokerOrderStatus,
    MarketAssetClass,
    PaperOrderSide,
    TradingExecutionMode,
    TradingSafetyAction,
    TradingSafetyEvent,
    TradingSafetyPolicy,
)
from app.repositories.finance import FinanceRepository


_PATH = re.compile(r"^/(?!/)(?!.*(?:^|/)\.\.?/)[^?#%\\\x00-\x20]{0,511}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,23}$")
_VENUE = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,31}$")
_CLIENT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_CONFIGURE_CONFIRMATION = "AUTHORIZE BROKER CONFIGURATION"
_ENABLE_CONFIRMATION = "ENABLE LIVE TRADING"
_REENABLE_CONFIRMATION = "RE-ENABLE LIVE TRADING"
_DISABLE_CONFIRMATION = "DISABLE LIVE TRADING"
_REQUIRED_BROKER_CAPABILITIES = frozenset(
    {"broker.account.read", "broker.order.submit", "broker.order.status"}
)
_ACCOUNT_KEYS = frozenset({"provider", "account", "risk", "market"})
_ACCOUNT_IDENTITY_KEYS = frozenset(
    {"id", "state", "currency", "live_trading_permitted", "mfa_session_valid", "session_valid_until"}
)
_RISK_KEYS = frozenset(
    {"daily_pnl_minor", "total_exposure_minor", "open_orders", "positions"}
)


@dataclass(frozen=True, slots=True)
class BrokerAccountSnapshot:
    account_id: str
    currency: str
    session_valid_until: datetime
    daily_pnl_minor: int
    total_exposure_minor: int
    open_orders: int
    position_values_minor: dict[str, int]
    market_session_open: bool


@dataclass(frozen=True, slots=True)
class LiveOrderCommand:
    asset_class: MarketAssetClass
    symbol: str
    venue: str
    currency: str
    side: PaperOrderSide
    quantity_micros: int
    limit_price_minor: int
    client_order_key: str
    market_data_connector_id: UUID
    quote_path: str
    max_quote_age_seconds: int = 60


def _bounded_string(value: Any, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise FinanceInputError(f"broker {label} is invalid")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0, maximum: int = 10**15) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise FinanceInputError(f"broker {label} is invalid")
    return value


def _path(value: Any, label: str, *, prefix: bool = False) -> str:
    result = _bounded_string(value, label, 512)
    if (
        _PATH.fullmatch(result) is None
        or any(segment in {".", ".."} for segment in result.split("/"))
        or (prefix and not result.endswith("/"))
    ):
        raise FinanceInputError(f"broker {label} is invalid")
    return result


def _string_list(values: tuple[str, ...], label: str, pattern: re.Pattern[str], maximum: int) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or not 1 <= len(values) <= maximum
        or len(set(values)) != len(values)
        or any(not isinstance(item, str) or pattern.fullmatch(item) is None for item in values)
    ):
        raise FinanceInputError(f"broker {label} is invalid")
    return tuple(sorted(values))


def _account_snapshot(payload: Any, *, expected_provider: str, now: datetime) -> BrokerAccountSnapshot:
    if not isinstance(payload, dict) or frozenset(payload) != _ACCOUNT_KEYS:
        raise FinanceInputError("broker account response is invalid")
    if payload["provider"] != expected_provider:
        raise FinanceInputError("broker provider identity mismatch")
    account = payload["account"]
    risk = payload["risk"]
    market = payload["market"]
    if not isinstance(account, dict) or frozenset(account) != _ACCOUNT_IDENTITY_KEYS:
        raise FinanceInputError("broker account identity response is invalid")
    if not isinstance(risk, dict) or frozenset(risk) != _RISK_KEYS:
        raise FinanceInputError("broker risk response is invalid")
    if not isinstance(market, dict) or frozenset(market) != {"session_open"}:
        raise FinanceInputError("broker market session response is invalid")
    account_id = _bounded_string(account["id"], "account identity", 256)
    currency = _bounded_string(account["currency"], "account currency", 3)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise FinanceInputError("broker account currency is invalid")
    if account["state"] != "verified" or account["live_trading_permitted"] is not True:
        raise FinanceConflictError("broker account is not approved for live trading")
    if account["mfa_session_valid"] is not True:
        raise FinanceConflictError("broker MFA/session is not valid")
    try:
        valid_until = datetime.fromisoformat(
            _bounded_string(account["session_valid_until"], "session expiry", 64).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise FinanceInputError("broker session expiry is invalid") from exc
    if valid_until.tzinfo is None or valid_until <= now:
        raise FinanceConflictError("broker session has expired")
    positions = risk["positions"]
    if not isinstance(positions, list) or len(positions) > 1_000:
        raise FinanceInputError("broker positions response is invalid")
    position_values: dict[str, int] = {}
    for item in positions:
        if not isinstance(item, dict) or frozenset(item) != {"symbol", "value_minor"}:
            raise FinanceInputError("broker position response is invalid")
        symbol = _bounded_string(item["symbol"], "position symbol", 24)
        if _SYMBOL.fullmatch(symbol) is None or symbol in position_values:
            raise FinanceInputError("broker position identity is invalid")
        position_values[symbol] = _integer(item["value_minor"], "position value")
    session_open = market["session_open"]
    if not isinstance(session_open, bool):
        raise FinanceInputError("broker market session state is invalid")
    return BrokerAccountSnapshot(
        account_id=account_id,
        currency=currency,
        session_valid_until=valid_until,
        daily_pnl_minor=_integer(risk["daily_pnl_minor"], "daily P&L", minimum=-(10**15)),
        total_exposure_minor=_integer(risk["total_exposure_minor"], "total exposure"),
        open_orders=_integer(risk["open_orders"], "open orders", maximum=100_000),
        position_values_minor=position_values,
        market_session_open=session_open,
    )


class LiveTradingSafetyWall:
    """Pure fail-closed decision gate run immediately before broker submission."""

    @staticmethod
    def require_local(
        policy: TradingSafetyPolicy | None,
        command: LiveOrderCommand,
        *,
        now: datetime,
    ) -> int:
        if policy is None:
            raise FinanceConflictError("live trading risk policy is required")
        if policy.execution_mode is not TradingExecutionMode.LIVE:
            raise FinanceConflictError("execution mode is not live")
        if policy.kill_switch_active:
            raise FinanceConflictError("trading kill switch is active")
        if not policy.live_trading_enabled:
            raise FinanceConflictError("owner live-trading authorization is disabled")
        if policy.owner_authorized_at is None or policy.session_valid_until is None:
            raise FinanceConflictError("owner authorization is incomplete")
        if policy.session_valid_until <= now:
            raise FinanceConflictError("broker authorization/session has expired")
        if command.symbol not in json.loads(policy.allowed_instruments_json):
            raise FinanceConflictError("instrument is not owner-authorized")
        if command.venue not in json.loads(policy.allowed_venues_json):
            raise FinanceConflictError("venue is not owner-authorized")
        if not _CLIENT_KEY.fullmatch(command.client_order_key):
            raise FinanceInputError("broker client order key is invalid")
        quantity = _integer(command.quantity_micros, "quantity", minimum=1)
        limit_price = _integer(command.limit_price_minor, "limit price", minimum=1)
        notional = _notional(quantity, limit_price)
        if notional > policy.max_order_value_minor:
            raise FinanceConflictError("maximum live order value exceeded")
        return notional

    @staticmethod
    def require(
        policy: TradingSafetyPolicy | None,
        command: LiveOrderCommand,
        account: BrokerAccountSnapshot,
        quote: NormalizedMarketQuote,
        *,
        now: datetime,
    ) -> int:
        notional = LiveTradingSafetyWall.require_local(
            policy, command, now=now
        )
        assert policy is not None
        if account.session_valid_until <= now:
            raise FinanceConflictError("broker authorization/session has expired")
        if digest(account.account_id) != policy.broker_account_sha256:
            raise FinanceConflictError("broker account identity changed")
        if account.currency != command.currency or quote.currency != command.currency:
            raise FinanceConflictError("order currency does not match verified sources")
        if quote.asset_class is not command.asset_class or quote.symbol != command.symbol:
            raise FinanceConflictError("market quote identity does not match order")
        if quote.exchange != command.venue:
            raise FinanceConflictError("market quote venue does not match order")
        if not account.market_session_open:
            raise FinanceConflictError("broker market session is closed")
        position_after = account.position_values_minor.get(command.symbol, 0)
        if command.side is PaperOrderSide.BUY:
            position_after += notional
        else:
            if notional > position_after:
                raise FinanceConflictError("sell order exceeds the verified position")
            position_after = max(0, position_after - notional)
        if position_after > policy.max_position_value_minor:
            raise FinanceConflictError("maximum live position value exceeded")
        if position_after > policy.per_symbol_exposure_limit_minor:
            raise FinanceConflictError("per-symbol exposure limit exceeded")
        exposure_after = account.total_exposure_minor + (
            notional if command.side is PaperOrderSide.BUY else -min(notional, account.total_exposure_minor)
        )
        if exposure_after > policy.total_exposure_limit_minor:
            raise FinanceConflictError("total exposure limit exceeded")
        if account.daily_pnl_minor < -policy.daily_loss_limit_minor:
            raise FinanceConflictError("daily loss limit exceeded")
        if account.open_orders >= policy.max_open_orders:
            raise FinanceConflictError("maximum open orders reached")
        return notional


class BrokerTradingService:
    def __init__(self, session, connector_service: ConnectorService) -> None:
        self.session = session
        self.repository = FinanceRepository(session)
        self.connector_service = connector_service
        self.market_data = MarketDataGateway(connector_service)

    async def _workspace(self, owner_id: UUID, workspace_id: UUID):
        workspace = await self.repository.get_workspace_for_owner(owner_id, workspace_id)
        if workspace is None:
            raise FinanceNotFoundError("finance workspace not found")
        return workspace

    @staticmethod
    def _policy_hash(policy: TradingSafetyPolicy) -> str:
        return digest(
            canonical_json(
                {
                    "workspace_id": str(policy.workspace_id),
                    "execution_mode": policy.execution_mode.value,
                    "broker_connector_id": (
                        str(policy.broker_connector_id)
                        if policy.broker_connector_id is not None
                        else None
                    ),
                    "broker_account_sha256": policy.broker_account_sha256,
                    "account_path": policy.account_path,
                    "order_path": policy.order_path,
                    "order_status_prefix": policy.order_status_prefix,
                    "live_trading_enabled": policy.live_trading_enabled,
                    "kill_switch_active": policy.kill_switch_active,
                    "max_order_value_minor": policy.max_order_value_minor,
                    "max_position_value_minor": policy.max_position_value_minor,
                    "daily_loss_limit_minor": policy.daily_loss_limit_minor,
                    "per_symbol_exposure_limit_minor": policy.per_symbol_exposure_limit_minor,
                    "total_exposure_limit_minor": policy.total_exposure_limit_minor,
                    "max_open_orders": policy.max_open_orders,
                    "allowed_instruments": json.loads(policy.allowed_instruments_json),
                    "allowed_venues": json.loads(policy.allowed_venues_json),
                }
            )
        )

    def _audit_policy(
        self,
        policy: TradingSafetyPolicy,
        action: TradingSafetyAction,
        connector_execution_id: UUID | None,
    ) -> None:
        self.session.add(
            TradingSafetyEvent(
                workspace_id=policy.workspace_id,
                owner_id=policy.owner_id,
                action=action,
                policy_sha256=self._policy_hash(policy),
                connector_execution_id=connector_execution_id,
            )
        )

    async def _account(
        self, owner_id: UUID, connector_id: UUID, account_path: str, *, now: datetime
    ) -> tuple[BrokerAccountSnapshot, UUID, str]:
        connector = await self.connector_service.get_for_owner(owner_id, connector_id)
        if connector.connection_status is not ConnectorConnectionStatus.HEALTHY:
            raise FinanceConflictError("broker connector is not healthy")
        if not _REQUIRED_BROKER_CAPABILITIES <= set(connector.capabilities):
            raise FinanceConflictError("broker connector capabilities are incomplete")
        result = await self.connector_service.execute_for_owner(
            owner_id,
            connector_id,
            method="GET",
            path=account_path,
            json_body=None,
            idempotency_key=None,
            required_capability="broker.account.read",
        )
        return _account_snapshot(result.payload, expected_provider=connector.provider, now=now), result.execution.id, connector.provider

    async def configure_policy(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        *,
        broker_connector_id: UUID,
        account_path: str,
        order_path: str,
        order_status_prefix: str,
        max_order_value_minor: int,
        max_position_value_minor: int,
        daily_loss_limit_minor: int,
        per_symbol_exposure_limit_minor: int,
        total_exposure_limit_minor: int,
        max_open_orders: int,
        allowed_instruments: tuple[str, ...],
        allowed_venues: tuple[str, ...],
        owner_confirmation: str,
        now: datetime | None = None,
    ) -> TradingSafetyPolicy:
        if owner_confirmation != _CONFIGURE_CONFIRMATION:
            raise FinanceConflictError("exact broker configuration confirmation is required")
        reference_now = now or datetime.now(timezone.utc)
        await self._workspace(owner_id, workspace_id)
        account_path = _path(account_path, "account path")
        order_path = _path(order_path, "order path")
        order_status_prefix = _path(order_status_prefix, "order status prefix", prefix=True)
        instruments = _string_list(allowed_instruments, "allowed instruments", _SYMBOL, 256)
        venues = _string_list(allowed_venues, "allowed venues", _VENUE, 64)
        account, account_execution_id, _ = await self._account(
            owner_id, broker_connector_id, account_path, now=reference_now
        )
        limits = (
            _integer(max_order_value_minor, "maximum order value", minimum=1),
            _integer(max_position_value_minor, "maximum position value", minimum=1),
            _integer(daily_loss_limit_minor, "daily loss limit", minimum=1),
            _integer(per_symbol_exposure_limit_minor, "per-symbol exposure", minimum=1),
            _integer(total_exposure_limit_minor, "total exposure", minimum=1),
        )
        if limits[0] > limits[1] or limits[1] > limits[4] or limits[3] > limits[4]:
            raise FinanceInputError("broker risk limits are inconsistent")
        max_open_orders = _integer(max_open_orders, "maximum open orders", minimum=1, maximum=1_000)
        policy = await self.repository.get_trading_policy(owner_id, workspace_id, for_update=True)
        fields = {
            "owner_id": owner_id,
            "execution_mode": TradingExecutionMode.LIVE,
            "broker_connector_id": broker_connector_id,
            "broker_account_sha256": digest(account.account_id),
            "account_path": account_path,
            "order_path": order_path,
            "order_status_prefix": order_status_prefix,
            "live_trading_enabled": False,
            "kill_switch_active": True,
            "owner_authorized_at": reference_now,
            "session_valid_until": account.session_valid_until,
            "max_order_value_minor": limits[0],
            "max_position_value_minor": limits[1],
            "daily_loss_limit_minor": limits[2],
            "per_symbol_exposure_limit_minor": limits[3],
            "total_exposure_limit_minor": limits[4],
            "max_open_orders": max_open_orders,
            "allowed_instruments_json": canonical_json(instruments, maximum=4_096),
            "allowed_venues_json": canonical_json(venues, maximum=4_096),
            "updated_at": reference_now,
        }
        if policy is None:
            policy = TradingSafetyPolicy(workspace_id=workspace_id, **fields)
            self.session.add(policy)
        else:
            for field, value in fields.items():
                setattr(policy, field, value)
        self._audit_policy(
            policy, TradingSafetyAction.CONFIGURED, account_execution_id
        )
        await self.session.commit()
        return policy

    async def set_live_enabled(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        *,
        enabled: bool,
        owner_confirmation: str,
        now: datetime | None = None,
    ) -> TradingSafetyPolicy:
        policy = await self.repository.get_trading_policy(owner_id, workspace_id, for_update=True)
        if policy is None:
            raise FinanceConflictError("live trading risk policy is required")
        reference_now = now or datetime.now(timezone.utc)
        if enabled:
            if owner_confirmation not in {_ENABLE_CONFIRMATION, _REENABLE_CONFIRMATION}:
                raise FinanceConflictError("exact live-trading owner confirmation is required")
            assert policy.broker_connector_id is not None and policy.account_path is not None
            account, account_execution_id, _ = await self._account(
                owner_id, policy.broker_connector_id, policy.account_path, now=reference_now
            )
            if digest(account.account_id) != policy.broker_account_sha256:
                raise FinanceConflictError("broker account identity changed")
            policy.live_trading_enabled = True
            policy.kill_switch_active = False
            policy.owner_authorized_at = reference_now
            policy.session_valid_until = account.session_valid_until
            action = TradingSafetyAction.LIVE_ENABLED
        else:
            if owner_confirmation != _DISABLE_CONFIRMATION:
                raise FinanceConflictError("exact live-trading disable confirmation is required")
            policy.live_trading_enabled = False
            policy.kill_switch_active = True
            account_execution_id = None
            action = TradingSafetyAction.LIVE_DISABLED
        policy.updated_at = reference_now
        self._audit_policy(policy, action, account_execution_id)
        await self.session.commit()
        return policy

    async def activate_kill_switch(
        self, owner_id: UUID, workspace_id: UUID, *, now: datetime | None = None
    ) -> TradingSafetyPolicy:
        policy = await self.repository.get_trading_policy(owner_id, workspace_id, for_update=True)
        if policy is None:
            raise FinanceConflictError("live trading risk policy is required")
        policy.kill_switch_active = True
        policy.live_trading_enabled = False
        policy.updated_at = now or datetime.now(timezone.utc)
        self._audit_policy(policy, TradingSafetyAction.KILL_SWITCH_ACTIVATED, None)
        await self.session.commit()
        return policy

    async def place_live_order(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        command: LiveOrderCommand,
        *,
        now: datetime | None = None,
    ) -> BrokerOrderRecord:
        reference_now = now or datetime.now(timezone.utc)
        client_hash = digest(command.client_order_key)
        request_body = {
            "asset_class": command.asset_class.value,
            "symbol": command.symbol,
            "venue": command.venue,
            "currency": command.currency,
            "side": command.side.value,
            "quantity_micros": command.quantity_micros,
            "limit_price_minor": command.limit_price_minor,
            "client_order_id": command.client_order_key,
        }
        request_hash = digest(canonical_json(request_body))
        policy = await self.repository.get_trading_policy(
            owner_id, workspace_id, for_update=True
        )
        if policy is None or policy.broker_connector_id is None or policy.account_path is None:
            raise FinanceConflictError("live trading risk policy is required")
        # The policy row is the workspace-level submission mutex.  Look up the
        # idempotency record only after taking that lock so two concurrent
        # first-use requests cannot both reach the broker before either record
        # exists.
        prior = await self.repository.get_broker_order_by_client_hash(
            owner_id, workspace_id, client_hash, for_update=True
        )
        if prior is not None:
            if prior.request_sha256 != request_hash:
                raise FinanceConflictError("broker idempotency key was reused for another order")
            # Release the policy/idempotency row locks before returning the
            # already-verified result.  Keeping this read transaction open
            # would block other submissions for the workspace.
            await self.session.commit()
            return prior
        LiveTradingSafetyWall.require_local(policy, command, now=reference_now)
        account, _, provider = await self._account(
            owner_id, policy.broker_connector_id, policy.account_path, now=reference_now
        )
        quote = await self.market_data.quote(
            owner_id,
            command.market_data_connector_id,
            path=command.quote_path,
            max_age_seconds=command.max_quote_age_seconds,
            now=reference_now,
        )
        notional = LiveTradingSafetyWall.require(
            policy, command, account, quote, now=reference_now
        )
        assert policy.order_path is not None and policy.order_status_prefix is not None
        submitted = await self.connector_service.execute_for_owner(
            owner_id,
            policy.broker_connector_id,
            method="POST",
            path=policy.order_path,
            json_body=request_body,
            idempotency_key=command.client_order_key,
            required_capability="broker.order.submit",
        )
        payload = submitted.payload
        required_ack = {"provider", "account_id", "client_order_id", "order_id", "status", "status_path"}
        if not isinstance(payload, dict) or frozenset(payload) != required_ack:
            raise FinanceConflictError("broker order acknowledgement is invalid")
        order_id = _bounded_string(payload["order_id"], "order identity", 256)
        status_path = _path(payload["status_path"], "order status path")
        if (
            payload["provider"] != provider
            or payload["account_id"] != account.account_id
            or payload["client_order_id"] != command.client_order_key
            or payload["status"] not in {"accepted", "open", "filled"}
            or not status_path.startswith(policy.order_status_prefix)
        ):
            raise FinanceConflictError("broker order acknowledgement did not match the request")
        verified = await self.connector_service.execute_for_owner(
            owner_id,
            policy.broker_connector_id,
            method="GET",
            path=status_path,
            json_body=None,
            idempotency_key=None,
            required_capability="broker.order.status",
        )
        status_payload = verified.payload
        required_status = {
            "provider", "account_id", "client_order_id", "order_id", "status", "filled_quantity_micros"
        }
        if not isinstance(status_payload, dict) or frozenset(status_payload) != required_status:
            raise FinanceConflictError("broker order status response is invalid")
        status_map = {
            "open": BrokerOrderStatus.VERIFIED_OPEN,
            "filled": BrokerOrderStatus.VERIFIED_FILLED,
            "cancelled": BrokerOrderStatus.VERIFIED_CANCELLED,
        }
        status_value = status_map.get(status_payload["status"])
        filled = _integer(status_payload["filled_quantity_micros"], "filled quantity")
        if (
            status_value is None
            or status_payload["provider"] != provider
            or status_payload["account_id"] != account.account_id
            or status_payload["client_order_id"] != command.client_order_key
            or status_payload["order_id"] != order_id
            or filled > command.quantity_micros
            or (status_value is BrokerOrderStatus.VERIFIED_FILLED and filled != command.quantity_micros)
        ):
            raise FinanceConflictError("broker order status did not verify the intended order")
        record = BrokerOrderRecord(
            workspace_id=workspace_id,
            owner_id=owner_id,
            broker_connector_id=policy.broker_connector_id,
            asset_class=command.asset_class,
            symbol=command.symbol,
            venue=command.venue,
            currency=command.currency,
            side=command.side,
            quantity_micros=command.quantity_micros,
            limit_price_minor=command.limit_price_minor,
            notional_minor=notional,
            filled_quantity_micros=filled,
            client_order_key_sha256=client_hash,
            request_sha256=request_hash,
            provider_order_sha256=digest(order_id),
            status=status_value,
            submit_execution_id=submitted.execution.id,
            status_execution_id=verified.execution.id,
            verified_at=reference_now,
        )
        self.session.add(record)
        await self.session.commit()
        return record
