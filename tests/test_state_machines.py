from app.domain.state_machines import (
    InvalidStateTransition,
    TableSessionStatus,
    TableStatus,
    apply_transition,
    can_transition,
)


def test_table_and_session_are_independent():
    assert can_transition(TableStatus, TableStatus.AVAILABLE, TableStatus.SEATED)
    assert can_transition(TableSessionStatus, TableSessionStatus.OPEN, TableSessionStatus.ORDERING)
    seated = apply_transition(TableStatus, TableStatus.AVAILABLE, TableStatus.SEATED)
    ordering = apply_transition(TableSessionStatus, TableSessionStatus.OPEN, TableSessionStatus.ORDERING)
    assert seated == TableStatus.SEATED
    assert ordering == TableSessionStatus.ORDERING


def test_rejected_transition():
    try:
        apply_transition(TableStatus, TableStatus.AVAILABLE, TableStatus.DIRTY)
        assert False, "expected InvalidStateTransition"
    except InvalidStateTransition:
        pass
