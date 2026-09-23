# tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py
#
# PB-7: HITL interrupt propagation for EDU-C2-052.
# Active when config/config.yaml has hitl.enabled: true.

from __future__ import annotations

import pathlib
import warnings

import pytest

from src.schemas.state import to_json

# ---------------------------------------------------------------------------
# Conditional skip — only runs when config/config.yaml has hitl.enabled: true
# ---------------------------------------------------------------------------

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _hitl_enabled() -> bool:
    """Return True when config/config.yaml declares hitl.enabled: true."""
    if not _CONFIG_PATH.exists():
        warnings.warn(f"{_CONFIG_PATH} not found — PB-7 skipped.", stacklevel=2)
        return False
    try:
        import yaml

        data = yaml.safe_load(_CONFIG_PATH.read_text())
    except Exception as exc:
        warnings.warn(f"{_CONFIG_PATH} could not be read ({exc}) — PB-7 skipped.", stacklevel=2)
        return False
    hitl = (data or {}).get("hitl", {}) if isinstance(data, dict) else None
    if not isinstance(hitl, dict):
        warnings.warn(f"{_CONFIG_PATH} has no valid hitl mapping — PB-7 skipped.", stacklevel=2)
        return False
    return bool(hitl.get("enabled", False))


pytestmark = pytest.mark.skipif(
    not _hitl_enabled(),
    reason="config/config.yaml does not set hitl.enabled: true — PB-7 not applicable",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _base_state(**overrides) -> dict:
    from framework.schemas.agent_status import AgentStatus
    from framework.schemas.trust_level import TrustLevel

    state = {
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "correlation_id": "pb7-test",
        "node_history": [],
        "error_log": [],
        "status": AgentStatus.SUCCESS.value,
        "hitl_allowed": True,
        "hitl_count": 0,
        "request_criteria_json": to_json({"subject_type": "student"}),
        "request_history_json": to_json([]),
        "policy_references_json": to_json([]),
        "citations_json": to_json([]),
        "briefing_draft_json": "",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# PB-7-A: interrupt() raises GraphInterrupt and propagates
# ---------------------------------------------------------------------------


def test_pb7_hitl_interrupt_propagates(monkeypatch) -> None:
    """PB-7: interrupt() raises GraphInterrupt and propagates (not caught by app boundary)."""
    from langgraph.errors import GraphInterrupt
    from langgraph.types import Interrupt
    import langgraph.types as langgraph_types
    from src.nodes.briefing_hitl_node import BriefingHitlNode

    def raise_interrupt(value: object) -> None:
        raise GraphInterrupt((Interrupt(value=value),))

    monkeypatch.setattr(langgraph_types, "interrupt", raise_interrupt)

    # interrupt() only fires on a deployment that can resume it: hitl.enabled
    # AND memory_enabled (the checkpointer). A no-config node is deliberately
    # non-resumable — see BriefingHitlNode._hitl_resumable.
    node = BriefingHitlNode(config={"hitl": {"enabled": True}, "memory_enabled": True})
    state = _base_state(hitl_allowed=True)

    with pytest.raises(GraphInterrupt):
        node(state)


# ---------------------------------------------------------------------------
# PB-7-B: hitl_allowed=False guard prevents deadlock
# ---------------------------------------------------------------------------


def test_pb7_hitl_allowed_false_skips_interrupt() -> None:
    """PB-7 guard: hitl_allowed=False must NOT raise GraphInterrupt (no deadlock)."""
    from src.nodes.briefing_hitl_node import BriefingHitlNode

    node = BriefingHitlNode()
    state = _base_state(hitl_allowed=False)

    result = node(state)
    assert result.get("review_outcome") == "approved"
    assert result.get("briefing_draft_json")


# ---------------------------------------------------------------------------
# PB-7-B2: non-resumable entry point must not suspend
# ---------------------------------------------------------------------------


def test_pb7_non_resumable_entrypoint_skips_interrupt(monkeypatch) -> None:
    """A one-shot entry point (no checkpointer) must complete, not suspend.

    The Marketplace runner builds the graph as ``agent_cls()`` with no config and
    has no resume channel, so a suspended run never terminates — the chat UI shows
    "Thinking..." indefinitely. Even with hitl_allowed=True (its default), the node
    must auto-approve rather than call interrupt().
    """
    from langgraph.errors import GraphInterrupt
    from langgraph.types import Interrupt
    import langgraph.types as langgraph_types
    from src.nodes.briefing_hitl_node import BriefingHitlNode

    def raise_interrupt(value: object) -> None:  # pragma: no cover - must not run
        raise GraphInterrupt((Interrupt(value=value),))

    monkeypatch.setattr(langgraph_types, "interrupt", raise_interrupt)

    # No config == the Marketplace construction path.
    node = BriefingHitlNode()
    result = node(_base_state(hitl_allowed=True))

    assert result.get("review_outcome") == "approved"
    assert result.get("status") == "success"

    # hitl.enabled without memory_enabled is also non-resumable: no checkpointer.
    node_no_ckpt = BriefingHitlNode(config={"hitl": {"enabled": True}, "memory_enabled": False})
    assert node_no_ckpt(_base_state(hitl_allowed=True)).get("review_outcome") == "approved"


# ---------------------------------------------------------------------------
# PB-7-C: corrected feedback routing
# ---------------------------------------------------------------------------


def test_pb7_corrected_feedback_routing() -> None:
    """PB-7: corrected feedback sets review_outcome=corrected."""
    from src.nodes.briefing_hitl_node import BriefingHitlNode

    node = BriefingHitlNode()
    draft = to_json({"history_summary": "none", "policy_summary": "none", "has_gaps": False,
                     "notice": "test", "request_criteria": {}, "history_record_count": 0,
                     "policy_reference_count": 0, "citations": []})
    state = _base_state(
        hitl_allowed=True,
        briefing_draft_json=draft,
        hitl_feedback={"action": "correct", "corrected_output": "Correction here", "reason": "minor"},
    )

    result = node._process_review(state, {"action": "correct", "corrected_output": "Correction here", "reason": "minor"})
    assert result["review_outcome"] == "corrected"
    assert result["review_correction"] == "Correction here"


# ---------------------------------------------------------------------------
# PB-7-D: rejected feedback routing
# ---------------------------------------------------------------------------


def test_pb7_rejected_feedback_routing() -> None:
    """PB-7: rejected feedback sets review_outcome=rejected and status=error."""
    from framework.schemas.agent_status import AgentStatus
    from src.nodes.briefing_hitl_node import BriefingHitlNode

    node = BriefingHitlNode()
    draft = to_json({"history_summary": "none", "policy_summary": "none", "has_gaps": False,
                     "notice": "test", "request_criteria": {}, "history_record_count": 0,
                     "policy_reference_count": 0, "citations": []})
    state = _base_state(briefing_draft_json=draft)

    result = node._process_review(state, {"action": "reject", "reason": "incomplete data"})
    assert result["review_outcome"] == "rejected"
    assert result["status"] == AgentStatus.ERROR.value
