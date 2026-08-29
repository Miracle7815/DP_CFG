from generate_for_buggy.agents.test_executor import (
    Defects4JBuildAdapter,
    MavenBuildAdapter,
    TestExecutor as JavaTestExecutor,
    select_build_adapter,
)


def parser():
    return object.__new__(JavaTestExecutor)


def test_zero_exit_with_test_evidence_passes():
    result = parser()._parse_junit_output("Tests run: 1, Failures: 0", 0, "check")
    assert result.status == "ALL_PASSED"


def test_zero_exit_without_test_result_is_unknown():
    result = parser()._parse_junit_output("", 0, "check")
    assert result.status == "UNKNOWN_ERROR"
    assert not result.method_results


def test_nonzero_unknown_output_never_passes():
    result = parser()._parse_junit_output("build stopped unexpectedly", 3, "check")
    assert result.status == "UNKNOWN_ERROR"


def test_assertion_and_compilation_failures_are_distinguished():
    assertion = parser()._parse_junit_output(
        "org.opentest4j.AssertionFailedError: expected true", 1, "check"
    )
    compilation = parser()._parse_junit_output(
        "COMPILATION ERROR cannot find symbol: Missing", 1, "check"
    )
    assert assertion.status == "ASSERTION_FAILURE"
    assert compilation.status == "COMPILATION_ERROR"


def test_build_adapter_prefers_maven_only_when_pom_exists(tmp_path):
    assert isinstance(select_build_adapter(str(tmp_path), 10), Defects4JBuildAdapter)
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert isinstance(select_build_adapter(str(tmp_path), 10), MavenBuildAdapter)
