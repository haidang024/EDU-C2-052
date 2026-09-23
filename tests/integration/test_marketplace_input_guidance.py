import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph


def test_invalid_marketplace_history_request_returns_readable_guidance():
    graph = Graph(config={})
    graph.compile()
    result = graph.invoke(
        "Hello",
        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"conversation_history": []},
    )
    assert result["status"] == "success"
    assert result["output"].startswith("Data access history request could not be processed.")
    assert "not valid JSON" in result["output"]


def test_success_marketplace_output_is_readable_but_api_stays_json():
    graph = Graph(config={})
    formatted_output = json.dumps(
        {
            "review_disposition": "approved",
            "executive_summary": "Two access requests were recorded during the review period.",
            "history_record_count": 2,
            "policy_reference_count": 1,
            "citations": ["Access Policy section 5"],
            "limitations": ["Manual verification is still required."],
            "notice": "This briefing is for authorized human review only.",
        }
    )
    api_result = graph.get_output({"formatted_output": formatted_output})
    marketplace_result = graph.get_output(
        {"formatted_output": formatted_output, "input_context": {"conversation_history": []}}
    )
    assert api_result.get("output", api_result.get("formatted_output")) == formatted_output
    assert marketplace_result["output"].startswith("# Data Access Request History Briefing")
    assert "History records: 2" in marketplace_result["output"]
    assert not marketplace_result["output"].lstrip().startswith("{")
