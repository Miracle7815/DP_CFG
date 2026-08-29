"""Deterministic, method-isolated Java test compilation and execution."""

from __future__ import annotations

import os
import re
import subprocess
import time
import glob
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..config import CONFIG


@dataclass
class CommandResult:
    returncode: Optional[int]
    output: str
    timed_out: bool = False
    command_missing: bool = False


@dataclass
class TestMethodResult:
    method_name: str
    passed: bool
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    stack_trace: Optional[str] = None


@dataclass
class ExecutionResult:
    status: str
    compile_errors: list[str] = field(default_factory=list)
    method_results: list[TestMethodResult] = field(default_factory=list)
    raw_output: str = ""
    returncode: Optional[int] = None
    failure_phase: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class BuildAdapter:
    def __init__(self, project_loc: str, timeout: int):
        self.project_loc = project_loc
        self.timeout = timeout

    def compile(self) -> CommandResult:
        raise NotImplementedError

    def test(self, test_class_sig: str, method_name: Optional[str]) -> CommandResult:
        raise NotImplementedError

    def _run(self, command: list[str], timeout: Optional[int] = None) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                cwd=self.project_loc,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
            return CommandResult(result.returncode, result.stdout + "\n" + result.stderr)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
            return CommandResult(None, stdout + "\n" + stderr, timed_out=True)
        except FileNotFoundError:
            return CommandResult(None, f"Command not found: {command[0]}", command_missing=True)


class MavenBuildAdapter(BuildAdapter):
    def compile(self) -> CommandResult:
        return self._run(["mvn", "-q", "-DskipTests", "test-compile"])

    def test(self, test_class_sig: str, method_name: Optional[str]) -> CommandResult:
        selector = test_class_sig if not method_name else f"{test_class_sig}#{method_name}"
        return self._run(["mvn", "-q", "test", f"-Dtest={selector}"])


class Defects4JBuildAdapter(BuildAdapter):
    def compile(self) -> CommandResult:
        return self._run(["defects4j", "compile"])

    def test(self, test_class_sig: str, method_name: Optional[str]) -> CommandResult:
        selector = test_class_sig if not method_name else f"{test_class_sig}::{method_name}"
        return self._run(["defects4j", "test", "-t", selector])


def select_build_adapter(project_loc: str, timeout: int) -> BuildAdapter:
    if os.path.isfile(os.path.join(project_loc, "pom.xml")):
        return MavenBuildAdapter(project_loc, timeout)
    return Defects4JBuildAdapter(project_loc, timeout)


class TestExecutor:
    def __init__(self, project_name: str, project_loc: str, test_loc: str):
        self.project_name = project_name
        self.project_loc = os.path.abspath(project_loc)
        self.test_loc = test_loc
        timeout = int(CONFIG["pipeline"]["test_timeout_seconds"])
        self.adapter = select_build_adapter(self.project_loc, timeout)

    def test_file_path(self, test_class_sig: str) -> str:
        package_parts = test_class_sig.split(".")
        relative = os.path.join(*package_parts[:-1], package_parts[-1] + ".java")
        root = self.test_loc if os.path.isabs(self.test_loc) else os.path.join(self.project_loc, self.test_loc)
        return os.path.join(root, relative)

    def write_test(self, test_code: str, test_class_sig: str) -> str:
        test_file = self.test_file_path(test_class_sig)
        os.makedirs(os.path.dirname(test_file), exist_ok=True)
        with open(test_file, "w", encoding="utf-8") as handle:
            handle.write(test_code)
        return test_file

    def compile_test(self, test_code: str, test_class_sig: str, test_subdir: str = "") -> tuple[bool, str]:
        del test_subdir
        self.write_test(test_code, test_class_sig)
        result = self.adapter.compile()
        if result.returncode == 0 and not result.timed_out:
            return True, ""
        if result.timed_out:
            return False, "Test compilation timed out\n" + result.output
        return False, result.output

    def run_test(
        self,
        test_class_sig: str,
        method_name: Optional[str] = None,
        test_subdir: str = "",
    ) -> ExecutionResult:
        del test_subdir
        started_at = time.time()
        result = self.adapter.test(test_class_sig, method_name)
        if result.timed_out:
            return ExecutionResult(
                status="TIMEOUT",
                raw_output=result.output or "Test execution timed out",
                returncode=result.returncode,
                failure_phase="execution",
            )
        if result.command_missing:
            return ExecutionResult(
                status="UNKNOWN_ERROR",
                raw_output=result.output,
                returncode=result.returncode,
                failure_phase="execution",
            )
        evidence = self._has_test_evidence(
            result.output,
            test_class_sig,
            method_name,
            started_at,
        )
        return self._parse_junit_output(
            result.output,
            result.returncode,
            method_name or "",
            has_test_evidence=evidence,
        )

    def _parse_junit_output(
        self,
        output: str,
        returncode: Optional[int],
        method_name: str = "",
        has_test_evidence: Optional[bool] = None,
    ) -> ExecutionResult:
        if returncode == 0:
            if has_test_evidence is None:
                has_test_evidence = self._output_has_test_evidence(output)
            if not has_test_evidence:
                return ExecutionResult(
                    status="UNKNOWN_ERROR",
                    raw_output=output or "Command exited successfully but produced no test result",
                    returncode=returncode,
                    failure_phase="execution",
                )
            return ExecutionResult(
                status="ALL_PASSED",
                method_results=[TestMethodResult(method_name=method_name, passed=True)] if method_name else [],
                raw_output=output,
                returncode=returncode,
            )

        if returncode is None:
            return ExecutionResult(
                status="UNKNOWN_ERROR",
                raw_output=output,
                returncode=returncode,
                failure_phase="execution",
            )

        if self._looks_like_compilation_error(output):
            return ExecutionResult(
                status="COMPILATION_ERROR",
                compile_errors=self._extract_compilation_errors(output),
                raw_output=output,
                returncode=returncode,
                failure_phase="compilation",
            )

        assertion_match = re.search(
            r"((?:org\.opentest4j\.)?AssertionFailedError|junit\.framework\.AssertionFailedError|java\.lang\.AssertionError)"
            r"(?::\s*([^\r\n]*))?",
            output,
            re.IGNORECASE,
        )
        if assertion_match:
            failed_method = self._extract_failed_test_method(output) or method_name
            method_result = TestMethodResult(
                method_name=failed_method,
                passed=False,
                exception_type=assertion_match.group(1),
                exception_message=(assertion_match.group(2) or "").strip(),
                stack_trace=self._extract_stack_trace(output),
            )
            return ExecutionResult(
                status="ASSERTION_FAILURE",
                method_results=[method_result],
                raw_output=output,
                returncode=returncode,
                failure_phase="assertion",
            )

        runtime_match = re.search(
            r"((?:[A-Za-z_$][\w$]*\.)+(?:\w+Exception|\w+Error))(?::\s*([^\r\n]*))?",
            output,
        )
        if runtime_match:
            failed_method = self._extract_failed_test_method(output) or method_name
            return ExecutionResult(
                status="RUNTIME_ERROR",
                method_results=[TestMethodResult(
                    method_name=failed_method,
                    passed=False,
                    exception_type=runtime_match.group(1),
                    exception_message=(runtime_match.group(2) or "").strip(),
                    stack_trace=self._extract_stack_trace(output),
                )],
                raw_output=output,
                returncode=returncode,
                failure_phase="execution",
            )

        return ExecutionResult(
            status="UNKNOWN_ERROR",
            raw_output=output,
            returncode=returncode,
            failure_phase="execution",
        )

    @staticmethod
    def _output_has_test_evidence(output: str) -> bool:
        return bool(re.search(
            r"(?:Tests run:\s*\d+|Failing tests:\s*\d+|test result:\s*(?:ok|FAILED))",
            output,
            re.IGNORECASE,
        ))

    def _has_test_evidence(
        self,
        output: str,
        test_class_sig: str,
        method_name: Optional[str],
        started_at: float,
    ) -> bool:
        if self._output_has_test_evidence(output):
            return True
        report_pattern = os.path.join(self.project_loc, "**", "surefire-reports", "TEST-*.xml")
        for report in glob.glob(report_pattern, recursive=True):
            try:
                if os.path.getmtime(report) + 1 < started_at:
                    continue
                root = ET.parse(report).getroot()
            except (OSError, ET.ParseError):
                continue
            testcases = root.findall(".//testcase")
            for testcase in testcases:
                class_name = testcase.get("classname", "")
                name = testcase.get("name", "")
                if class_name != test_class_sig:
                    continue
                if method_name is None or name == method_name or name.startswith(method_name + "["):
                    return True
        return False

    @staticmethod
    def _looks_like_compilation_error(output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in (
            "compilation failure",
            "compilation error",
            "cannot find symbol",
            "incompatible types",
            "compiler.err.",
        ))

    @staticmethod
    def _extract_compilation_errors(output: str) -> list[str]:
        lines = []
        for line in output.splitlines():
            lowered = line.lower()
            if "error" in lowered or "cannot find symbol" in lowered or "incompatible types" in lowered:
                lines.append(line.strip())
        return lines[:20] or [output[-2000:]]

    @staticmethod
    def _extract_failed_test_method(output: str) -> str:
        patterns = (
            r"at\s+[\w.$]+\.(\w+)\([^)]+\.java:\d+\)",
            r"(?:test)?([A-Za-z_$][\w$]*)\([^)]*\)\s+Time elapsed",
        )
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_stack_trace(output: str) -> str:
        stack_lines = [line for line in output.splitlines() if line.lstrip().startswith("at ")]
        return "\n".join(stack_lines[:30])

    def diagnose(
        self,
        test_code: str,
        test_class_sig: str,
        method_name: Optional[str] = None,
        test_subdir: str = "",
    ) -> ExecutionResult:
        compile_ok, compile_error = self.compile_test(test_code, test_class_sig, test_subdir)
        if not compile_ok:
            return ExecutionResult(
                status="COMPILATION_ERROR",
                compile_errors=self._extract_compilation_errors(compile_error),
                raw_output=compile_error,
                failure_phase="compilation",
            )
        return self.run_test(test_class_sig, method_name, test_subdir)
