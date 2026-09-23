"""PB-6: prove the real BaseNode invocation and S-1 denial boundaries."""

import importlib
import inspect
import json
import pkgutil
from typing import ClassVar

import pytest

from framework.nodes.base_node import BaseNode
from framework.schemas.trust_level import TrustLevel


class _PrivilegedTrustGateFixture(BaseNode):
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _security_gate_input(self, state):
        return state

    def execute(self, state):
        return {"status": "success"}

    def _security_gate_output(self, result):
        return result


def _discover_node_classes() -> list[type]:
    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError as exc:
        pytest.fail(f"PB-6 cannot import src.nodes: {exc}")

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


def _state(required_trust: TrustLevel) -> dict:
    scope = {
        "subject_type": "student",
        "subject_id": "PB6-001",
        "date_from": "2025-01-01",
        "date_to": "2025-12-31",
        "approved_source_ids": ["source-a"],
    }
    return {
        "caller_trust_level": required_trust.value,
        "correlation_id": "pb6-correlation",
        "session_id": "pb6-session",
        "thread_id": "pb6-thread",
        "trace_id": "pb6-trace",
        "node_history": [],
        "error_log": [],
        "status": "success",
        "hitl_allowed": False,
        "stg_mock_mode": True,
        "user_input": json.dumps(scope),
        "request_criteria_json": json.dumps(scope),
        "approved_source_ids_json": json.dumps(["source-a"]),
        "request_history_json": "[]",
        "policy_references_json": "[]",
        "citations_json": "[]",
        "briefing_draft_json": "",
        "review_outcome": "approved",
        "review_correction": "",
    }


class TestInvokeOrder:
    def test_call_order_for_every_node(self, monkeypatch):
        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in _discover_node_classes():
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _order=order: _order.append(
                    f"event:{event_type}"
                ),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _order=order, _label=label, _original=original):
                    _order.append(_label)
                    return _original(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            node_cls()(_state(node_cls.required_trust_level))
            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(f"{node_cls.__name__}: expected {expected}, got {order}")

        assert not failures, "\n".join(failures)

    def test_s1_denial_refuses_execution_before_execute(self, monkeypatch):
        import framework.nodes.base_node as base_node_module

        events: list[str] = []
        execute_calls: list[object] = []
        monkeypatch.setattr(
            base_node_module,
            "emit_trace_event",
            lambda event_type, _payload, _state: events.append(event_type),
        )
        original_execute = _PrivilegedTrustGateFixture.execute

        def spy_execute(self, state):
            execute_calls.append(state)
            return original_execute(self, state)

        monkeypatch.setattr(_PrivilegedTrustGateFixture, "execute", spy_execute)
        result = _PrivilegedTrustGateFixture()(
            {
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
                "correlation_id": "pb6-s1-denial",
            }
        )

        assert result["status"] == "error"
        assert "S-1 trust gate denied" in result["error_log"][0]
        assert events == ["s1_denied"]
        assert not execute_calls
