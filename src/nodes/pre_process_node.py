"""PreProcessNode — validates and normalizes the access-history request criteria."""

from __future__ import annotations

import json
import re
from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import to_json

# Prohibited framing — operators must not embed automated-decision or
# disclosure directives in the request criteria.
_PROHIBITED_PATTERNS = re.compile(
    r"\b(auto[_\s-]?approve|auto[_\s-]?disclose|send[_\s-]?disclosure|"
    r"grant[_\s-]?access|override[_\s-]?policy|bypass[_\s-]?review)\b",
    re.IGNORECASE,
)

_MAX_INPUT_LEN = 4096

_REQUIRED_FIELDS = {"subject_type", "subject_id", "date_from", "date_to", "approved_source_ids"}
_VALID_SUBJECT_TYPES = {"student", "employee", "faculty", "staff"}

# Date format: YYYY-MM-DD
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PreProcessNode(FunctionNode):
    """Parse, validate and normalize the incoming data-access request criteria.

    Required input JSON fields:
      subject_type      — "student" | "employee" | "faculty" | "staff"
      subject_id        — non-empty identifier (no personal data stored in state)
      date_from         — YYYY-MM-DD
      date_to           — YYYY-MM-DD
      approved_source_ids — non-empty list of approved source identifiers

    Optional:
      request_type      — filter by request category
      operator_id       — privacy-office operator reference

    S-2 extra gate checks:
      - Input length ≤ 4096 characters
      - No prohibited automated-decision/disclosure framing
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict) -> dict:
        """S-2: length limit and prohibited-framing guard."""
        user_input = state.get("user_input", "")
        if len(user_input) > _MAX_INPUT_LEN:
            raise SecurityViolationError(
                f"PreProcessNode: input length {len(user_input)} exceeds limit {_MAX_INPUT_LEN}"
            )
        if _PROHIBITED_PATTERNS.search(user_input):
            raise SecurityViolationError(
                "PreProcessNode: input contains prohibited automated-decision or disclosure framing"
            )
        return state

    def execute(self, state: dict) -> dict:
        user_input = state.get("user_input", "").strip()

        if not user_input:
            emit_trace_event("PreProcessNode_validation_failed", {"reason": "empty_input"}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "input_error_message": "No data-access history request was provided.",
                "input_error_guidance": [
                    "Provide JSON with subject_type, subject_id, date_from, date_to, and approved_source_ids.",
                    "Use YYYY-MM-DD dates and at least one approved source identifier.",
                ],
            }

        try:
            scope = json.loads(user_input)
        except (json.JSONDecodeError, ValueError):
            emit_trace_event("PreProcessNode_validation_failed", {"reason": "invalid_json"}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "input_error_message": "The data-access history request is not valid JSON.",
                "input_error_guidance": ["Provide a complete JSON object with the required request criteria."],
            }

        if not isinstance(scope, dict):
            emit_trace_event("PreProcessNode_validation_failed", {"reason": "not_dict"}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "input_error_message": "The data-access history request must be a JSON object.",
                "input_error_guidance": [
                    "Provide subject_type, subject_id, date_from, date_to, and approved_source_ids."
                ],
            }

        # Required-field check
        missing = [f for f in _REQUIRED_FIELDS if not scope.get(f)]
        if missing:
            emit_trace_event("PreProcessNode_validation_failed", {"reason": "missing_fields", "fields": missing}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "input_error_message": f"Required data-access history fields are missing: {missing}.",
                "input_error_guidance": ["Add every required field and at least one approved source identifier."],
            }

        # subject_type validation
        subject_type = scope["subject_type"]
        if subject_type not in _VALID_SUBJECT_TYPES:
            emit_trace_event("PreProcessNode_validation_failed", {"reason": "invalid_subject_type"}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "input_error_message": f"Subject type '{subject_type}' is not supported.",
                "input_error_guidance": [f"Use one of: {', '.join(sorted(_VALID_SUBJECT_TYPES))}."],
            }

        # Date format validation
        for date_field in ("date_from", "date_to"):
            if not _DATE_RE.match(str(scope.get(date_field, ""))):
                emit_trace_event("PreProcessNode_validation_failed", {"reason": f"invalid_{date_field}"}, state)
                return {
                    "status": AgentStatus.SUCCESS.value,
                    "input_error_message": f"{date_field} must use YYYY-MM-DD format.",
                    "input_error_guidance": ["Correct both date_from and date_to before trying again."],
                }

        # approved_source_ids validation
        approved_source_ids = scope.get("approved_source_ids", [])
        if not isinstance(approved_source_ids, list) or not approved_source_ids:
            emit_trace_event("PreProcessNode_validation_failed", {"reason": "empty_approved_source_ids"}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "input_error_message": "approved_source_ids must be a non-empty list.",
                "input_error_guidance": ["Add one or more operator-approved source identifiers."],
            }

        # Build normalized criteria (no personal data stored beyond scope reference)
        criteria = {
            "subject_type": subject_type,
            "subject_id": scope["subject_id"],
            "date_from": scope["date_from"],
            "date_to": scope["date_to"],
            "request_type": scope.get("request_type", ""),
            "operator_id": scope.get("operator_id", ""),
        }

        emit_trace_event(
            "PreProcessNode_validated",
            {"subject_type": subject_type, "source_count": len(approved_source_ids)},
            state,
        )

        return {
            "request_criteria_json": to_json(criteria),
            "approved_source_ids_json": to_json(approved_source_ids),
            "validated_input": user_input,
            "status": AgentStatus.SUCCESS.value,
        }
