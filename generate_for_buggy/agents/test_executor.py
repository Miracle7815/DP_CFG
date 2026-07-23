"""
TestExecutor - Deterministic test compilation, execution, and result parsing.
Called by TestAgent. Not an LLM agent.
"""
import os
import re
import subprocess
import shutil
from dataclasses import dataclass, field
from typing import Optional

from ..config import logger


@dataclass
class TestMethodResult:
    method_name: str
    passed: bool
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    stack_trace: Optional[str] = None


@dataclass
class ExecutionResult:
    status: str  # COMPILATION_ERROR / ASSERTION_FAILURE / RUNTIME_ERROR / ALL_PASSED
    compile_errors: list = field(default_factory=list)
    method_results: list = field(default_factory=list)  # list[TestMethodResult]
    raw_output: str = ""


class TestExecutor:
    """Compiles and runs JUnit tests against a Java project."""

    def __init__(self, project_name: str, project_loc: str, test_loc: str):
        self.project_name = project_name
        self.project_loc = project_loc  # e.g. data/project_under_test/Lang/Lang_1_buggy
        self.test_loc = test_loc        # e.g. data/tmp_test (where generated tests are stored)

    # ── compilation ──────────────────────────────────────────────────

    def compile_test(self, test_code: str, test_class_sig: str, test_subdir: str = "") -> tuple[bool, str]:
        """Write test_code to a .java file and compile it against the project.
        Returns (success, error_output).
        """
        # derive file path from signature: e.g. "org.apache.commons.lang3.math.NumberUtilsTest"
        parts = test_class_sig.split(".")
        file_name = parts[-1] + ".java"
        if test_subdir:
            test_dir = os.path.join(self.test_loc, test_subdir)
        else:
            test_dir = os.path.join(self.test_loc, self.project_name)
        os.makedirs(test_dir, exist_ok=True)
        test_file = os.path.join(test_dir, file_name)

        with open(test_file, "w", encoding="utf-8") as f:
            f.write(test_code)

        # attempt Maven compile (if pom.xml exists), fall back to javac
        compile_ok, compile_out = self._maven_compile(test_file, test_class_sig)
        if compile_ok:
            return True, ""

        return False, compile_out

    def _maven_compile(self, test_file: str, test_class_sig: str) -> tuple[bool, str]:
        """Try Maven compile first. Returns (success, output)."""
        pom = os.path.join(self.project_loc, "pom.xml")
        if not os.path.exists(pom):
            return False, "No pom.xml found, Maven compile not available"

        # copy test file into project's test source tree
        src_test = os.path.join(self.project_loc, "src", "test", "java")
        pkg_path = test_class_sig.replace(".", os.sep)
        dest_dir = os.path.join(src_test, os.path.dirname(pkg_path))
        os.makedirs(dest_dir, exist_ok=True)
        file_name = os.path.basename(test_file)
        dest_file = os.path.join(dest_dir, file_name)
        shutil.copy2(test_file, dest_file)

        cmd = ["mvn", "test-compile", "-q", "-DskipTests"]
        try:
            result = subprocess.run(
                cmd, cwd=self.project_loc,
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr + "\n" + result.stdout
        except subprocess.TimeoutExpired:
            return False, "Maven compile timed out"
        except FileNotFoundError:
            return False, "mvn command not found"

    # ── execution ────────────────────────────────────────────────────

    def run_test(self, test_class_sig: str, test_subdir: str = "") -> ExecutionResult:
        """Run a single test class and parse JUnit results.
        Returns ExecutionResult.
        """
        pom = os.path.join(self.project_loc, "pom.xml")
        if not os.path.exists(pom):
            return ExecutionResult(
                status="RUNTIME_ERROR",
                raw_output="No pom.xml found"
            )

        # copy test file into project's test source tree if not already there
        self._ensure_test_in_project(test_class_sig, test_subdir)

        cmd = ["mvn", "test", "-q", f"-Dtest={test_class_sig}"]
        try:
            result = subprocess.run(
                cmd, cwd=self.project_loc,
                capture_output=True, text=True, timeout=120
            )
            output = result.stdout + "\n" + result.stderr
            return self._parse_junit_output(output)
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                status="RUNTIME_ERROR",
                raw_output="Test execution timed out"
            )
        except FileNotFoundError:
            return ExecutionResult(
                status="RUNTIME_ERROR",
                raw_output="mvn command not found"
            )

    def _ensure_test_in_project(self, test_class_sig: str, test_subdir: str = ""):
        """Copy the generated test file into the project's test source tree."""
        parts = test_class_sig.split(".")
        file_name = parts[-1] + ".java"
        if test_subdir:
            test_dir = os.path.join(self.test_loc, test_subdir)
        else:
            test_dir = os.path.join(self.test_loc, self.project_name)
        src_file = os.path.join(test_dir, file_name)
        if not os.path.exists(src_file):
            return

        dest_dir = os.path.join(self.project_loc, "src", "test", "java", os.sep.join(parts[:-1]))
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(src_file, os.path.join(dest_dir, file_name))

    def _parse_junit_output(self, output: str) -> ExecutionResult:
        """Parse Maven/JUnit output to determine test results."""
        method_results: list[TestMethodResult] = []

        # check for overall pass/fail
        if "BUILD SUCCESS" in output:
            # all tests passed
            return ExecutionResult(
                status="ALL_PASSED",
                method_results=method_results,
                raw_output=output
            )

        # look for assertion failures
        # JUnit4 format: junit.framework.AssertionFailedError or java.lang.AssertionError
        assertion_pattern = re.compile(
            r"(?:junit\.framework\.AssertionFailedError|java\.lang\.AssertionError)"
            r"(?::\s*(.*?))?(?:\n|$)",
            re.IGNORECASE
        )
        # JUnit test method failure pattern
        test_fail_pattern = re.compile(
            r"Tests\s+run:\s+\d+,\s+Failures:\s+(\d+)"
        )

        # extract individual test results
        test_result_pattern = re.compile(
            r"^(.+?)\((.+?)\)\s+(Time elapsed:.*?)(.*)$",
            re.MULTILINE
        )

        has_assertion_failure = False
        has_runtime_error = False

        # check for assertion failures
        if assertion_pattern.search(output):
            has_assertion_failure = True

            # extract exception message and stack trace
            exc_match = re.search(
                r"(?:junit\.framework\.AssertionFailedError|java\.lang\.AssertionError)"
                r"(?::\s*(.*?))?(?:\n|$)"
                r"(.*?)(?:Tests run:|BUILD|$)",
                output,
                re.DOTALL | re.IGNORECASE
            )
            exc_message = ""
            exc_stack = ""
            if exc_match:
                exc_message = exc_match.group(1) or ""
                exc_stack = exc_match.group(2) or ""

            # try to extract test method name
            method_name = self._extract_failed_test_method(output)

            method_results.append(TestMethodResult(
                method_name=method_name,
                passed=False,
                exception_type="AssertionError",
                exception_message=exc_message.strip(),
                stack_trace=exc_stack.strip()
            ))

        # check for runtime errors (exceptions other than AssertionError)
        runtime_exc_pattern = re.compile(
            r"(?:java\.lang\.\w+Exception|java\.lang\.\w+Error)"
            r"(?::\s*(.*?))?(?:\n|$)"
        )
        if not has_assertion_failure and runtime_exc_pattern.search(output):
            has_runtime_error = True
            exc_match = re.search(
                r"(java\.lang\.\w+(?:Exception|Error))"
                r"(?::\s*(.*?))?(?:\n|$)"
                r"(.*?)(?:Tests run:|BUILD|$)",
                output,
                re.DOTALL
            )
            if exc_match:
                exc_type = exc_match.group(1)
                exc_message = exc_match.group(2) or ""
                exc_stack = exc_match.group(3) or ""
                method_name = self._extract_failed_test_method(output)

                method_results.append(TestMethodResult(
                    method_name=method_name,
                    passed=False,
                    exception_type=exc_type,
                    exception_message=exc_message.strip(),
                    stack_trace=exc_stack.strip()
                ))

        # if BUILD FAILURE but no specific exception found
        if "BUILD FAILURE" in output and not method_results:
            return ExecutionResult(
                status="RUNTIME_ERROR",
                raw_output=output
            )

        if has_assertion_failure:
            return ExecutionResult(
                status="ASSERTION_FAILURE",
                method_results=method_results,
                raw_output=output
            )
        elif has_runtime_error:
            return ExecutionResult(
                status="RUNTIME_ERROR",
                method_results=method_results,
                raw_output=output
            )
        else:
            return ExecutionResult(
                status="ALL_PASSED",
                method_results=method_results,
                raw_output=output
            )

    def _extract_failed_test_method(self, output: str) -> str:
        """Extract the name of the failed test method from JUnit output."""
        # Pattern: at org.example.MyTest.testMethod(MyTest.java:42)
        match = re.search(r"at\s+([\w.]+)\.(\w+)\([^)]+\.java:\d+\)", output)
        if match:
            return match.group(2)
        return ""

    # ── combined diagnostic flow ─────────────────────────────────────

    def diagnose(self, test_code: str, test_class_sig: str, test_subdir: str = "") -> ExecutionResult:
        """Full pipeline: compile then run. Returns ExecutionResult."""
        compile_ok, compile_err = self.compile_test(test_code, test_class_sig, test_subdir)
        if not compile_ok:
            return ExecutionResult(
                status="COMPILATION_ERROR",
                compile_errors=[compile_err],
                raw_output=compile_err
            )
        return self.run_test(test_class_sig, test_subdir)
