"""Unit tests for EDU-C2-052 nodes — TC-01, TC-08–TC-11, BL-01–BL-18."""

from __future__ import annotations

import json

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.schemas.state import State, from_json, to_json


# ---------------------------------------------------------------------------
# TC-01: State contract
# ---------------------------------------------------------------------------

class TestTC01StateContract:
    """TC-01: flat TypedDict state contract."""

    def test_state_extends_agent_state(self):
        from typing import is_typeddict

        assert is_typeddict(State)
        assert "session_id" in State.__annotations__

    def test_to_json_from_json_roundtrip(self):
        data = {"key": "value", "list": [1, 2, 3]}
        assert from_json(to_json(data)) == data

    def test_from_json_empty_returns_default(self):
        assert from_json("", default=[]) == []
        assert from_json(None, default={}) == {}

    def test_from_json_invalid_returns_default(self):
        assert from_json("not-json", default=[]) == []

    def test_state_has_required_fields(self):
        annotations = State.__annotations__
        required = {
            "request_criteria_json", "approved_source_ids_json", "validated_input",
            "request_history_json", "policy_references_json", "citations_json",
            "briefing_draft_json", "review_outcome", "review_correction", "formatted_output",
        }
        for field in required:
            assert field in annotations, f"State missing field: {field}"


# ---------------------------------------------------------------------------
# TC-08: required_trust_level enforced
# ---------------------------------------------------------------------------

class TestTC08TrustLevel:
    """TC-08: ANONYMOUS caller rejected for VERIFIED_EXTERNAL nodes."""

    def test_pre_process_rejects_anonymous(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        assert node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
        state = {"caller_trust_level": TrustLevel.ANONYMOUS.value}
        result = node(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert "S-1 trust gate denied" in result["error_log"][0]

    def test_post_process_rejects_anonymous(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        assert node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL
        state = {"caller_trust_level": TrustLevel.ANONYMOUS.value}
        result = node(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert "S-1 trust gate denied" in result["error_log"][0]

    def test_history_retrieval_allows_anonymous(self):
        from src.nodes.history_retrieval_node import HistoryRetrievalNode
        node = HistoryRetrievalNode()
        assert node.required_trust_level == TrustLevel.ANONYMOUS

    def test_policy_reference_allows_anonymous(self):
        from src.nodes.policy_reference_node import PolicyReferenceNode
        node = PolicyReferenceNode()
        assert node.required_trust_level == TrustLevel.ANONYMOUS

    def test_briefing_hitl_allows_anonymous(self):
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        assert node.required_trust_level == TrustLevel.ANONYMOUS


# ---------------------------------------------------------------------------
# TC-09: S-2 extra gate non-trivial
# ---------------------------------------------------------------------------

class TestTC09S2Gate:
    """TC-09: PreProcessNode._extra_security_gate_input() guards length + prohibited framing."""

    def _state(self, user_input: str) -> dict:
        return {"user_input": user_input, "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value}

    def test_long_input_raises_security_violation(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        long_input = "x" * 4097
        with pytest.raises(SecurityViolationError, match="exceeds limit"):
            node._extra_security_gate_input(self._state(long_input))

    def test_prohibited_framing_auto_approve_raises(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        with pytest.raises(SecurityViolationError, match="prohibited"):
            node._extra_security_gate_input(self._state('{"auto_approve": true}'))

    def test_prohibited_framing_auto_disclose_raises(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        with pytest.raises(SecurityViolationError, match="prohibited"):
            node._extra_security_gate_input(self._state('{"action": "auto_disclose"}'))

    def test_prohibited_framing_grant_access_raises(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        with pytest.raises(SecurityViolationError, match="prohibited"):
            node._extra_security_gate_input(self._state("grant_access to subject"))

    def test_valid_input_returns_state(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        state = self._state('{"subject_type": "student"}')
        result = node._extra_security_gate_input(state)
        assert result is state


# ---------------------------------------------------------------------------
# TC-10: S-3 extra gate non-trivial
# ---------------------------------------------------------------------------

class TestTC10S3Gate:
    """TC-10: PostProcessNode._extra_security_gate_output() scans for credentials."""

    def test_bearer_token_raises_security_violation(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        result = {"formatted_output": '{"data": "Bearer eyJhbGciOiJSUzI1NiJ9.abc123def456"}'}
        with pytest.raises(SecurityViolationError, match="credential-like"):
            node._extra_security_gate_output(result)

    def test_api_key_raises_security_violation(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        result = {"formatted_output": '{"data": "api_key: sk-abc12345678901234"}'}
        with pytest.raises(SecurityViolationError, match="credential-like"):
            node._extra_security_gate_output(result)

    def test_clean_output_passes(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        result = {"formatted_output": '{"summary": "No records found."}'}
        returned = node._extra_security_gate_output(result)
        assert returned == result


# ---------------------------------------------------------------------------
# TC-11: emit_trace_event in every execute()
# ---------------------------------------------------------------------------

class TestTC11AuditEvents:
    """TC-11: every domain node execute() emits at least one domain trace event."""

    def _make_state(self, trust_level=TrustLevel.ANONYMOUS.value) -> dict:
        return {
            "caller_trust_level": trust_level,
            "correlation_id": "tc11-test",
            "node_history": [],
            "error_log": [],
            "status": AgentStatus.SUCCESS.value,
            "hitl_allowed": False,
            "stg_mock_mode": True,
        }

    def test_pre_process_emits_event(self, monkeypatch):
        import src.nodes.pre_process_node as pn
        events = []
        monkeypatch.setattr(pn, "emit_trace_event", lambda n, p, s: events.append(n))
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        state = self._make_state(TrustLevel.VERIFIED_EXTERNAL.value)
        state["user_input"] = ""
        node(state)
        assert any(e.startswith("PreProcessNode_") for e in events)

    def test_history_retrieval_emits_event(self, monkeypatch):
        import src.nodes.history_retrieval_node as hrn
        events = []
        monkeypatch.setattr(hrn, "emit_trace_event", lambda n, p, s: events.append(n))
        from src.nodes.history_retrieval_node import HistoryRetrievalNode
        node = HistoryRetrievalNode()
        state = self._make_state()
        state["request_criteria_json"] = ""
        node(state)
        assert any(e.startswith("HistoryRetrievalNode_") for e in events)

    def test_policy_reference_emits_event(self, monkeypatch):
        import src.nodes.policy_reference_node as prn
        events = []
        monkeypatch.setattr(prn, "emit_trace_event", lambda n, p, s: events.append(n))
        from src.nodes.policy_reference_node import PolicyReferenceNode
        node = PolicyReferenceNode()
        state = self._make_state()
        state["request_criteria_json"] = to_json({"subject_type": "student"})
        state["approved_source_ids_json"] = to_json([])
        node(state)
        assert any(e.startswith("PolicyReferenceNode_") for e in events)

    def test_briefing_hitl_emits_event(self, monkeypatch):
        import src.nodes.briefing_hitl_node as bhn
        events = []
        monkeypatch.setattr(bhn, "emit_trace_event", lambda n, p, s: events.append(n))
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        state = self._make_state()
        state["request_criteria_json"] = to_json({"subject_type": "student"})
        state["request_history_json"] = to_json([])
        state["policy_references_json"] = to_json([])
        state["citations_json"] = to_json([])
        node(state)
        assert any(e.startswith("BriefingHitlNode_") for e in events)

    def test_post_process_emits_event(self, monkeypatch):
        import src.nodes.post_process_node as ppn
        events = []
        monkeypatch.setattr(ppn, "emit_trace_event", lambda n, p, s: events.append(n))
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        state = self._make_state(TrustLevel.VERIFIED_EXTERNAL.value)
        state["review_outcome"] = "approved"
        state["request_criteria_json"] = to_json({"subject_type": "student", "date_from": "2025-01-01", "date_to": "2025-12-31"})
        state["request_history_json"] = to_json([])
        state["policy_references_json"] = to_json([])
        state["citations_json"] = to_json([])
        state["briefing_draft_json"] = to_json({"history_summary": "none", "policy_summary": "none", "has_gaps": True})
        node(state)
        assert any(e.startswith("PostProcessNode_") for e in events)


# ---------------------------------------------------------------------------
# BL-01 – BL-18: Business logic tests
# ---------------------------------------------------------------------------

def _valid_scope(**overrides) -> str:
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


def _pre_state(user_input: str) -> dict:
    return {
        "user_input": user_input,
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "node_history": [],
        "error_log": [],
        "status": AgentStatus.SUCCESS.value,
    }


class TestBL01ValidScopeAccepted:
    """BL-01: valid scope accepted and normalized."""

    def test_valid_scope_populates_fields(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        state = _pre_state(_valid_scope())
        result = node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["request_criteria_json"]
        criteria = from_json(result["request_criteria_json"])
        assert criteria["subject_type"] == "student"
        source_ids = from_json(result["approved_source_ids_json"])
        assert "source-a" in source_ids


class TestBL02EmptyInputGuidance:
    """BL-02: empty user_input returns guidance."""

    def test_empty_input(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        state = _pre_state("")
        result = node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["input_error_message"]


class TestBL03NonJsonGuidance:
    """BL-03: non-JSON input returns guidance."""

    def test_non_json(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        state = _pre_state("not json")
        result = node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "json" in result["input_error_message"].lower()


class TestBL04MissingFieldRejected:
    """BL-04: missing required field rejected."""

    def test_missing_subject_type(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        scope = json.dumps({"subject_id": "STU-001", "date_from": "2025-01-01", "date_to": "2025-12-31", "approved_source_ids": ["src-a"]})
        result = node(_pre_state(scope))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "subject_type" in result["input_error_message"]

    def test_missing_approved_source_ids(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        scope = json.dumps({"subject_type": "student", "subject_id": "STU-001", "date_from": "2025-01-01", "date_to": "2025-12-31"})
        result = node(_pre_state(scope))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "approved_source_ids" in result["input_error_message"]


class TestBL05InvalidValues:
    """BL-05: invalid field values rejected."""

    def test_invalid_subject_type(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        result = node(_pre_state(_valid_scope(subject_type="robot")))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "subject type" in result["input_error_message"].lower()

    def test_invalid_date_format(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        result = node(_pre_state(_valid_scope(date_from="01-01-2025")))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "date" in result["input_error_message"].lower()

    def test_empty_approved_source_ids(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        result = node(_pre_state(_valid_scope(approved_source_ids=[])))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "approved_source_ids" in result["input_error_message"]


class TestBL06HistoryRetrievalEmpty:
    """BL-06: empty approved sources → empty records."""

    def test_empty_sources_returns_empty(self):
        from src.nodes.history_retrieval_node import HistoryRetrievalNode
        node = HistoryRetrievalNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "stg_mock_mode": True,
            "request_criteria_json": to_json({"subject_type": "student"}),
            "approved_source_ids_json": to_json([]),
            "node_history": [],
            "error_log": [],
            "status": AgentStatus.SUCCESS.value,
        }
        result = node(state)
        # With no approved sources the service returns [] but criteria present
        # empty approved_source_ids → empty result
        records = from_json(result.get("request_history_json", "[]"), default=[])
        assert records == []


class TestBL07MissingCriteriaRetrievalError:
    """BL-07: missing criteria → retrieval error."""

    def test_missing_criteria(self):
        from src.nodes.history_retrieval_node import HistoryRetrievalNode
        node = HistoryRetrievalNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "stg_mock_mode": True,
            "request_criteria_json": "",
            "approved_source_ids_json": to_json(["source-a"]),
            "node_history": [],
            "error_log": [],
            "status": AgentStatus.SUCCESS.value,
        }
        result = node(state)
        assert result["status"] == AgentStatus.ERROR.value


class TestBL08ProviderRecordsMappedWithProvenance:
    """BL-08: provider records mapped with provenance."""

    def test_records_include_provenance(self, monkeypatch):
        import src.nodes.history_retrieval_node as hrn

        class _FakeAdapter:
            def fetch_history(self, criteria, source_ids):
                return [{
                    "id": "req-001",
                    "subject_type": "student",
                    "subject_id": "STU-001",
                    "request_date": "2025-03-15",
                    "request_type": "FERPA",
                    "outcome": "approved",
                    "requester_role": "registrar",
                    "source_id": "source-a",
                    "provenance_url": "https://example.edu/records/req-001",
                }]

        from src.services.service import AccessHistoryService
        monkeypatch.setattr(
            hrn,
            "AccessHistoryService",
            lambda api_key: AccessHistoryService(api_key=api_key, fake_adapter=_FakeAdapter()),
        )

        from src.nodes.history_retrieval_node import HistoryRetrievalNode
        node = HistoryRetrievalNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "stg_mock_mode": True,
            "request_criteria_json": to_json({"subject_type": "student", "subject_id": "STU-001"}),
            "approved_source_ids_json": to_json(["source-a"]),
            "node_history": [],
            "error_log": [],
            "status": AgentStatus.SUCCESS.value,
        }
        result = node(state)
        records = from_json(result["request_history_json"])
        assert len(records) == 1
        assert records[0]["provenance_url"] == "https://example.edu/records/req-001"
        assert records[0]["source_id"] == "source-a"


class TestBL09PolicyRefsWithCitations:
    """BL-09: policy references produce citations."""

    def test_policy_refs_with_citation_url(self, monkeypatch):
        import src.nodes.policy_reference_node as prn

        class _FakeAdapter:
            def fetch_policies(self, criteria, source_ids):
                return [{
                    "policy_id": "FERPA-34CFR99",
                    "title": "FERPA 34 CFR Part 99",
                    "section": "99.3",
                    "relevance_note": "Defines education records",
                    "source_id": "source-a",
                    "citation_url": "https://law.cornell.edu/cfr/text/34/part-99",
                }]

        from src.services.service import AccessHistoryService
        monkeypatch.setattr(
            prn,
            "AccessHistoryService",
            lambda api_key: AccessHistoryService(api_key=api_key, fake_adapter=_FakeAdapter()),
        )

        from src.nodes.policy_reference_node import PolicyReferenceNode
        node = PolicyReferenceNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "stg_mock_mode": True,
            "request_criteria_json": to_json({"subject_type": "student"}),
            "approved_source_ids_json": to_json(["source-a"]),
            "node_history": [],
            "error_log": [],
        }
        result = node(state)
        refs = from_json(result["policy_references_json"])
        citations = from_json(result["citations_json"])
        assert len(refs) == 1
        assert len(citations) == 1
        assert citations[0]["url"] == "https://law.cornell.edu/cfr/text/34/part-99"


class TestBL10NoRecordsUncertainty:
    """BL-10: no records → uncertainty marker in briefing."""

    def test_empty_history_produces_uncertainty(self):
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "hitl_allowed": False,
            "request_criteria_json": to_json({"subject_type": "student"}),
            "request_history_json": to_json([]),
            "policy_references_json": to_json([]),
            "citations_json": to_json([]),
            "node_history": [],
            "error_log": [],
        }
        result = node(state)
        draft = from_json(result["briefing_draft_json"])
        assert "No historical" in draft["history_summary"] or draft["has_gaps"] is True


class TestBL11CitationsFromRecords:
    """BL-11: citations collected from policy references."""

    def test_citations_populated(self, monkeypatch):
        import src.nodes.policy_reference_node as prn

        class _FakeAdapter:
            def fetch_policies(self, criteria, source_ids):
                return [{"policy_id": "P1", "title": "Policy 1", "section": "1", "relevance_note": "r",
                         "source_id": "s1", "citation_url": "https://example.edu/p1"}]

        from src.services.service import AccessHistoryService
        monkeypatch.setattr(
            prn,
            "AccessHistoryService",
            lambda api_key: AccessHistoryService(api_key=api_key, fake_adapter=_FakeAdapter()),
        )

        from src.nodes.policy_reference_node import PolicyReferenceNode
        node = PolicyReferenceNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "stg_mock_mode": True,
            "request_criteria_json": to_json({"subject_type": "student"}),
            "approved_source_ids_json": to_json(["s1"]),
            "node_history": [],
            "error_log": [],
        }
        result = node(state)
        citations = from_json(result["citations_json"])
        assert any(c.get("url") for c in citations)


class TestBL12GapFlaggedNoPolicyRefs:
    """BL-12: gap flagged when no policy references available."""

    def test_has_gaps_true_when_no_policies(self):
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "hitl_allowed": False,
            "request_criteria_json": to_json({"subject_type": "student"}),
            "request_history_json": to_json([]),
            "policy_references_json": to_json([]),
            "citations_json": to_json([]),
            "node_history": [],
            "error_log": [],
        }
        result = node(state)
        draft = from_json(result["briefing_draft_json"])
        assert draft["has_gaps"] is True


class TestBL13OutputAdvisoryNotDecision:
    """BL-13: output does not indicate automated decision or disclosure."""

    def test_no_automated_decision_language(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        state = {
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "review_outcome": "approved",
            "request_criteria_json": to_json({"subject_type": "student", "date_from": "2025-01-01", "date_to": "2025-12-31"}),
            "request_history_json": to_json([]),
            "policy_references_json": to_json([]),
            "citations_json": to_json([]),
            "briefing_draft_json": to_json({"history_summary": "none", "policy_summary": "none", "has_gaps": True}),
            "review_correction": "",
            "status": AgentStatus.SUCCESS.value,
            "node_history": [],
            "error_log": [],
        }
        result = node(state)
        output = json.loads(result["formatted_output"])
        summary_lower = output.get("executive_summary", "").lower()
        notice = output.get("notice", "").lower()
        assert "access granted" not in summary_lower
        assert "disclosure approved" not in summary_lower
        assert "briefing" in notice or "human review" in notice


class TestBL14BriefingHITLDisabled:
    """BL-14: briefing assembled with HITL disabled (batch mode)."""

    def test_hitl_false_auto_approves(self):
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        state = {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "hitl_allowed": False,
            "request_criteria_json": to_json({"subject_type": "student"}),
            "request_history_json": to_json([]),
            "policy_references_json": to_json([]),
            "citations_json": to_json([]),
            "node_history": [],
            "error_log": [],
        }
        result = node(state)
        assert result["briefing_draft_json"]
        assert result["review_outcome"] == "approved"


class TestBL15ReviewOutcomes:
    """BL-15: resume with approve/correct/reject feedback."""

    def _draft_state(self) -> dict:
        draft = {
            "notice": "test",
            "request_criteria": {},
            "history_record_count": 0,
            "history_summary": "none",
            "policy_reference_count": 0,
            "policy_summary": "none",
            "citations": [],
            "has_gaps": False,
        }
        return {
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "hitl_allowed": True,
            "briefing_draft_json": to_json(draft),
            "node_history": [],
            "error_log": [],
            "status": AgentStatus.SUCCESS.value,
        }

    def test_approve_sets_approved(self):
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        state = self._draft_state()
        state["hitl_feedback"] = "approve"
        result = node._process_review(state, "approve")
        assert result["review_outcome"] == "approved"

    def test_correct_sets_corrected(self):
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        state = self._draft_state()
        feedback = {"action": "correct", "corrected_output": "Corrected text", "reason": "minor edit"}
        result = node._process_review(state, feedback)
        assert result["review_outcome"] == "corrected"
        assert result["review_correction"] == "Corrected text"

    def test_reject_sets_rejected(self):
        from src.nodes.briefing_hitl_node import BriefingHitlNode
        node = BriefingHitlNode()
        state = self._draft_state()
        feedback = {"action": "reject", "reason": "incomplete"}
        result = node._process_review(state, feedback)
        assert result["review_outcome"] == "rejected"
        assert result["status"] == AgentStatus.ERROR.value


class TestBL16OutputSections:
    """BL-16: approved output includes all required sections."""

    def _post_state(self, review_outcome="approved") -> dict:
        return {
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "review_outcome": review_outcome,
            "request_criteria_json": to_json({"subject_type": "student", "date_from": "2025-01-01", "date_to": "2025-12-31"}),
            "request_history_json": to_json([]),
            "policy_references_json": to_json([]),
            "citations_json": to_json([]),
            "briefing_draft_json": to_json({"history_summary": "none", "policy_summary": "none", "has_gaps": True}),
            "review_correction": "",
            "status": AgentStatus.SUCCESS.value,
            "node_history": [],
            "error_log": [],
        }

    def test_approved_output_has_all_sections(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        result = node(self._post_state("approved"))
        output = json.loads(result["formatted_output"])
        for key in ("agent", "notice", "review_disposition", "executive_summary", "citations"):
            assert key in output, f"Missing key: {key}"

    def test_rejected_output_marked(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        state = self._post_state("rejected")
        state["status"] = AgentStatus.ERROR.value
        state["error_log"] = ["rejected"]
        # Isolate the error-rendering business branch; BaseNode correctly
        # short-circuits states that already carry status=error.
        result = node.execute(state)
        output = json.loads(result["formatted_output"])
        assert "[REJECTED BY REVIEWER]" in output["executive_summary"]

    def test_corrected_output_includes_correction(self):
        from src.nodes.post_process_node import PostProcessNode
        node = PostProcessNode()
        state = self._post_state("corrected")
        state["review_correction"] = "Operator correction applied"
        result = node(state)
        output = json.loads(result["formatted_output"])
        assert "operator_correction" in output
        assert output["operator_correction"] == "Operator correction applied"


class TestBL17PipelineStopsWithGuidance:
    """BL-17: pre-process failure returns guidance before domain work."""

    def test_invalid_input_propagates_error(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        state = _pre_state("not json at all")
        result = node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["input_error_message"]

    def test_missing_field_propagates_error(self):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        result = node(_pre_state('{"subject_type": "student"}'))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["input_error_message"]


class TestBL18AllSubjectTypes:
    """BL-18: all valid subject types accepted."""

    @pytest.mark.parametrize("subject_type", ["student", "employee", "faculty", "staff"])
    def test_valid_subject_types(self, subject_type):
        from src.nodes.pre_process_node import PreProcessNode
        node = PreProcessNode()
        result = node(_pre_state(_valid_scope(subject_type=subject_type)))
        assert result["status"] == AgentStatus.SUCCESS.value
