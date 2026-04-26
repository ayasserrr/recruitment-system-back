"""
PipelineController — single source of truth for JobRequisition status transitions.

Why this exists
───────────────
Previously, every task file called `jr.status = "some_string"` directly.
There was no enforcement that the transition was legal, no central place to
add guards, and no audit trail.  This controller fixes all three:

  1. Enforces the state machine — illegal transitions raise immediately.
  2. Single commit point — status + updated_at are written atomically.
  3. Optional bypass — validate=False for internal recovery flows.

Status machine
──────────────
  Draft
    → Active           (after LinkedIn/posting pipeline starts)
  Active / published / failed
    → ranked           (after CV ranking completes)
  ranked
    → assessment_sent  (after assessment invitations dispatched)
    → ranking_complete (when no assessment config exists — skip straight to final)
  assessment_sent
    → assessment_ranked (after post-deadline no-show marking + pool ranking)
  assessment_ranked
    → interview_pending (after interview invitations dispatched)
    → ranking_complete  (when no interview config — skip straight to final)
  interview_pending
    → ranking_complete  (after final ranking computed)
  ranking_complete     [TERMINAL — no further transitions]

Usage
─────
    from controllers.pipeline_controller import PipelineController

    # Inside a task, after the graph finishes:
    with SessionLocal() as db:   # or the standard try/finally pattern
        PipelineController.transition(db, requisition_id, "ranked")
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from models.db.job_requisition import JobRequisition

logger = logging.getLogger(__name__)


# ── State machine definition ──────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[str, list[str]] = {
    "Draft": ["Active"],
    "Active": ["published", "failed", "ranked"],
    "published": ["ranked", "failed"],
    "failed": ["ranked"],           # manual re-trigger after operator intervention
    "ranked": ["assessment_sent", "ranking_complete"],
    "assessment_sent": ["assessment_ranked"],
    "assessment_ranked": ["interview_pending", "ranking_complete"],
    "interview_pending": ["ranking_complete"],
    "ranking_complete": [],         # terminal — no further transitions
}

# Statuses where a Celery scanner should NOT dispatch (job already done or running).
SKIP_STATUSES: frozenset[str] = frozenset({
    "ranking_complete",
})

# Statuses that indicate the full pipeline has finished.
TERMINAL_STATUSES: frozenset[str] = frozenset({"ranking_complete"})


# ── Exceptions ────────────────────────────────────────────────────────────────

class InvalidStatusTransitionError(ValueError):
    """Raised when a requested status transition is not in the valid map."""


class RequisitionNotFoundError(LookupError):
    """Raised when no JobRequisition row matches the given requisition_id."""


# ── Controller ────────────────────────────────────────────────────────────────

class PipelineController:
    """
    Static-method controller — no instance state.  All DB work is done within
    the caller-supplied session so the caller controls the transaction boundary.
    """

    @staticmethod
    def transition(
        db: Session,
        requisition_id: int,
        new_status: str,
        *,
        validate: bool = True,
    ) -> JobRequisition:
        """
        Atomically transition a JobRequisition to *new_status*.

        Uses SELECT FOR UPDATE to block concurrent transitions on the same row.
        Refreshes updated_at unconditionally.

        Args:
            db:               Active SQLAlchemy session — caller must commit/rollback.
            requisition_id:   PK of the target JobRequisition.
            new_status:       Target status string.
            validate:         If False, skip the state-machine check.
                              Use only for recovery or seeding flows.

        Returns:
            The updated (but not yet committed) JobRequisition ORM object.

        Raises:
            RequisitionNotFoundError:     JR row doesn't exist.
            InvalidStatusTransitionError: Transition not in the state machine.
        """
        jr: Optional[JobRequisition] = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .with_for_update()
            .first()
        )
        if jr is None:
            raise RequisitionNotFoundError(
                f"JobRequisition {requisition_id} not found."
            )

        old_status = jr.status

        if validate:
            allowed = _VALID_TRANSITIONS.get(old_status, [])
            if new_status not in allowed:
                raise InvalidStatusTransitionError(
                    f"JR {requisition_id}: '{old_status}' → '{new_status}' is not a valid "
                    f"transition.  Allowed from '{old_status}': {allowed}"
                )

        jr.status = new_status
        jr.updated_at = datetime.utcnow()

        logger.info(
            "[pipeline_ctrl] JR %d: '%s' → '%s'",
            requisition_id,
            old_status,
            new_status,
        )
        # Caller is responsible for db.commit() / db.rollback()
        return jr

    @staticmethod
    def get_status(db: Session, requisition_id: int) -> Optional[str]:
        """Return current jr.status, or None if the JR doesn't exist."""
        jr = (
            db.query(JobRequisition)
            .filter(JobRequisition.requisition_id == requisition_id)
            .first()
        )
        return jr.status if jr else None

    @staticmethod
    def is_terminal(status: str) -> bool:
        """True if the given status is a terminal (no-more-transitions) state."""
        return status in TERMINAL_STATUSES

    @staticmethod
    def allowed_transitions(current_status: str) -> list[str]:
        """Return the list of valid next statuses from *current_status*."""
        return list(_VALID_TRANSITIONS.get(current_status, []))

    @staticmethod
    def guard_no_duplicate(
        db: Session,
        requisition_id: int,
        expected_status: str,
    ) -> bool:
        """
        Idempotency guard used at the START of a worker task.

        Returns True  → status matches *expected_status*; safe to proceed.
        Returns False → status has already advanced past *expected_status*;
                        task should return early (work already done).

        Example usage in a Celery task::

            if not PipelineController.guard_no_duplicate(db, req_id, "ranked"):
                return {"skipped": True, "reason": "status_already_advanced"}
        """
        current = PipelineController.get_status(db, requisition_id)
        if current != expected_status:
            logger.info(
                "[pipeline_ctrl] JR %d guard failed: expected '%s', found '%s' — skipping.",
                requisition_id,
                expected_status,
                current,
            )
            return False
        return True
