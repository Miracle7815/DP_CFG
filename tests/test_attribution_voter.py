import json

from generate_for_buggy.agents.attribution_voter import AttributionVoter
from generate_for_buggy.models.model import Model, ModelResponse


class FakeModel(Model):
    def __init__(self, name, category, valid=True):
        super().__init__(name)
        self.base_url = f"https://{name}.invalid"
        self.category = category
        self.valid = valid
        self.prompts = []
        self.calls = 0

    def call(self, messages, **kwargs):
        self.prompts.append(messages)
        self.calls += 1
        if not self.valid:
            return ModelResponse(content="not json")
        if self.calls == 1:
            return ModelResponse(content=json.dumps({
                "predicted_behavior": "The documented property holds",
                "oracle_basis": "public contract",
                "reasoning": "contract",
                "confidence": 0.8,
            }))
        return ModelResponse(content=json.dumps({
            "category": self.category,
            "reasoning": "classification",
            "confidence": 0.8,
            "fix_instructions": "repair current test" if self.category.startswith("test_") else "",
        }))


def scenario():
    return {
        "id": "S1",
        "category": "normal_path",
        "input_constraints": "A documented valid argument",
        "expected_behavior_constraints": "The documented result property holds",
    }


def vote(categories):
    models = [FakeModel(f"m{index}", category) for index, category in enumerate(categories)]
    result = AttributionVoter(models).attribute(
        scenario(),
        "@Test public void check() { assertEquals(expected, actual); }",
        "AssertionFailedError: normalized mismatch",
        "demo.Calc#f(int)",
        "Returns the documented result.",
        "public source-free context",
    )
    return result, models


def test_two_of_three_source_bug_is_kept():
    result, _ = vote(["source_bug", "source_bug", "ambiguous"])
    assert result["decision"] == "source_bug"
    assert result["accept_as_bug_detecting"] is True


def test_two_different_test_problem_categories_trigger_repair():
    result, _ = vote(["test_oracle_error", "test_implementation_error", "source_bug"])
    assert result["decision"] == "test_issue"
    assert result["category"] == "test_issue"
    assert "repair current test" in result["fix_instructions"]


def test_fewer_than_three_complete_ballots_is_ambiguous():
    models = [
        FakeModel("m1", "source_bug"),
        FakeModel("m2", "source_bug"),
        FakeModel("m3", "source_bug", valid=False),
    ]
    result = AttributionVoter(models).attribute(
        scenario(), "assertTrue(result);", "mismatch", "f(int)", "docs"
    )
    assert result["decision"] == "ambiguous"
    assert len(result["ballots"]) == 2


def test_stage_one_hides_authored_oracle_and_actual_result():
    result, models = vote(["ambiguous", "ambiguous", "ambiguous"])
    assert result["decision"] == "ambiguous"
    first_stage = json.dumps(models[0].prompts[0])
    assert "expected_behavior_constraints" not in first_stage
    assert "normalized mismatch" not in first_stage

