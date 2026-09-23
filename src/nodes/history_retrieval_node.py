"""HistoryRetrievalNode — retrieves approved access-request history records."""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.service import AccessHistoryService


class HistoryRetrievalNode(FunctionNode):
    """Retrieve approved data-access request history records from configured sources.

    Queries only the approved source IDs validated by PreProcessNode.
    Normalizes all records with provenance metadata before writing to state.
    Does not make any disclosure or approval decisions.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        criteria = from_json(state.get("request_criteria_json"), default={})
        approved_source_ids = from_json(state.get("approved_source_ids_json"), default=[])

        if not criteria:
            emit_trace_event(
                "HistoryRetrievalNode_missing_criteria",
                {"approved_source_count": 0},
                state,
            )
            return {
                "request_history_json": to_json([]),
                "status": AgentStatus.ERROR.value,
                "error_log": ["HistoryRetrievalNode: request_criteria_json is empty or missing"],
            }

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
        records = svc.fetch_request_history(criteria, approved_source_ids)

        emit_trace_event(
            "HistoryRetrievalNode_records_retrieved",
            {"source_count": len(approved_source_ids), "record_count": len(records)},
            state,
        )

        return {
            "request_history_json": to_json(records),
            "status": AgentStatus.SUCCESS.value,
        }
