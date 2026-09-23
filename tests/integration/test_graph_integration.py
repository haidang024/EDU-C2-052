"""Integration tests for EDU-C2-052 — BL-17 end-to-end and BL-18 error propagation."""

from __future__ import annotations

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from langgraph.checkpoint.memory import MemorySaver
from src.graph.graph import Graph


def _valid_payload(**overrides) -> str:
    base = {
        "subject_type": "student",
        "subject_id": "STU-001",
        "date_from": "2025-01-01",
        "date_to": "2025-12-31",
        "approved_source_ids": ["source-a"],
        "request_type": "FERPA",
        "operator_id": "OP-001",
    }
    base.update(overrides)
    return json.dumps(base)


def _ctx(hitl_allowed: bool = False) -> InvocationContext:
    return InvocationContext(
        session_id="integration-test",
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        hitl_allowed=hitl_allowed,
    )


class TestIntegrationFullPipeline:
    """BL-17: full pipeline completes end-to-end without provider records."""

    def test_full_pipeline_no_records_completes(self):
        graph = Graph(config={"stg_mock_mode": True})
        graph.compile()
        result = graph.invoke(_valid_payload(), ctx=_ctx(hitl_allowed=False))
        assert result is not None
        formatted = result.get("output", "")
        assert formatted
        output = json.loads(formatted)
        assert output.get("agent") == "edu-c2-052"
        assert "briefing" in output.get("notice", "").lower() or "human review" in output.get("notice", "").lower()
        assert "review_disposition" in output

    def test_full_pipeline_output_includes_citations_key(self):
        graph = Graph(config={"stg_mock_mode": True})
        graph.compile()
        result = graph.invoke(_valid_payload(), ctx=_ctx(hitl_allowed=False))
        output = json.loads(result.get("output", "{}"))
        assert "citations" in output

    def test_hitl_approval_resume_completes(self):
        graph = Graph(
            config={
                "stg_mock_mode": True,
                "memory_enabled": True,
                "hitl": {"enabled": True},
            }
        )
        graph.compile(checkpointer=MemorySaver())

        first = graph.invoke(_valid_payload(), ctx=_ctx(hitl_allowed=True))
        assert first["status"] == AgentStatus.AWAITING_HUMAN.value

        resumed = graph.resume(first["thread_id"], {"action": "approve"})
        assert resumed["status"] == AgentStatus.SUCCESS.value
        assert resumed["output"]


class TestIntegrationInputGuidance:
    """BL-18: correctable input failures return actionable guidance."""

    def test_invalid_json_returns_guidance(self):
        graph = Graph(config={"stg_mock_mode": True})
        graph.compile()
        result = graph.invoke("not json", ctx=_ctx())
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert "valid json" in result.get("output", "").lower()

    def test_missing_fields_return_guidance(self):
        graph = Graph(config={"stg_mock_mode": True})
        graph.compile()
        result = graph.invoke(json.dumps({"subject_type": "student"}), ctx=_ctx())
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert "missing" in result.get("output", "").lower()
