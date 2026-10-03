"""Mojaloop settlement objects.

A settlement window is the natural period boundary for reconciliation. A
treasury team does not reconcile "the last 7 days"; it reconciles "the window
that closed last night". Persisting windows lets a reconciliation run be scoped
to one, recorded against it, and re-run later over exactly the same boundary.

Shapes follow the Mojaloop central-settlement API (``settlements_1.0`` /
``settlements_2.0`` in the Testing Toolkit):

* ``GET /settlementWindows``      -> [{id, state, reason, createdDate, changedDate}]
* ``GET /settlementWindows/{id}`` -> single window
* ``GET /settlements``            -> [{id, state, settlementWindows[], participants[]}]
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.core.constants import SettlementWindowState
from app.database.db import Base


class SettlementWindow(Base):
    """A settlement window pulled from the hub.

    ``window_id`` is the hub's identifier and is the natural key; ``id`` is only
    a surrogate. ``reconciled_at`` / ``last_run_id`` record whether this window
    has already been reconciled, which is what lets the scheduler pick up only
    windows that have newly closed.
    """

    __tablename__ = "settlement_windows"

    id = Column(Integer, primary_key=True, index=True)
    window_id = Column(String, unique=True, index=True, nullable=False)
    state = Column(String, default=SettlementWindowState.OPEN.value, index=True)
    reason = Column(Text, nullable=True)

    # Hub-reported lifecycle timestamps. ``created_date`` opens the window and
    # ``changed_date`` is when it last transitioned (typically when it closed).
    created_date = Column(DateTime, nullable=True, index=True)
    changed_date = Column(DateTime, nullable=True, index=True)

    # Our bookkeeping.
    synced_at = Column(DateTime, default=datetime.utcnow, index=True)
    reconciled_at = Column(DateTime, nullable=True, index=True)
    last_run_id = Column(Integer, nullable=True, index=True)
    transactions_in_window = Column(Integer, default=0)
    raw_payload = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_settlement_windows_state_changed", "state", "changed_date"),
    )

    @property
    def is_closed(self) -> bool:
        """True once the hub has stopped accepting transfers into this window."""
        return self.state in {
            SettlementWindowState.CLOSED.value,
            SettlementWindowState.PENDING_SETTLEMENT.value,
            SettlementWindowState.SETTLED.value,
        }


class Settlement(Base):
    """A settlement covering one or more windows."""

    __tablename__ = "settlements"

    id = Column(Integer, primary_key=True, index=True)
    settlement_id = Column(String, unique=True, index=True, nullable=False)
    state = Column(String, nullable=True, index=True)
    settlement_model = Column(String, nullable=True)
    reason = Column(Text, nullable=True)

    created_date = Column(DateTime, nullable=True, index=True)
    changed_date = Column(DateTime, nullable=True)

    # Comma-separated window ids this settlement covers. Kept denormalised
    # because the hub returns them inline and we only ever read them as a set.
    window_ids = Column(Text, nullable=True)
    participant_count = Column(Integer, default=0)
    net_amount = Column(Float, nullable=True)
    currency = Column(String, nullable=True)

    synced_at = Column(DateTime, default=datetime.utcnow)
    raw_payload = Column(Text, nullable=True)
