"""PolicyReferenceNode — retrieves and maps policy references to request criteria."""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.service import AccessHistoryService


class PolicyReferenceNode(FunctionNode):
    """Retrieve policy and regulation references relevant to the request criteria.

    Queries only the approved source IDs. Maps each reference to traceable
    citation metadata for the audit trail. Returns a safe partial result
    (empty lists) when references are unavailable, without failing the pipeline.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        criteria = from_json(state.get("request_criteria_json"), default={})
        approved_source_ids = from_json(state.get("approved_source_ids_json"), default=[])

        # The connector is stubbed regardless, so an unprovisioned credential
        # must not abort the run: it only means the fixture records answer
        # instead of a live source. The service treats an empty key as "no live
        # connector" and returns its bundled records.
        api_key = ""
        if not state.get("stg_mock_mode", False):
            ctx = InvocationContext.from_state(state)
            try:
                api_key = ctx.secrets.require("ACCESS_HISTORY_API_KEY")
            except Exception:  # noqa: BLE001
                api_key = ""

        svc = AccessHistoryService(api_key=api_key)

        policy_refs: list[dict] = []
        citations: list[dict] = []

        if criteria and approved_source_ids:
            policy_refs = svc.fetch_policy_references(criteria, approved_source_ids)
            citations = [
                {
                    "policy_id": ref["policy_id"],
                    "title": ref["title"],
                    "section": ref.get("section", ""),
                    "url": ref.get("citation_url", ""),
                    "source_id": ref.get("source_id", ""),
                }
                for ref in policy_refs
                if ref.get("citation_url")
            ]

        has_gaps = len(policy_refs) == 0

        emit_trace_event(
            "PolicyReferenceNode_references_mapped",
            {"policy_count": len(policy_refs), "citation_count": len(citations), "has_gaps": has_gaps},
            state,
        )

        return {
            "policy_references_json": to_json(policy_refs),
            "citations_json": to_json(citations),
            "status": AgentStatus.SUCCESS.value,
        }
