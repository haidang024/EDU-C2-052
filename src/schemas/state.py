"""State schema for EDU-C2-052 — Education Data Access Request History Agent."""

from __future__ import annotations

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# alternatives). LangGraph checkpoints use msgpack serialization, so only
# plain serializable fields are allowed. Do NOT add credentials or secrets.

import json
from typing import Any

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> str:
    """Serialize a value to a JSON string for safe state storage."""
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None, default: Any = None) -> Any:
    """Deserialize a JSON string from state, returning default on empty/error."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class State(AgentState):
    """Agent state for EDU-C2-052.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    Domain fields (all str, JSON-encoded where structured):
      request_criteria_json   — Producer: PreProcessNode
          Normalized access-request search criteria (JSON-encoded dict).
      approved_source_ids_json — Producer: PreProcessNode
          Operator-configured source allowlist (JSON-encoded list[str]).
      validated_input         — Producer: PreProcessNode
          Cleaned/validated string input forwarded to the inner workflow.
      request_history_json    — Producer: HistoryRetrievalNode
          Normalized historical access-request records with provenance
          (JSON-encoded list[dict]).
      policy_references_json  — Producer: PolicyReferenceNode
          Policy/regulation references mapped to the request criteria
          (JSON-encoded list[dict]).
      citations_json          — Producer: PolicyReferenceNode
          Provenance citation metadata for audit trail
          (JSON-encoded list[dict]).
      briefing_draft_json     — Producer: BriefingHitlNode
          Assembled human-review briefing draft (JSON-encoded dict).
      review_outcome          — Producer: BriefingHitlNode (on resume)
          "approved" | "corrected" | "rejected"
      review_correction       — Producer: BriefingHitlNode (on resume)
          Operator correction text when review_outcome == "corrected".
      formatted_output        — Producer: PostProcessNode
          Final structured briefing JSON string for the caller.
    """

    # Input processing
    request_criteria_json: str
    approved_source_ids_json: str
    validated_input: str
    stg_mock_mode: bool

    # Evidence
    request_history_json: str
    policy_references_json: str
    citations_json: str

    # Briefing and review
    briefing_draft_json: str
    review_outcome: str
    review_correction: str

    # Final output
    formatted_output: str
    input_error_message: str | None
    input_error_guidance: list[str]
    # Inner-workflow failure reason, carried as a domain field so the run keeps
    # a valid AgentStatus and still reaches post_process.
    workflow_error_message: str | None
    # Stable classification + the raw cause. Both MUST stay declared here:
    # LangGraph drops undeclared keys between nodes, which would lose the
    # failure before post_process can report it. `workflow_error_detail` is
    # log-facing only — it names node classes, so it is never rendered into
    # the caller-visible output.
    workflow_error_code: str | None
    workflow_error_detail: str | None
    generation_mode: str | None
    provider_error_message: str | None
