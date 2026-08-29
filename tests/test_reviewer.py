from generate_for_buggy.agents.agent_reviewer import ReviewAgent


def test_schema_failure_is_rejected_before_model_call():
    result = ReviewAgent().review_test_scenarios(
        {"method_intent": "", "input_output_spec": {}, "test_scenarios": []},
        "demo.Calc#f(int)",
        "docs",
    )
    assert result["result"] == "No"
    assert result["issues"]


def test_assertion_failure_cannot_use_legacy_single_model_attribution():
    result = ReviewAgent().review_test_failure(
        {}, "", "ASSERTION_FAILURE", "mismatch"
    )
    assert result["diagnosis"] == "ambiguous"
    assert result["accept_as_bug_detecting"] is False

