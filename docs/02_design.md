# Template Design Specification — EDU-C2-052

## Position in AgentCore Architecture

- **Agent Class**: `Graph` (in `src/graph/graph.py`)
- **L1 Base**: `AgentBaseGraph` (outer) + `BaseGraph` (inner `DomainWorkflowGraph`)
- **Three-Layer Separation**:
  - State: flat TypedDict composition — all structured fields JSON-encoded (msgpack-safe)
  - Node: L1 inheritance (`FunctionNode` for domain nodes, `GraphNode` for `main` slot)
  - Graph: composition (`register_nodes()` for node substitution; inner graph in `domain_workflow_graph.py`)

## Architecture Overview

### Node Configuration

| Node | Responsibility | Input State Fields | Output State Fields | Inherits |
|------|---------------|-------------------|---------------------|---------| 
| initialize | Framework lifecycle init | — | — | `InitializeNode` (framework default) |
| pre_process | Parse/validate access-request criteria; validate approved sources | `user_input`, `input_context` | `request_criteria_json`, `approved_source_ids_json`, `validated_input` | `FunctionNode` (`PreProcessNode`) |
| main | Wraps inner DomainWorkflowGraph | `validated_input`, outer state | `request_history_json`, `policy_references_json`, `citations_json`, `briefing_draft_json`, `review_outcome`, `review_correction` | `GraphNode` (`AccessHistoryGraphNode`) |
| post_process | Assemble final traceable briefing; S-3 credential guard | `briefing_draft_json`, `review_outcome`, `review_correction`, all *_json fields | `formatted_output`, `result` | `FunctionNode` (`PostProcessNode`) |
| finalize | Framework lifecycle finalize | — | — | `FinalizeNode` (framework default) |

### Inner DomainWorkflowGraph Nodes

| Node | Responsibility | Trust Level |
|------|---------------|-------------|
| history_retrieval | Retrieve approved access-request history records from configured sources | `ANONYMOUS` |
| policy_reference | Retrieve and map policy/regulation references to request criteria; collect citations | `ANONYMOUS` |
| briefing_hitl | Assemble briefing draft; interrupt for human privacy-office review (D6 pattern) | `ANONYMOUS` |

### Data Flow

```
User invokes → Graph.invoke(user_input=<JSON criteria>)

Outer backbone:
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                        ↓ (RETRY, max 3)
                                      pre_process

Inside main (AccessHistoryGraphNode):
  DomainWorkflowGraph.invoke(validated_input)
    → history_retrieval
    → [conditional: ERROR → END]
    → policy_reference
    → briefing_hitl ──[interrupt if hitl_allowed=True]──→ human review
    → END

merge_output() maps inner sub_result → outer state
post_process formats final briefing output
```

### Runtime Configuration and Optional LLM Injection

- AgentRegistry identity is root-level in `config/agent.yaml`; runtime controls
  live in `config/config.yaml`.
- Azure OpenAI is constructed per invocation by `src/services/llm_runtime.py`.
- Retrieval, policy mapping, and briefing
  assembly remain deterministic (`generation_mode: deterministic`).
- `AccessHistoryGraphNode` retains one checkpointer-backed inner graph instance
  so HITL resume uses the checkpoint created by the initial invocation.

### State Definition

| Field | Type | Purpose | Producer |
|-------|------|---------|---------| 
| `request_criteria_json` | `str` (JSON-encoded dict) | Normalized access-request search criteria | `PreProcessNode` |
| `approved_source_ids_json` | `str` (JSON-encoded list) | Operator-configured source allowlist | `PreProcessNode` |
| `validated_input` | `str` | Cleaned/validated input string | `PreProcessNode` |
| `request_history_json` | `str` (JSON-encoded list[dict]) | Retrieved historical access-request records | `HistoryRetrievalNode` |
| `policy_references_json` | `str` (JSON-encoded list[dict]) | Policy/regulation references mapped to criteria | `PolicyReferenceNode` |
| `citations_json` | `str` (JSON-encoded list[dict]) | Provenance citation metadata for audit trail | `PolicyReferenceNode` |
| `briefing_draft_json` | `str` (JSON-encoded dict) | Assembled human-review briefing draft | `BriefingHitlNode` |
| `review_outcome` | `str` | `"approved"` / `"corrected"` / `"rejected"` | `BriefingHitlNode` (on resume) |
| `review_correction` | `str` | Operator correction text when corrected | `BriefingHitlNode` (on resume) |
| `formatted_output` | `str` | Final structured briefing (JSON) | `PostProcessNode` |

**State Constraints (mandatory):**
- Flat TypedDict only — no Pydantic, no dataclass (msgpack incompatible)
- ALL fields are primitives (`str`) — structured data is JSON-string-encoded with `to_json()`
- `to_json()` / `from_json()` helpers mandatory in every `state.py`
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- `InvocationContext` accessed via `InvocationContext.from_state(state)` inside nodes, never stored in State

## Framework Utilization

### Shared Components Used
- [x] `InvocationContext` — `ctx.secrets.require("ACCESS_HISTORY_API_KEY")` in inner nodes
- [x] `SecurityViolationError` — S-2 gate (`PreProcessNode._extra_security_gate_input`)
- [x] S-2: `_extra_security_gate_input()` — length limit (4096) + prohibited-framing guard (`PreProcessNode`)
- [x] S-3: `_extra_security_gate_output()` — credential-pattern scan on `formatted_output` (`PostProcessNode`)
- [x] S-4: `emit_trace_event()` — at least one domain event per `execute()` per node
- [x] HITL: `langgraph.types.interrupt()` via D6 pattern, guarded by `hitl_allowed`

### Security Gate Summary

| Node | S-2 `_extra_security_gate_input()` | S-3 `_extra_security_gate_output()` |
|------|------------------------------------|--------------------------------------|
| `PreProcessNode` | ✅ Length limit (4096) + prohibited-framing keywords | None needed (input JSON, no credential risk) |
| `PostProcessNode` | None needed (result is structured dict) | ✅ Regex scan for credential-like patterns in `formatted_output` |
| Inner domain nodes | Not applicable (`FunctionNode` framework default gate applies) | Not applicable |

### Composition Pattern

- **Pattern**: Cat 2 — `GraphNode` (inner `DomainWorkflowGraph` subgraph)
- **Composition target**: `DomainWorkflowGraph` (inner `BaseGraph`)
- **Error propagation strategy**: `propagate` (re-raise inner errors as `SubgraphError`)
- **HITL propagation**: `propagate_hitl = True` (inner HITL interrupt surfaced to outer caller)

## EU AI Act Art.13 Design-Time Evidence

The proposal declares this intended purpose outside Annex III. The following
transparency controls remain part of the design:

| Evidence item | Design reference / description |
|---------------|--------------------------------|
| Intended purpose and operating context | Factual access-history briefing for authorized education privacy-office personnel |
| System capabilities and limitations | Retrieves only allowlisted sources, maps citations, and summarizes records; cannot grant access, approve disclosure, or communicate externally |
| User-facing transparency information | Output identifies itself as a human-review briefing and preserves limitations, gaps, provenance, and review disposition |
| Human oversight mechanism | `BriefingHitlNode` interrupts before interactive delivery; non-interactive bypass is explicit and produces decision-support output only |

## Import Isolation Confirmation
- [x] Template does not import `agenticstar-platform` SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------| 
| L1 base type | `AgentBaseGraph` | `AutonomousBaseGraph` | `AgentBaseGraph` | Deterministic multi-step pipeline; no autonomous loop required |
| Composition pattern | `GraphNode` (inner subgraph) | Flat `AgentBaseGraph` (Cat 1) | `GraphNode` + inner `BaseGraph` | 3 distinct domain steps require separate node files for testability |
| HITL propagation | `propagate_hitl=True` | `propagate_hitl=False` | `True` | Human review of access-history briefing must surface to outer caller |
| Error strategy | `propagate` | `handle` | `propagate` | Fail-fast preferred for privacy evidence pipeline; degraded output not acceptable |
| Trust level (outer nodes) | `VERIFIED_EXTERNAL` | `ANONYMOUS` | `VERIFIED_EXTERNAL` | Outer `pre_process`/`post_process` are the trust boundary for privacy-office access |
| Trust level (inner nodes) | `ANONYMOUS` | `VERIFIED_EXTERNAL` | `ANONYMOUS` | Trust already verified at `PreProcessNode`; inner nodes are not re-entry points |
| Default hitl_allowed | `False` | `True` | `False` | Safer default; callers explicitly opt in to HITL; prevents deadlock in PB-6 testing |
