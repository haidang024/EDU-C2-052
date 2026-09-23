"""BriefingHitlNode — assembles access-history briefing and requests human review."""

from __future__ import annotations

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json


class BriefingHitlNode(FunctionNode):
    """Assemble the data-access request history briefing and request human review.

    Uses the D6 interrupt() pattern: when hitl_allowed=True the session suspends
    for operator review before the briefing is finalized. When hitl_allowed=False
    (batch/automated mode) the draft is auto-approved without raising interrupt().

    On resume, handles three outcomes:
      approve   → review_outcome=approved
      correct   → review_outcome=corrected, review_correction=<text>
      reject    → review_outcome=rejected, status=error

    The briefing is clearly labeled as a factual summary for human review and
    never constitutes an automated access decision.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        # Static config, read once at construction — not mutable per-invocation
        # state (node instances are shared across invocations).
        self._config = dict(config or {})

    def _hitl_resumable(self) -> bool:
        """True only when this deployment can actually resume an interrupt().

        Requires `hitl.enabled: true` AND `memory_enabled: true` — the latter is
        what attaches the checkpointer that makes pause/resume possible. The
        Marketplace entry point builds the graph with no config at all, so this
        is False there and the run completes instead of suspending forever.
        """
        hitl = self._config.get("hitl") or {}
        if not isinstance(hitl, dict) or not hitl.get("enabled", False):
            return False
        return bool(self._config.get("memory_enabled", False))

    def execute(self, state: dict) -> dict:
        # Idempotency guard: if draft already assembled and we're resuming,
        # skip expensive re-assembly and go straight to review processing.
        existing_draft = state.get("briefing_draft_json", "")
        hitl_feedback = state.get("hitl_feedback")

        if existing_draft and hitl_feedback:
            return self._process_review(state, hitl_feedback)

        # Assemble briefing draft from upstream data
        criteria = from_json(state.get("request_criteria_json"), default={})
        history = from_json(state.get("request_history_json"), default=[])
        policy_refs = from_json(state.get("policy_references_json"), default=[])
        citations = from_json(state.get("citations_json"), default=[])

        draft = {
            "notice": (
                "This briefing is a factual summary for authorized human review only. "
                "It does not constitute an automated data-access decision or disclosure."
            ),
            "request_criteria": criteria,
            "history_record_count": len(history),
            "history_summary": self._summarize_history(history),
            "policy_reference_count": len(policy_refs),
            "policy_summary": self._summarize_policies(policy_refs),
            "citations": citations,
            "has_gaps": len(policy_refs) == 0,
        }

        emit_trace_event(
            "BriefingHitlNode_draft_assembled",
            {"history_count": len(history), "policy_count": len(policy_refs), "has_gaps": draft["has_gaps"]},
            state,
        )

        # Two independent conditions must hold before suspending for review:
        #
        #  1. hitl_allowed — the caller permits human review at all (a parent
        #     GraphNode sets False for automated pipelines).
        #  2. hitl_resumable — the *entry point* can actually deliver feedback
        #     back into this graph. A one-shot Marketplace execution cannot:
        #     `shared.bootstrap.marketplace_app` states "AWAITING_HUMAN is
        #     treated as a failure. A one-shot Marketplace execution has no
        #     resume channel." It also constructs the graph as `agent_cls()`
        #     with no config, so no checkpointer is attached and `interrupt()`
        #     can never be resumed — the run just suspends and the chat UI
        #     shows "Thinking..." forever.
        #
        # hitl_allowed alone is the wrong signal: it defaults to True, so an
        # entry point that never sets it would suspend regardless. Requiring an
        # explicitly configured, checkpointed HITL setup fails safe toward
        # "finish the run" instead of "hang".
        hitl_allowed = state.get("hitl_allowed", False)
        if hitl_allowed and self._hitl_resumable():
            from langgraph.types import interrupt

            hitl_feedback = interrupt(
                {
                    "message": "Access-history briefing ready for authorized review.",
                    "briefing_summary": draft["history_summary"],
                    "has_gaps": draft["has_gaps"],
                    "action": "review_required",
                }
            )
            # On Command(resume=...), interrupt() returns the operator feedback.
            return self._process_review(state, hitl_feedback, draft)

        # Batch/automated mode: auto-approve
        return {
            "briefing_draft_json": to_json(draft),
            "review_outcome": "approved",
            "review_correction": "",
            "status": AgentStatus.SUCCESS.value,
        }

    # ------------------------------------------------------------------
    # Review outcome processing
    # ------------------------------------------------------------------

    def _process_review(
        self,
        state: dict,
        hitl_feedback: Any,
        draft: dict | None = None,
    ) -> dict:
        if draft is None:
            draft = from_json(state.get("briefing_draft_json"), default={})

        if isinstance(hitl_feedback, str):
            action = hitl_feedback
            corrected = ""
            reason = ""
        elif isinstance(hitl_feedback, dict):
            action = hitl_feedback.get("action", "approve")
            corrected = hitl_feedback.get("corrected_output", "")
            reason = hitl_feedback.get("reason", "")
        else:
            action = "approve"
            corrected = ""
            reason = ""

        if action == "reject":
            return {
                "briefing_draft_json": to_json(draft),
                "review_outcome": "rejected",
                "review_correction": reason,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"BriefingHitlNode: briefing rejected by reviewer. Reason: {reason}"],
            }
        elif action == "correct":
            return {
                "briefing_draft_json": to_json(draft),
                "review_outcome": "corrected",
                "review_correction": corrected,
                "status": AgentStatus.SUCCESS.value,
            }
        else:
            return {
                "briefing_draft_json": to_json(draft),
                "review_outcome": "approved",
                "review_correction": "",
                "status": AgentStatus.SUCCESS.value,
            }

    # ------------------------------------------------------------------
    # Summary helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_history(records: list[dict]) -> str:
        if not records:
            return "No historical access-request records found for the specified criteria."
        lines = []
        for r in records[:10]:
            lines.append(
                f"[{r.get('request_date', 'unknown date')}] {r.get('request_type', 'request')} "
                f"by {r.get('requester_role', 'unknown')} — outcome: {r.get('outcome', 'unknown')} "
                f"(source: {r.get('source_id', '')})"
            )
        if len(records) > 10:
            lines.append(f"... and {len(records) - 10} more records.")
        return "\n".join(lines)

    @staticmethod
    def _summarize_policies(refs: list[dict]) -> str:
        if not refs:
            return "No policy references found. Manual policy review recommended."
        lines = [f"- {r.get('title', '')} §{r.get('section', '')} [{r.get('source_id', '')}]" for r in refs]
        return "\n".join(lines)
