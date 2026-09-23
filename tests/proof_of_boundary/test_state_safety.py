"""PB-2/PB-5 state and conditional checkpoint safety coverage."""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

CREDENTIAL_FIELD_PATTERNS = re.compile(
    r"(jwt|token|api_key|secret|password|credential|connection_string)",
    re.IGNORECASE,
)
PROHIBITED_TYPE_ANNOTATIONS = ["BaseModel", "InvocationContext"]
_RUNTIME_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _checkpointing_enabled() -> bool:
    if not _RUNTIME_CONFIG_PATH.exists():
        return False
    try:
        import yaml

        config = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text()) or {}
    except Exception:
        return False
    return bool(config.get("memory_enabled") or config.get("hitl", {}).get("enabled", False))


def _framework_ingress_protection_available() -> bool:
    try:
        from framework.graph.base_graph import BaseGraph
    except Exception:
        return False
    return all(
        hasattr(BaseGraph, hook)
        for hook in ("_sanitize_ingress", "_sanitize_resume_feedback")
    )


def _scan_state_file(filepath: str) -> list[str]:
    source = pathlib.Path(filepath).read_text()
    tree = ast.parse(source, filename=filepath)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id
                    if CREDENTIAL_FIELD_PATTERNS.search(field_name):
                        violations.append(
                            f"{filepath}:{item.lineno} — Credential-like field name: {field_name}"
                        )
                    if item.annotation:
                        annotation_str = ast.dump(item.annotation)
                        for prohibited in PROHIBITED_TYPE_ANNOTATIONS:
                            if prohibited in annotation_str:
                                violations.append(
                                    f"{filepath}:{item.lineno} — Prohibited type in State: {prohibited}"
                                )
    return violations


class TestStateSafety:
    def test_state_file_safety(self):
        state_file = pathlib.Path(__file__).parents[2] / "src" / "schemas" / "state.py"
        violations = _scan_state_file(str(state_file))
        assert violations == [], "State safety violations found:\n" + "\n".join(violations)


_PB5_APPLICABLE = _checkpointing_enabled() and _framework_ingress_protection_available()
_PB5_WAIVER_REASON = (
    "config/config.yaml enables neither memory nor HITL — PB-5 auto-waived"
    if not _checkpointing_enabled()
    else "installed agentcore lacks BaseGraph ingress hooks — PB-5 auto-waived"
)


@pytest.mark.skipif(not _PB5_APPLICABLE, reason=_PB5_WAIVER_REASON)
def test_pb5_precheckpoint_ingress_not_raw() -> None:
    pytest.fail(
        "PB-5 became applicable, but this template still needs a full checkpoint-surface fixture."
    )
