import pytest

from generate_for_buggy.contracts import (
    BranchStep,
    ContractError,
    CoverageGoal,
    SymbolicPredicate,
    assert_public_coverage_goal,
    validate_scenario,
    validate_scenario_file,
)


def canonical_scenario(**overrides):
    scenario = {
        "id": "S1",
        "origin": "initial",
        "category": "normal_path",
        "description": "A documented normal behavior",
        "input_constraints": "The argument satisfies the documented precondition",
        "expected_behavior_constraints": "The documented result property holds",
        "oracle_basis": "The return-value clause in the public documentation",
        "priority": "high",
        "status": "draft",
    }
    scenario.update(overrides)
    return scenario


def test_canonical_scenario_is_accepted():
    scenario_file = {
        "method_intent": "Compute a documented result",
        "input_output_spec": {},
        "test_scenarios": [canonical_scenario()],
    }
    assert validate_scenario_file(scenario_file, "initial") == []


@pytest.mark.parametrize("legacy", ["type", "input", "expected_behavior"])
def test_legacy_scenario_fields_are_rejected(legacy):
    scenario = canonical_scenario(**{legacy: "legacy"})
    assert any("legacy fields" in issue for issue in validate_scenario(scenario))


def test_coverage_scenario_requires_goal_id():
    issues = validate_scenario(canonical_scenario(origin="coverage"))
    assert any("coverage_goal_id" in issue for issue in issues)


def test_coverage_probe_is_coverage_only_and_cannot_claim_an_oracle():
    initial = canonical_scenario(oracle_basis="coverage_probe_only")
    assert any("valid only" in issue for issue in validate_scenario(initial))
    coverage = canonical_scenario(
        origin="coverage",
        coverage_goal_id="CG_x",
        oracle_basis="coverage_probe_only",
        expected_behavior_constraints="The target is invoked using the required abstract path",
    )
    assert validate_scenario(coverage) == []
    coverage["expected_behavior_constraints"] = "Returns a particular result"
    assert any("cannot claim" in issue for issue in validate_scenario(coverage))


def test_concrete_values_in_constraints_are_rejected():
    issues = validate_scenario(canonical_scenario(input_constraints="The argument equals 42"))
    assert any("concrete literals" in issue for issue in issues)


def test_concrete_values_in_input_output_spec_are_rejected():
    scenario_file = {
        "method_intent": "Compute a documented result",
        "input_output_spec": {"example": 42},
        "test_scenarios": [canonical_scenario()],
    }
    assert any("concrete input or output values" in issue for issue in validate_scenario_file(scenario_file))


def test_public_goal_rejects_values_and_numeric_payloads():
    with pytest.raises(ContractError):
        assert_public_coverage_goal({"goal_id": "CG_x", "value": "hidden"})
    with pytest.raises(ContractError):
        assert_public_coverage_goal({"goal_id": "CG_x", "count": 7})


def test_public_goal_contains_only_opaque_anchor_not_private_constant():
    goal = CoverageGoal(
        goal_id="CG_x",
        target_edge=BranchStep("BRANCH_x", "taken"),
        path_signature=(BranchStep("BRANCH_x", "taken"),),
        symbolic_predicates=(
            SymbolicPredicate("ARG_0", "value", "greater_than", anchor="κ0"),
        ),
        support_status="supported",
    ).to_public_dict()
    assert goal["symbolic_predicates"][0]["anchor"] == "κ0"
    assert "41" not in repr(goal)
