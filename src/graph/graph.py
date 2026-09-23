"""Graph — outer AgentBaseGraph for EDU-C2-052."""

from __future__ import annotations

import re

import json
from typing import TYPE_CHECKING, Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import DomainWorkflowGraph


# Caller-facing text per stable workflow error code. Raw node error_log text
# names the failing node class, so it must never be rendered into the chat
# window; the code is what support maps back to the detail kept on
# `workflow_error_detail`.
_WORKFLOW_ERROR_REASONS: dict[str, str] = {
    "CONFIGURATION_INVALID": (
        "This agent is not yet connected to the records system it needs, so no briefing was produced."
    ),
    "REQUEST_INCOMPLETE": ("The request is missing details needed to build the briefing, so none was produced."),
    "REVIEW_REJECTED": "A reviewer declined the briefing, so it was not released.",
    "OUTPUT_BLOCKED": ("The draft briefing did not pass the required content checks, so it was not released."),
    "WORKFLOW_FAILED": "The records request briefing could not be completed.",
}

_DEFAULT_WORKFLOW_ERROR_CODE = "WORKFLOW_FAILED"


def _classify_workflow_error(raw: str) -> str:
    """Map raw inner-graph error text onto a stable, caller-safe code."""
    lowered = raw.lower()
    if any(marker in lowered for marker in ("required secret", "missingsecret", "<credential>")):
        return "CONFIGURATION_INVALID"
    if "rejected by reviewer" in lowered:
        return "REVIEW_REJECTED"
    if any(marker in lowered for marker in ("credential-like pattern", "prohibited")):
        return "OUTPUT_BLOCKED"
    if any(marker in lowered for marker in ("empty or missing", "exceeds limit", "is required")):
        return "REQUEST_INCOMPLETE"
    return _DEFAULT_WORKFLOW_ERROR_CODE


def _user_facing_reason(error_code: str) -> str:
    """Return text that is safe to show the caller for a workflow error code."""
    return _WORKFLOW_ERROR_REASONS.get(error_code, _WORKFLOW_ERROR_REASONS[_DEFAULT_WORKFLOW_ERROR_CODE])


class AccessHistoryGraphNode(GraphNode):
    """Wraps the inner DomainWorkflowGraph; assigned to the `main` slot.

    Propagates HITL interrupts to the outer caller (privacy-office operator
    review is a mandatory gate for this agent).
    """

    # "handle" (not "propagate"): a propagated SubgraphError aborts the run
    # before merge_output(), so post_process never executes and the Marketplace
    # runner returns a bare RuntimeError with no reason. on_subgraph_error()
    # converts the failure into a domain field instead.
    error_strategy: ClassVar[str] = "handle"
    propagate_hitl: ClassVar[bool] = True

    def __init__(self, config: dict[str, Any] | None = None, llm: Any | None = None) -> None:
        super().__init__()
        self._config = dict(config or {})
        self._llm = llm
        self._subgraph: DomainWorkflowGraph | None = None

    def get_subgraph(self) -> DomainWorkflowGraph:
        from langgraph.checkpoint.memory import MemorySaver
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        # Preserve one checkpointer-backed instance so an inner HITL interrupt
        # can resume against the checkpoint created by its initial invocation.
        if self._subgraph is None:
            self._subgraph = DomainWorkflowGraph(config=self._parent_config())
            self._subgraph.compile(checkpointer=MemorySaver())
        return self._subgraph

    def extract_input(self, state: AgentState) -> str:
        payload = {
            "request_criteria_json": state.get("request_criteria_json", ""),
            "approved_source_ids_json": state.get("approved_source_ids_json", ""),
            "validated_input": state.get("validated_input", state.get("user_input", "")),
            "stg_mock_mode": bool(self._config.get("stg_mock_mode", False)),
        }
        return json.dumps(payload)

    def execute(self, state: AgentState) -> dict[str, Any]:
        if state.get("input_error_message"):
            return {"status": AgentStatus.SUCCESS.value}
        return cast(dict[str, Any], super().execute(state))

    def on_subgraph_error(self, state: AgentState, error: Exception) -> dict[str, Any]:
        """Carry an inner failure as a domain field so the pipeline keeps running.

        Returning status=error here would route straight to finalize, skipping
        post_process; the Marketplace runner then drops `output` and the caller sees
        only "invocation did not succeed".
        """
        error_log = getattr(error, "error_log", None) or []
        # BaseNode.__call__() appends "[Node] <message>\n<full traceback>" to
        # error_log. Forwarding that verbatim leaks internal file paths and source
        # lines to the caller, and the surviving source line
        # `api_key = ctx.secrets.require(...)` trips the S-3 output scan, which
        # rejects the whole briefing and turns a graceful degradation into a hard
        # invocation failure. Keep only the first line — the human-readable reason —
        # and drop the traceback.
        #
        # A reason may still name a missing secret (e.g. FOO_API_KEY). That is a key
        # *name*, not key material, but the S-3 output scan matches the literal
        # "api_key" substring and would reject the whole message. Mask such
        # identifiers so the diagnostic still reaches the caller.
        reasons = [
            re.sub(
                r"\b[A-Z0-9]+(?:_[A-Z0-9]+)*_(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD)\b",
                "<credential>",
                str(e).strip().splitlines()[0],
            )
            for e in error_log
            if str(e).strip()
        ]
        raw = reasons[-1] if reasons else ""
        # Masking the secret name is not enough: the first line still carries the
        # failing node class ("[HistoryRetrievalNode] ..."), which is internal
        # structure. Render only a pre-authored description keyed on a stable
        # code; the (already traceback-stripped) reason stays on
        # workflow_error_detail for the logs.
        code = _classify_workflow_error(raw)
        return {
            "status": AgentStatus.SUCCESS.value,
            "workflow_error_code": code,
            "workflow_error_detail": raw,
            "workflow_error_message": _user_facing_reason(code),
        }

    def merge_output(self, state: AgentState, sub_result: dict) -> dict:
        return {
            "request_history_json": sub_result.get("request_history_json", ""),
            "policy_references_json": sub_result.get("policy_references_json", ""),
            "citations_json": sub_result.get("citations_json", ""),
            "briefing_draft_json": sub_result.get("briefing_draft_json", ""),
            "review_outcome": sub_result.get("review_outcome", ""),
            "review_correction": sub_result.get("review_correction", ""),
            "status": sub_result.get("status"),
            "error_log": sub_result.get("error_log", []),
        }

    def _parent_config(self) -> dict:
        return {**self._config, "llm": self._llm}


class Graph(AgentBaseGraph):
    """EDU-C2-052 — Education Data Access Request History Agent.

    Cat 2 outer graph. Domain logic is encapsulated in the inner
    DomainWorkflowGraph via AccessHistoryGraphNode in the `main` slot.

    Backbone: initialize → pre_process → main → post_process → finalize (fixed).
    """

    @property
    def name(self) -> str:
        return "edu-c2-052"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = AccessHistoryGraphNode(
            config=self.config,
            llm=self.config.get("llm"),
        )
        self._nodes["post_process"] = PostProcessNode(
            llm=self.config.get("llm"),
            config=self.config,
        )

    def get_output(self, state: AgentState) -> dict[str, Any]:
        output = cast(dict[str, Any], super().get_output(state))
        output["generation_mode"] = state.get("generation_mode")
        output["provider_error_message"] = state.get("provider_error_message")
        # Classification + raw cause for operators. `workflow_error_detail` is
        # deliberately absent from the rendered `output` string — it names node
        # classes — but it must reach the audit log.
        output["workflow_error_code"] = state.get("workflow_error_code")
        output["workflow_error_detail"] = state.get("workflow_error_detail")
        context = state.get("input_context")
        is_marketplace = isinstance(context, dict) and "conversation_history" in context
        if not is_marketplace:
            return output

        if _set_marketplace_guidance(output, state, "Data access history request"):
            return output

        payload = self._parse_history_briefing(output.get("output", output.get("formatted_output")))
        if payload is not None:
            output["output"] = self._render_marketplace_briefing(payload)
        return output

    @staticmethod
    def _parse_history_briefing(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _render_marketplace_briefing(payload: dict[str, Any]) -> str:
        lines = [
            "# Data Access Request History Briefing",
            "",
            f"**Review disposition:** {payload.get('review_disposition', 'unknown')}",
            "",
            str(payload.get("executive_summary", "No history summary is available.")),
        ]
        if payload.get("history_record_count") is not None:
            lines.extend(
                [
                    "",
                    f"History records: {payload.get('history_record_count', 0)}",
                    f"Policy references: {payload.get('policy_reference_count', 0)}",
                ]
            )
        citations = payload.get("citations")
        if isinstance(citations, list) and citations:
            lines.extend(["", "Sources:"])
            for citation in citations[:20]:
                if isinstance(citation, dict):
                    lines.append(f"- {citation.get('citation') or citation.get('source') or 'Source'}")
                else:
                    lines.append(f"- {citation}")
        limitations = payload.get("limitations")
        if isinstance(limitations, list) and limitations:
            lines.extend(["", "Limitations:"])
            lines.extend(f"- {item}" for item in limitations)
        if payload.get("operator_correction"):
            lines.extend(["", "Operator correction:", str(payload["operator_correction"])])
        if payload.get("notice"):
            lines.extend(["", f"> {payload['notice']}"])
        return "\n".join(lines)


def _set_marketplace_guidance(output: dict[str, Any], state: AgentState, subject: str) -> bool:
    context = state.get("input_context")
    message = state.get("input_error_message")
    if not (isinstance(context, dict) and "conversation_history" in context and message):
        return False
    lines = [f"{subject} could not be processed.", "", f"Reason: {message}"]
    guidance = state.get("input_error_guidance")
    if isinstance(guidance, list) and guidance:
        lines.extend(["", "How to continue:"])
        lines.extend(f"- {item}" for item in guidance)
    output["output"] = "\n".join(lines)
    return True
