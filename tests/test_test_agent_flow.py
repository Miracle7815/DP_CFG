from types import SimpleNamespace

from generate_for_buggy.agents.agent_test import (
    TestAgent as JavaTestAgent,
    remove_test_method,
    validate_generated_test_method,
)
from generate_for_buggy.agents.test_executor import ExecutionResult, TestMethodResult as JavaTestMethodResult


def approved_scenario():
    return {
        "id": "S1",
        "origin": "initial",
        "category": "normal_path",
        "description": "Documented behavior",
        "input_constraints": "A documented valid argument",
        "expected_behavior_constraints": "The documented result property holds",
        "oracle_basis": "Return clause",
        "priority": "high",
        "status": "approved",
    }


class FakeExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.candidates = []
        self.restored = None

    def diagnose(self, candidate, test_class_sig, method_name):
        self.candidates.append(candidate)
        return self.results.pop(0)

    def write_test(self, code, test_class_sig):
        self.restored = code


class FakeVoter:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def attribute(self, **kwargs):
        return self.decisions.pop(0)


def agent(tmp_path, executor, voter):
    created = object.__new__(JavaTestAgent)
    created.project_name = "Fixture_1"
    created.project_loc = str(tmp_path)
    created.test_loc = "src/test/java"
    created.src_loc = "src/main/java"
    created.method_map = {}
    created.class_map = {}
    created.executor = executor
    created.attribution_voter = voter
    created.constructor_info = {}
    created.target_method = SimpleNamespace(
        signature="demo.Calc#f(int)",
        javadoc="Returns a documented result.",
    )
    created.test_class_sig = "demo.CalcGeneratedTest"
    created.test_class_code = "package demo;\npublic class CalcGeneratedTest {\n}\n"
    created.public_context = "public context"
    created.coverage_goals = {}
    created.witness_binder = None
    created.coverage_verifier = None
    return created


def assertion_failure():
    return ExecutionResult(
        status="ASSERTION_FAILURE",
        method_results=[JavaTestMethodResult(
            method_name="check",
            passed=False,
            exception_type="AssertionFailedError",
            exception_message="mismatch",
        )],
        failure_phase="assertion",
    )


def test_majority_test_issue_replaces_current_method_then_accepts(tmp_path):
    executor = FakeExecutor([assertion_failure(), ExecutionResult(status="ALL_PASSED")])
    voter = FakeVoter([{
        "decision": "test_issue",
        "category": "test_oracle_error",
        "fix_instructions": "repair oracle",
        "accept_as_bug_detecting": False,
        "ballots": [],
    }])
    test_agent = agent(tmp_path, executor, voter)
    methods = iter([
        "@Test public void check() { assertEquals(1, 2); }",
        "@Test public void check() { assertEquals(2, 2); }",
    ])
    test_agent._generate_test_method = lambda *args, **kwargs: next(methods)

    scenario = approved_scenario()
    result = test_agent.generate_test_for_scenario(
        scenario, {"method_intent": "intent", "input_output_spec": {}, "test_scenarios": [scenario]}
    )
    assert result["final_status"] == "passed"
    assert len(executor.candidates) == 2
    assert "assertEquals(1, 2)" not in test_agent.test_class_code
    assert "assertEquals(2, 2)" in test_agent.test_class_code


def test_majority_source_bug_keeps_failing_test(tmp_path):
    executor = FakeExecutor([assertion_failure()])
    voter = FakeVoter([{
        "decision": "source_bug",
        "category": "source_bug",
        "fix_instructions": "",
        "accept_as_bug_detecting": True,
        "ballots": [{}, {}, {}],
    }])
    test_agent = agent(tmp_path, executor, voter)
    test_agent._generate_test_method = lambda *args, **kwargs: (
        "@Test public void revealsBug() { assertEquals(1, 2); }"
    )
    scenario = approved_scenario()
    result = test_agent.generate_test_for_scenario(
        scenario, {"method_intent": "intent", "input_output_spec": {}, "test_scenarios": [scenario]}
    )
    assert result["final_status"] == "source_bug"
    assert "revealsBug" in test_agent.test_class_code


def test_generated_method_rejects_reflection_unsafe_and_placeholder_oracles():
    issues = validate_generated_test_method(
        "@Test public void bad() { value.getClass().getDeclaredField(\"secret\"); assertTrue(true); }"
    )
    assert "Reflection and Unsafe are forbidden" in issues
    assert "Placeholder assertions are forbidden" in issues


def test_coverage_probe_has_no_assertion_and_is_removed_from_final_class():
    probe = "@Test public void explore() { target.run(); }"
    assert validate_generated_test_method(probe, coverage_probe_only=True) == []
    class_code = (
        "public class Generated {\n"
        "    @Test public void keep() { assertTrue(target.ok()); }\n"
        "    " + probe + "\n}\n"
    )
    final_code = remove_test_method(class_code, "explore")
    assert "explore" not in final_code
    assert "keep" in final_code
    assert final_code.strip().endswith("}")


def test_probe_execution_is_kept_temporarily_but_not_marked_as_final_pass(tmp_path):
    executor = FakeExecutor([ExecutionResult(status="ALL_PASSED")])
    test_agent = agent(tmp_path, executor, FakeVoter([]))
    test_agent.coverage_verifier = lambda *args: (True, {"status": "target_hit"})
    test_agent._generate_test_method = lambda *args, **kwargs: (
        "@Test public void exploreOnly() { target.run(); }"
    )
    scenario = approved_scenario()
    scenario.update({
        "origin": "coverage",
        "coverage_goal_id": "CG_x",
        "oracle_basis": "coverage_probe_only",
        "expected_behavior_constraints": "The target is invoked using the required abstract path",
    })
    result = test_agent.generate_test_for_scenario(
        scenario, {"method_intent": "intent", "input_output_spec": {}, "test_scenarios": [scenario]}
    )
    assert result["final_status"] == "coverage_probe"
    assert result["target_hit"] is True


def test_test_environment_is_reported_without_existing_test_bodies(tmp_path):
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies><dependency><artifactId>junit-jupiter</artifactId></dependency></dependencies></project>",
        encoding="utf-8",
    )
    test_dir = tmp_path / "src" / "test" / "java" / "demo"
    test_dir.mkdir(parents=True)
    (test_dir / "ExistingTest.java").write_text(
        "import org.junit.jupiter.api.Test;\nclass ExistingTest { @Test void verifiesResult() { secretBody(); } }",
        encoding="utf-8",
    )
    created = object.__new__(JavaTestAgent)
    created.project_loc = str(tmp_path)
    created.test_loc = "src/test/java"
    created.test_environment = {}
    environment = created.discover_test_environment()
    assert environment["build_adapter"] == "maven"
    assert environment["junit_style"] == "junit5"
    assert "junit-jupiter" in environment["test_dependencies"]
    assert "secretBody" not in repr(environment)
