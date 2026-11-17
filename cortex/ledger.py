"""Event-sourced dual-inventory ledger.

Balances are never mutated in place: they are a fold over an append-only
event stream. That makes every holding, fee and reservation reconstructable
and makes the reconciliation gate (Gate 2) a real check rather than a label.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .config import Config
from .domain import (
    LedgerState,
    Reservation,
    content_hash,
    dec_str,
    iso,
    q,
    utc_now,
)

ZERO = Decimal("0")


class LedgerError(ValueError):
    """Raised when an event would violate an accounting invariant."""


class EventType(StrEnum):
    SEED = "SEED"
    ADJUST = "ADJUST"
    RESERVE = "RESERVE"
    RELEASE = "RELEASE"
    FILL = "FILL"


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    seq: int
    event_type: EventType
    created_at: datetime
    order_id: str | None = None
    asset: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    fee_usdt: Decimal | None = None
    side: str | None = None
    btc_core: Decimal | None = None
    btc_tactical: Decimal | None = None
    usdt_core: Decimal | None = None
    usdt_tactical: Decimal | None = None
    note: str = ""
    actor: str = "operator"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type.value,
            "created_at": iso(self.created_at),
            "order_id": self.order_id,
            "asset": self.asset,
            "quantity": dec_str(self.quantity),
            "price": dec_str(self.price),
            "fee_usdt": dec_str(self.fee_usdt),
            "side": self.side,
            "btc_core": dec_str(self.btc_core),
            "btc_tactical": dec_str(self.btc_tactical),
            "usdt_core": dec_str(self.usdt_core),
            "usdt_tactical": dec_str(self.usdt_tactical),
            "note": self.note,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LedgerEvent":
        from .domain import D, ensure_utc

        def opt(name: str) -> Decimal | None:
            raw = data.get(name)
            return None if raw is None else D(raw)

        return cls(
            seq=int(data["seq"]),
            event_type=EventType(data["event_type"]),
            created_at=ensure_utc(datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))),
            order_id=data.get("order_id"),
            asset=data.get("asset"),
            quantity=opt("quantity"),
            price=opt("price"),
            fee_usdt=opt("fee_usdt"),
            side=data.get("side"),
            btc_core=opt("btc_core"),
            btc_tactical=opt("btc_tactical"),
            usdt_core=opt("usdt_core"),
            usdt_tactical=opt("usdt_tactical"),
            note=str(data.get("note", "")),
            actor=str(data.get("actor", "operator")),
        )


@dataclass
class _Mutable:
    btc_core: Decimal = ZERO
    btc_tactical: Decimal = ZERO
    usdt_core: Decimal = ZERO
    usdt_tactical: Decimal = ZERO
    realised_usdt: Decimal = ZERO
    fees_paid: Decimal = ZERO
    cost_basis: Decimal = ZERO
    cost_qty: Decimal = ZERO
    reservations: dict[str, Reservation] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.reservations is None:
            self.reservations = {}


class Ledger:
    def __init__(self, events: Iterable[LedgerEvent] = ()) -> None:
        self._events: list[LedgerEvent] = []
        for event in events:
            self.append(event)

    # -- reading -----------------------------------------------------------
    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)

    @property
    def next_seq(self) -> int:
        return len(self._events)

    def event_hash(self) -> str:
        return content_hash([e.to_dict() for e in self._events])

    def state(self) -> LedgerState:
        s = self._fold()
        reservations = tuple(
            sorted(self._active_reservations(s).values(), key=lambda r: (r.created_at, r.order_id))
        )
        return LedgerState(
            btc_core=s.btc_core,
            btc_tactical=s.btc_tactical,
            usdt_core=s.usdt_core,
            usdt_tactical=s.usdt_tactical,
            reservations=reservations,
            realised_usdt=s.realised_usdt,
            fees_paid_usdt=s.fees_paid,
            buy_cost_basis_usdt=s.cost_basis,
            buy_cost_quantity=s.cost_qty,
        )

    def average_cost(self) -> Decimal | None:
        s = self._fold()
        if s.cost_qty <= ZERO:
            return None
        return s.cost_basis / s.cost_qty

    # -- writing -----------------------------------------------------------
    def append(self, event: LedgerEvent) -> LedgerEvent:
        if event.seq != self.next_seq:
            raise LedgerError(f"event sequence gap: expected {self.next_seq}, got {event.seq}")
        candidate = self._events + [event]
        self._validate(candidate)
        self._events.append(event)
        return event

    def seed(
        self,
        *,
        btc_core: Decimal,
        btc_tactical: Decimal,
        usdt_core: Decimal,
        usdt_tactical: Decimal,
        actor: str = "operator",
        note: str = "paper balances set by operator",
        at: datetime | None = None,
    ) -> LedgerEvent:
        if any(v < ZERO for v in (btc_core, btc_tactical, usdt_core, usdt_tactical)):
            raise LedgerError("seed balances cannot be negative")
        return self.append(
            LedgerEvent(
                seq=self.next_seq,
                event_type=EventType.SEED,
                created_at=at or utc_now(),
                btc_core=btc_core,
                btc_tactical=btc_tactical,
                usdt_core=usdt_core,
                usdt_tactical=usdt_tactical,
                note=note,
                actor=actor,
            )
        )

    def reserve(self, *, order_id: str, asset: str, quantity: Decimal, note: str = "") -> LedgerEvent:
        return self.append(
            LedgerEvent(
                seq=self.next_seq,
                event_type=EventType.RESERVE,
                created_at=utc_now(),
                order_id=order_id,
                asset=asset,
                quantity=q(quantity),
                note=note,
            )
        )

    def release(self, *, order_id: str, note: str = "cancellation confirmed") -> LedgerEvent:
        return self.append(
            LedgerEvent(
                seq=self.next_seq,
                event_type=EventType.RELEASE,
                created_at=utc_now(),
                order_id=order_id,
                note=note,
            )
        )

    def fill(
        self,
        *,
        order_id: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        fee_usdt: Decimal,
        asset: str | None = None,
        note: str = "",
    ) -> LedgerEvent:
        reservation = self._active_reservations(self._fold()).get(order_id)
        if reservation is None:
            raise LedgerError(f"no active reservation for order {order_id}")
        expected = "BTC" if side == "SELL" else "USDT"
        if reservation.asset != (asset or expected):
            raise LedgerError(
                f"reservation asset {reservation.asset} does not match fill side {side}"
            )
        if quantity > reservation.quantity:
            raise LedgerError("fill quantity exceeds reserved quantity")
        return self.append(
            LedgerEvent(
                seq=self.next_seq,
                event_type=EventType.FILL,
                created_at=utc_now(),
                order_id=order_id,
                asset=asset or expected,
                side=side,
                quantity=q(quantity),
                price=price,
                fee_usdt=fee_usdt,
                note=note,
            )
        )

    def adjust(self, *, asset: str, delta: Decimal, note: str) -> LedgerEvent:
        return self.append(
            LedgerEvent(
                seq=self.next_seq,
                event_type=EventType.ADJUST,
                created_at=utc_now(),
                asset=asset,
                quantity=delta,
                note=note,
            )
        )

    # -- internals ---------------------------------------------------------
    def _validate(self, events: list[LedgerEvent]) -> None:
        s = _Mutable()
        for event in events:
            self._apply(s, event, strict=True)
        if any(
            v < ZERO
            for v in (s.btc_core, s.btc_tactical, s.usdt_core, s.usdt_tactical)
        ):
            raise LedgerError("event sequence would drive a balance negative")

    def _fold(self) -> _Mutable:
        s = _Mutable()
        for event in self._events:
            self._apply(s, event, strict=False)
        return s

    @staticmethod
    def _active_reservations(s: _Mutable) -> dict[str, Reservation]:
        return {key: value for key, value in s.reservations.items() if value.quantity > ZERO}

    def _apply(self, s: _Mutable, event: LedgerEvent, *, strict: bool) -> None:
        if event.event_type is EventType.SEED:
            s.btc_core = event.btc_core or ZERO
            s.btc_tactical = event.btc_tactical or ZERO
            s.usdt_core = event.usdt_core or ZERO
            s.usdt_tactical = event.usdt_tactical or ZERO
            s.reservations.clear()
            return

        if event.event_type is EventType.ADJUST:
            if event.asset == "BTC":
                s.btc_tactical += event.quantity or ZERO
            elif event.asset == "USDT":
                s.usdt_tactical += event.quantity or ZERO
            else:
                raise LedgerError(f"unsupported adjustment asset {event.asset!r}")
            return

        if event.event_type is EventType.RESERVE:
            if not event.order_id or not event.asset or event.quantity is None:
                raise LedgerError("reservation requires order id, asset and quantity")
            if event.order_id in s.reservations:
                raise LedgerError(f"order {event.order_id} already holds a reservation")
            if event.quantity <= ZERO:
                raise LedgerError("reservation quantity must be positive")
            if event.asset == "BTC":
                already = sum(
                    (r.quantity for r in s.reservations.values() if r.asset == "BTC"), ZERO
                )
                if event.quantity + already > s.btc_tactical:
                    raise LedgerError("BTC reservation exceeds unreserved tactical inventory")
            if event.asset == "USDT":
                already = sum(
                    (r.quantity for r in s.reservations.values() if r.asset == "USDT"), ZERO
                )
                if event.quantity + already > s.usdt_tactical:
                    raise LedgerError("USDT reservation exceeds unreserved tactical inventory")
            s.reservations[event.order_id] = Reservation(
                order_id=event.order_id,
                asset=event.asset,
                quantity=event.quantity,
                created_at=event.created_at,
                note=event.note,
            )
            return

        if event.event_type is EventType.RELEASE:
            if not event.order_id:
                raise LedgerError("release requires an order id")
            if event.order_id not in s.reservations:
                raise LedgerError(f"no active reservation for order {event.order_id}")
            del s.reservations[event.order_id]
            return

        if event.event_type is EventType.FILL:
            if not event.order_id:
                raise LedgerError("fill requires an order id")
            reservation = s.reservations.get(event.order_id)
            if reservation is None:
                raise LedgerError(f"no active reservation for order {event.order_id}")
            quantity = event.quantity or ZERO
            price = event.price or ZERO
            fee = event.fee_usdt or ZERO

            if event.side == "BUY":
                gross = quantity * price
                if gross + fee > s.usdt_tactical:
                    raise LedgerError("buy fill exceeds USDT inventory")
                s.usdt_tactical -= gross + fee
                s.btc_tactical += quantity
                s.cost_basis += gross
                s.cost_qty += quantity
                s.fees_paid += fee
            elif event.side == "SELL":
                if quantity > s.btc_tactical:
                    raise LedgerError("sell fill exceeds BTC inventory")
                proceeds = quantity * price
                average = (s.cost_basis / s.cost_qty) if s.cost_qty > ZERO else None
                s.btc_tactical -= quantity
                s.usdt_tactical += proceeds - fee
                s.fees_paid += fee
                if average is not None:
                    matched = average * quantity
                    s.cost_basis -= matched
                    s.cost_qty -= quantity
                    s.realised_usdt += proceeds - fee - matched
            else:
                raise LedgerError(f"unsupported fill side {event.side!r}")

            remaining = reservation.quantity - quantity
            if remaining > ZERO:
                s.reservations[event.order_id] = Reservation(
                    order_id=reservation.order_id,
                    asset=reservation.asset,
                    quantity=remaining,
                    created_at=reservation.created_at,
                    note=reservation.note,
                )
            else:
                del s.reservations[event.order_id]
            return

        raise LedgerError(f"unsupported event type {event.event_type!r}")


# --------------------------------------------------------------------------
# availability rules — the only sanctioned source of tradable quantity
# --------------------------------------------------------------------------


def available_btc(state: LedgerState, config: Config) -> Decimal:
    floor = config.portfolio.min_btc_inventory
    if floor is None:
        return ZERO
    tradable = state.btc_tactical - state.reserved("BTC")
    shortfall = max(ZERO, floor - state.btc_total)
    return max(ZERO, tradable - shortfall)


def available_usdt(state: LedgerState, config: Config) -> Decimal:
    floor = config.portfolio.min_usdt_inventory
    if floor is None:
        return ZERO
    return max(ZERO, state.usdt_tactical - state.reserved("USDT") - floor)


def reconcile(
    state: LedgerState,
    *,
    reported_btc: Decimal | None,
    reported_usdt: Decimal | None,
    tolerance_btc: Decimal = Decimal("0.00000001"),
    tolerance_usdt: Decimal = Decimal("0.01"),
) -> tuple[bool, list[str]]:
    """Compare the internal ledger with externally reported balances."""
    problems: list[str] = []
    if reported_btc is None:
        problems.append("BTC balance not reported")
    elif abs(state.btc_total - reported_btc) > tolerance_btc:
        problems.append(
            f"BTC mismatch: ledger {dec_str(state.btc_total)} vs reported {dec_str(reported_btc)}"
        )
    if reported_usdt is None:
        problems.append("USDT balance not reported")
    elif abs(state.usdt_total - reported_usdt) > tolerance_usdt:
        problems.append(
            f"USDT mismatch: ledger {dec_str(state.usdt_total)} vs reported {dec_str(reported_usdt)}"
        )
    return (not problems, problems)


def integrity_check(ledger: Ledger) -> tuple[bool, list[str]]:
    problems: list[str] = []
    try:
        state = ledger.state()
    except LedgerError as error:  # pragma: no cover - defensive
        return False, [str(error)]
    if state.btc_core < ZERO or state.btc_tactical < ZERO:
        problems.append("negative BTC balance")
    if state.usdt_core < ZERO or state.usdt_tactical < ZERO:
        problems.append("negative USDT balance")
    if state.reserved("BTC") > state.btc_tactical:
        problems.append("BTC reservations exceed tactical inventory")
    if state.reserved("USDT") > state.usdt_tactical:
        problems.append("USDT reservations exceed tactical inventory")
    seen: set[str] = set()
    for reservation in state.reservations:
        if reservation.order_id in seen:
            problems.append(f"duplicate reservation for {reservation.order_id}")
        seen.add(reservation.order_id)
    return (not problems, problems)


__all__ = [
    "EventType",
    "Ledger",
    "LedgerError",
    "LedgerEvent",
    "available_btc",
    "available_usdt",
    "integrity_check",
    "reconcile",
]
