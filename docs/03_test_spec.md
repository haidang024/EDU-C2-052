# Test Specification — EDU-C2-052

## Test Strategy
- Coverage target: 80%
- Test types: Unit / Integration / Proof-of-Boundary
- Runner: `pytest` against the CI-provided AgentCore wheel; no local framework shims

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Imports without error; `State` extends `AgentState`; `to_json`/`from_json` helpers present and functional | ✅ PASS |
| TC-02 | `SecurityViolationError` fires on S-2 gate | `PreProcessNode._extra_security_gate_input()` raises `SecurityViolationError` on prohibited framing | ✅ PASS |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations | ✅ PASS |
| TC-04 | `InvocationContext` via configurable only | Nodes access context via `InvocationContext.from_state(state)` — no direct state field | ✅ PASS |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from all `execute()` bodies | ✅ PASS |
| TC-06 | S-2: `_security_gate_input()` not overridden | Real framework raises `TypeError` at class definition | ✅ PASS |
| TC-07 | S-3: `_security_gate_output()` not overridden | Real framework raises `TypeError` at class definition | ✅ PASS |
| TC-08 | `required_trust_level` enforced | Under-privileged caller is refused before `execute()` | ✅ PASS |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial | `PreProcessNode`: length limit + prohibited-framing guard raise `SecurityViolationError` | ✅ PASS |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial | `PostProcessNode`: credential regex scan raises `SecurityViolationError` on bearer/api_key match | ✅ PASS |
| TC-11 | S-4: at least one domain `emit_trace_event()` per `execute()` | All 5 domain nodes (`PreProcess`, `HistoryRetrieval`, `PolicyReference`, `BriefingHitl`, `PostProcess`) emit at least one domain event | ✅ PASS |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures (verified via TC-11 monkeypatch) | ✅ PASS |
| PB-2 | State serialization | `State` contains only `str` primitives (no `dict`, `list`, `Pydantic`) | AST scan: 0 violations | ✅ PASS |
| PB-3 | Level 2 → External service | `AccessHistoryService` fake adapter returns normalised records | Data retrieved and provenance preserved | ✅ PASS (BL-08) |
| PB-4 | Import isolation | No Level 0 imports in `src/` | AST scan: 0 violations | ✅ PASS |
| PB-5 | Checkpoint safety *(conditional)* | Full checkpoint surfaces inspected when AgentCore exposes ingress hooks | Auto-waived on AgentCore releases without both hooks |
| PB-6 | Invoke execution order | `__call__()`: `S-1 trust gate → node_start → S-2 → execute → S-3 → node_complete` | Order verified for all 5 concrete node classes | ✅ PASS |
| PB-7 | HITL interrupt propagation *(active — `config/config.yaml` enables HITL)* | `interrupt()` raises `GraphInterrupt` when `hitl_allowed=True`; no interrupt when `hitl_allowed=False` | `GraphInterrupt` propagates; `hitl_allowed=False` auto-approves | ✅ PASS |
| PB-8 | Standalone adapter injection/auth | Optional LLM reaches Cat-2 wrapper and inner config; tokens map to exact trust levels | ✅ PASS |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Valid scope accepted and normalized | JSON criteria with all required fields | `request_criteria_json` + `approved_source_ids_json` populated; `status=success` | ✅ PASS |
| BL-02 | Empty user_input rejected | `""` | `status=error`; error_log populated | ✅ PASS |
| BL-03 | Non-JSON input rejected | `"not json"` | `status=error` | ✅ PASS |
| BL-04 | Missing required field rejected | Criteria without `subject_type` or `approved_source_ids` | `status=error`; field name in error_log | ✅ PASS |
| BL-05 | Invalid values rejected | Invalid `subject_type`, bad date format, empty `approved_source_ids` | `status=error` | ✅ PASS |
| BL-06 | Empty approved sources → empty records | `approved_source_ids_json=[]` | `request_history_json=[]`; `status=success` | ✅ PASS |
| BL-07 | Missing criteria → retrieval error | `request_criteria_json=""` | `status=error` | ✅ PASS |
| BL-08 | Provider records mapped with provenance | Fake adapter with record | Records in `request_history_json`; `provenance_url` preserved | ✅ PASS |
| BL-09 | Policy references with citation URLs | Fake adapter with policy ref | `policy_references_json` + `citations_json` populated; `url` present | ✅ PASS |
| BL-10 | No records → uncertainty marker | Empty history | Uncertainty language in briefing draft | ✅ PASS |
| BL-11 | Citations collected from policy refs | Policy ref with `citation_url` | `citations_json` populated; `url` present | ✅ PASS |
| BL-12 | Gap flagged when no policy references | No policy refs from source | `has_gaps=True` in `briefing_draft_json` | ✅ PASS |
| BL-13 | Output is advisory, not automated decision | PostProcess output | No "access granted" / "disclosure approved" in output | ✅ PASS |
| BL-14 | Briefing assembled with HITL disabled | `hitl_allowed=False` | `briefing_draft_json` populated; `review_outcome=approved` | ✅ PASS |
| BL-15 | Resume: approve/correct/reject → correct outcome | HITL feedback variants | `review_outcome` set correctly; `review_correction` set on correct | ✅ PASS |
| BL-16 | Approved output includes all sections | `review_outcome=approved` | `agent`, `notice`, `review_disposition`, `executive_summary`, `citations` present | ✅ PASS |
| BL-16b | Rejected output marked | `review_outcome=rejected` | `[REJECTED BY REVIEWER]` in `executive_summary` | ✅ PASS |
| BL-16c | Corrected output includes correction | `review_outcome=corrected` | `operator_correction` field present | ✅ PASS |
| BL-17 | Full pipeline completes without provider | `graph.invoke()` end-to-end, no provider records | Completes; uncertainty/gap markers present | ✅ PASS |
| BL-18 | All valid subject types accepted | `student`, `employee`, `faculty`, `staff` | `status=success` for each | ✅ PASS |

## Test Execution Summary
- Execution date: 2026-08-18
- Full suite: 72 passed / 0 failed / 1 conditional PB-5 waiver
- Proof-of-boundary suite: 13 passed / 0 failed / 1 conditional PB-5 waiver
- Static checks: Ruff, Ruff format, and mypy all pass
- Stage 5: v1 invoke evidence and advisory v2 validation pass
