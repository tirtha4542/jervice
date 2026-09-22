import pytest
from app.domain.state_machines import (
    InvalidStateTransition,
    TableSessionStatus,
    TableStatus,
    apply_transition,
    can_transition,
)


def test_new_feature_table_and_session_independent():
    """Verify that physical table status and service session status operate independently."""
    assert can_transition(TableStatus, TableStatus.AVAILABLE, TableStatus.SEATED)
    assert can_transition(TableSessionStatus, TableSessionStatus.OPEN, TableSessionStatus.ORDERING)
    
    seated = apply_transition(TableStatus, TableStatus.AVAILABLE, TableStatus.SEATED)
    ordering = apply_transition(TableSessionStatus, TableSessionStatus.OPEN, TableSessionStatus.ORDERING)
    
    assert seated == TableStatus.SEATED
    assert ordering == TableSessionStatus.ORDERING


def test_new_feature_rejected_transition():
    """Verify that invalid state transitions properly trigger an InvalidStateTransition exception."""
    with pytest.raises(InvalidStateTransition):
        apply_transition(TableStatus, TableStatus.AVAILABLE, TableStatus.DIRTY)