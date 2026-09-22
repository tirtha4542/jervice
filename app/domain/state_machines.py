from datetime import datetime
from enum import StrEnum


class TableStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    SEATED = "seated"
    DIRTY = "dirty"
    BLOCKED = "blocked"


class TableSessionStatus(StrEnum):
    OPEN = "open"
    ORDERING = "ordering"
    DINING = "dining"
    SETTLING = "settling"
    CLOSED = "closed"


class GuestSessionStatus(StrEnum):
    AUTHENTICATING = "authenticating"
    JOINED = "joined"
    ORDERING = "ordering"
    DINING = "dining"
    SETTLING = "settling"
    LEFT = "left"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_KITCHEN = "in_kitchen"
    READY = "ready"
    SERVED = "served"
    CANCELLED = "cancelled"


class OrderItemStatus(StrEnum):
    QUEUED = "queued"
    PREPPING = "prepping"
    FIRED = "fired"
    READY = "ready"
    PICKED_UP = "picked_up"
    VOIDED = "voided"


class PaymentStatus(StrEnum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    AUTHORIZED = "authorized"
    SETTLED = "settled"
    FAILED = "failed"
    REFUNDED = "refunded"


class ReservationStatus(StrEnum):
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    SEATED = "seated"
    NO_SHOW = "no_show"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class InvalidStateTransition(ValueError):
    pass


TRANSITIONS: dict[type[StrEnum], dict[StrEnum, set[StrEnum]]] = {
    TableStatus: {
        TableStatus.AVAILABLE: {TableStatus.RESERVED, TableStatus.SEATED, TableStatus.BLOCKED},
        TableStatus.RESERVED: {TableStatus.SEATED, TableStatus.AVAILABLE, TableStatus.BLOCKED},
        TableStatus.SEATED: {TableStatus.DIRTY, TableStatus.AVAILABLE},
        TableStatus.DIRTY: {TableStatus.AVAILABLE, TableStatus.BLOCKED},
        TableStatus.BLOCKED: {TableStatus.AVAILABLE},
    },
    TableSessionStatus: {
        TableSessionStatus.OPEN: {TableSessionStatus.ORDERING, TableSessionStatus.CLOSED},
        TableSessionStatus.ORDERING: {TableSessionStatus.DINING, TableSessionStatus.CLOSED},
        TableSessionStatus.DINING: {TableSessionStatus.SETTLING, TableSessionStatus.ORDERING},
        TableSessionStatus.SETTLING: {TableSessionStatus.CLOSED, TableSessionStatus.DINING},
        TableSessionStatus.CLOSED: set(),
    },
    GuestSessionStatus: {
        GuestSessionStatus.AUTHENTICATING: {GuestSessionStatus.JOINED, GuestSessionStatus.LEFT},
        GuestSessionStatus.JOINED: {GuestSessionStatus.ORDERING, GuestSessionStatus.LEFT},
        GuestSessionStatus.ORDERING: {GuestSessionStatus.DINING, GuestSessionStatus.LEFT},
        GuestSessionStatus.DINING: {GuestSessionStatus.SETTLING, GuestSessionStatus.ORDERING},
        GuestSessionStatus.SETTLING: {GuestSessionStatus.LEFT, GuestSessionStatus.DINING},
        GuestSessionStatus.LEFT: set(),
    },
    OrderStatus: {
        OrderStatus.DRAFT: {OrderStatus.SUBMITTED, OrderStatus.CANCELLED},
        OrderStatus.SUBMITTED: {OrderStatus.IN_KITCHEN, OrderStatus.CANCELLED},
        OrderStatus.IN_KITCHEN: {OrderStatus.READY, OrderStatus.CANCELLED},
        OrderStatus.READY: {OrderStatus.SERVED, OrderStatus.CANCELLED},
        OrderStatus.SERVED: set(),
        OrderStatus.CANCELLED: set(),
    },
    OrderItemStatus: {
        OrderItemStatus.QUEUED: {OrderItemStatus.PREPPING, OrderItemStatus.VOIDED},
        OrderItemStatus.PREPPING: {OrderItemStatus.FIRED, OrderItemStatus.VOIDED},
        OrderItemStatus.FIRED: {OrderItemStatus.READY, OrderItemStatus.VOIDED},
        OrderItemStatus.READY: {OrderItemStatus.PICKED_UP, OrderItemStatus.VOIDED},
        OrderItemStatus.PICKED_UP: set(),
        OrderItemStatus.VOIDED: set(),
    },
    PaymentStatus: {
        PaymentStatus.UNPAID: {PaymentStatus.PARTIAL, PaymentStatus.AUTHORIZED, PaymentStatus.FAILED},
        PaymentStatus.PARTIAL: {PaymentStatus.AUTHORIZED, PaymentStatus.SETTLED, PaymentStatus.FAILED},
        PaymentStatus.AUTHORIZED: {PaymentStatus.SETTLED, PaymentStatus.FAILED, PaymentStatus.REFUNDED},
        PaymentStatus.SETTLED: {PaymentStatus.REFUNDED},
        PaymentStatus.FAILED: {PaymentStatus.UNPAID, PaymentStatus.AUTHORIZED},
        PaymentStatus.REFUNDED: set(),
    },
    ReservationStatus: {
        ReservationStatus.REQUESTED: {ReservationStatus.CONFIRMED, ReservationStatus.CANCELLED},
        ReservationStatus.CONFIRMED: {
            ReservationStatus.SEATED,
            ReservationStatus.NO_SHOW,
            ReservationStatus.CANCELLED,
        },
        ReservationStatus.SEATED: {ReservationStatus.COMPLETED},
        ReservationStatus.NO_SHOW: set(),
        ReservationStatus.CANCELLED: set(),
        ReservationStatus.COMPLETED: set(),
    },
}


def can_transition(enum_cls: type[StrEnum], current: StrEnum, target: StrEnum) -> bool:
    return target in TRANSITIONS[enum_cls].get(current, set())


def apply_transition(enum_cls: type[StrEnum], current: StrEnum, target: StrEnum) -> StrEnum:
    if not can_transition(enum_cls, current, target):
        raise InvalidStateTransition(f"{enum_cls.__name__}: {current} -> {target} is not allowed")
    return target


def utcnow() -> datetime:
    return datetime.utcnow()
