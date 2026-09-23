"""DomainWorkflowGraph — inner BaseGraph for EDU-C2-052 access-history pipeline."""

from __future__ import annotations

import json

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.briefing_hitl_node import BriefingHitlNode
from src.nodes.history_retrieval_node import HistoryRetrievalNode
from src.nodes.policy_reference_node import PolicyReferenceNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain graph for EDU-C2-052.

    Pipeline:
        START → history_retrieval → [ERROR → END]
              → policy_reference
              → briefing_hitl ──[interrupt if hitl_allowed=True]──→ human review
              → END

    All nodes use TrustLevel.ANONYMOUS (trust already verified by PreProcessNode).
    """

    @property
    def name(self) -> str:
        return "data_access_history_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def invoke(self, user_input, **kwargs):
        """Retain the wrapper payload for the initial-state extension hook."""
        self._pending_user_input = user_input
        return super().invoke(user_input, **kwargs)

    def _extra_initial_state(self) -> dict:
        """Restore outer pre-processing fields at the inner graph boundary."""
        raw = getattr(self, "_pending_user_input", "")
        if not isinstance(raw, str) or not raw.strip().startswith("{"):
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        allowed = {
            "request_criteria_json",
            "approved_source_ids_json",
            "validated_input",
            "stg_mock_mode",
        }
        return {key: value for key, value in parsed.items() if key in allowed}

    def register_nodes(self) -> None:
        self._nodes["history_retrieval"] = HistoryRetrievalNode()
        self._nodes["policy_reference"] = PolicyReferenceNode()
        # The node needs the runtime config to decide whether an interrupt() can
        # actually be resumed on this deployment (see BriefingHitlNode._hitl_resumable).
        self._nodes["briefing_hitl"] = BriefingHitlNode(config=self.config)

    def add_edges(self) -> None:
        self._sg.add_edge(START, "history_retrieval")
        self._sg.add_conditional_edges("history_retrieval", self.route)
        self._sg.add_edge("policy_reference", "briefing_hitl")
        self._sg.add_edge("briefing_hitl", END)

    def route(self, state: AgentState) -> str:
        if state.get("status") == AgentStatus.ERROR.value:
            return str(END)
        return "policy_reference"

    def get_output(self, state: AgentState) -> dict:
        return {
            "request_history_json": state.get("request_history_json", ""),
            "policy_references_json": state.get("policy_references_json", ""),
            "citations_json": state.get("citations_json", ""),
            "briefing_draft_json": state.get("briefing_draft_json", ""),
            "review_outcome": state.get("review_outcome", ""),
            "review_correction": state.get("review_correction", ""),
            "status": state.get("status"),
            "error_log": state.get("error_log", []),
            "node_history": state.get("node_history", []),
        }
