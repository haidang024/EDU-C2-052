"""PostProcessNode — formats the final access-history briefing with traceability."""

from __future__ import annotations

import re
from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.llm_runtime import provider_metadata, request_advisory
from src.schemas.state import from_json, to_json

# S-3 credential-pattern scan — detect accidental leakage in formatted output
_CREDENTIAL_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9\-_]{16,}|api[_\-]?key\s*[:=]\s*\S{8,}|" r"password\s*[:=]\s*\S{8,})",
)


class PostProcessNode(FunctionNode):
    """Format the final access-history briefing for the caller.

    Assembles the structured output from state fields produced by the inner
    DomainWorkflowGraph. Applies the S-3 credential scan to detect any
    unintended leakage in the formatted output before it reaches the caller.

    The output is explicitly labeled as a briefing for human review and is
    not an automated data-access decision or disclosure.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict) -> dict:
        """S-3: scan formatted_output for credential-like strings."""
        output_str = result.get("formatted_output", "") or ""
        if _CREDENTIAL_RE.search(output_str):
            raise SecurityViolationError("PostProcessNode: formatted_output contains credential-like pattern")
        return result

    def __init__(self, llm: object | None = None, config: dict | None = None) -> None:
        super().__init__()
        self._llm = llm
        self._config = config or {}

    def execute(self, state: dict) -> dict:
        if state.get("input_error_message"):
            message = str(state["input_error_message"])
            return {"status": AgentStatus.SUCCESS.value, "result": message, "formatted_output": message}

        # Inner workflow failed (e.g. an unavailable credential or connector).
        # Report it on a SUCCESS envelope: the Marketplace runner only forwards
        # `output` when status == "success", so status=error would leave the
        # caller with no reason at all.
        if state.get("workflow_error_message"):
            reason = str(state["workflow_error_message"])
            # `workflow_error_message` is already caller-safe text chosen from
            # a fixed table in graph.py. The raw cause lives on
            # workflow_error_detail and must not be rendered here.
            reference = str(state.get("workflow_error_code") or "WORKFLOW_FAILED")
            if reference == "REQUEST_INCOMPLETE":
                next_step = "- Add the missing details to your request and try again."
            elif reference == "REVIEW_REJECTED":
                next_step = "- Ask the reviewer what they need changed, then resubmit."
            else:
                next_step = "- Ask an administrator to finish this agent's setup, then retry."
            message = (
                "The records request briefing could not be completed.\n\n"
                f"Reason: {reason}\n\n"
                f"How to continue:\n{next_step}\n\n"
                f"Reference: {reference}"
            )
            return {
                "status": AgentStatus.SUCCESS.value,
                "result": message,
                "formatted_output": message,
            }

        request_advisory(
            state,
            "Review the EDU-C2-052 result for clarity, grounding, and safe human review.",
            self._llm,
            timeout_s=float(self._config.get("timeout_s", 30.0)),
            max_retry=int(self._config.get("max_retry", 3)),
        )
        metadata = provider_metadata(state)
        status = state.get("status", AgentStatus.ERROR.value)
        review_outcome = state.get("review_outcome", "")
        error_log = state.get("error_log", [])

        if review_outcome == "rejected":
            output = {
                "agent": "edu-c2-052",
                "notice": (
                    "This briefing is a factual summary for authorized human review only. "
                    "It does not constitute an automated data-access decision or disclosure."
                ),
                "review_disposition": "rejected",
                "executive_summary": "[REJECTED BY REVIEWER]",
                "limitations": error_log,
            }

            emit_trace_event("PostProcessNode_output_rejected", {"review_outcome": "rejected"}, state)
            return {
                "formatted_output": to_json(output),
                "status": AgentStatus.ERROR.value,
                **metadata,
            }

        if status == AgentStatus.ERROR.value and not review_outcome:
            output = {
                "agent": "edu-c2-052",
                "notice": (
                    "This briefing is a factual summary for authorized human review only. "
                    "It does not constitute an automated data-access decision or disclosure."
                ),
                "review_disposition": "error",
                "executive_summary": "Pipeline error — see limitations for details.",
                "limitations": error_log,
            }
            emit_trace_event("PostProcessNode_output_error", {"errors": len(error_log)}, state)
            return {
                "formatted_output": to_json(output),
                "status": AgentStatus.ERROR.value,
                **metadata,
            }

        draft = from_json(state.get("briefing_draft_json"), default={})
        criteria = from_json(state.get("request_criteria_json"), default={})
        citations = from_json(state.get("citations_json"), default=[])
        history = from_json(state.get("request_history_json"), default=[])
        policy_refs = from_json(state.get("policy_references_json"), default=[])
        review_correction = state.get("review_correction", "")

        # Build executive summary
        history_summary = draft.get("history_summary", "No history summary available.")
        policy_summary = draft.get("policy_summary", "No policy summary available.")
        has_gaps = draft.get("has_gaps", len(policy_refs) == 0)

        executive_summary = (
            f"Access Request History Summary for {criteria.get('subject_type', 'unknown')} "
            f"({criteria.get('date_from', '')} to {criteria.get('date_to', '')}):\n\n"
            f"{history_summary}\n\nPolicy References:\n{policy_summary}"
        )

        limitations = []
        if has_gaps:
            limitations.append("No policy references found; manual policy review recommended.")
        if not history:
            limitations.append("No historical access-request records found for the specified criteria.")

        output = {
            "agent": "edu-c2-052",
            "notice": (
                "This briefing is a factual summary for authorized human review only. "
                "It does not constitute an automated data-access decision or disclosure."
            ),
            "review_disposition": review_outcome or "approved",
            "executive_summary": executive_summary,
            "history_record_count": len(history),
            "policy_reference_count": len(policy_refs),
            "citations": citations,
            "limitations": limitations,
        }

        if review_outcome == "corrected" and review_correction:
            output["operator_correction"] = review_correction

        emit_trace_event(
            "PostProcessNode_output_assembled",
            {
                "review_outcome": review_outcome,
                "history_count": len(history),
                "citation_count": len(citations),
                "has_gaps": has_gaps,
            },
            state,
        )

        return {
            "formatted_output": to_json(output),
            "status": AgentStatus.SUCCESS.value,
            **metadata,
        }
