"""Source-free, scaffold-first JUnit generation with per-scenario repair."""

from __future__ import annotations

import json
import os
import re
import glob
from typing import Callable, Optional

from ..basic_class.llm_message import MessageThread
from ..config import CONFIG, logger
from ..contracts import ExecutionRecord, validate_scenario
from ..models import model
from ..privacy import validate_and_record_prompt
from ..utils.public_api import (
    class_skeleton,
    construction_options,
    is_visible_declaration,
    sanitise_field_declaration,
)
from .attribution_voter import AttributionVoter
from .coverage_analyzer import CoverageAnalyzer
from .test_executor import ExecutionResult, TestExecutor


SCAFFOLD_SYSTEM_PROMPT = """You are an expert Java test engineer. Generate only a minimal JUnit test
class scaffold for the supplied public API. It must contain the package, valid imports, class declaration,
and optional shared setup, but no @Test methods and no placeholder assertions. Never request or infer the
target method body. Output one ```java``` block."""

CONSTRUCTOR_TOOL_SYSTEM_PROMPT = """Collect only the public or package-access API information needed to
construct custom input types. Method bodies, private state, source lines, and field initializers are forbidden.
Use the supplied tools, then output [ANALYSIS_COMPLETE]."""

TEST_METHOD_SYSTEM_PROMPT = """Generate one JUnit @Test method for the supplied approved scenario.
Use only public signatures, documentation, construction options, and the scenario's abstract constraints.
Choose concrete ordinary inputs yourself, but never infer a hidden implementation threshold. For a coverage
scenario, use every supplied witness placeholder verbatim; a private local binder will materialize it later.
The assertion must be justified by oracle_basis. Do not use assertTrue(true), reflection, Unsafe, source code,
or a fixed program version.
Mocks are allowed only for publicly injectable dependencies and their behavior must come from public contracts,
never from a hidden branch condition.
If oracle_basis is coverage_probe_only, invoke the target using every witness placeholder but emit no assertion;
the orchestrator will remove this temporary method from the final test suite.

Output exactly:
```java
// IMPORTS
// zero or more complete import statements
// END_IMPORTS

@Test
public void testMethodName() {
    // test body
}
```"""

FIX_METHOD_SYSTEM_PROMPT = """Repair one generated JUnit test method using the structured diagnostic.
Preserve the approved scenario semantics. Do not inspect or request source code. If witness placeholders are
provided, keep them verbatim. Output the same IMPORTS plus one @Test method format."""


CLASS_INDENT = "    "


def normalize_method_code(test_method_code: str) -> list[str]:
    lines = test_method_code.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    non_empty = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    indent = min(non_empty) if non_empty else 0
    return [line[indent:] if line.strip() else "" for line in lines]


def extract_imports_and_method(output: str) -> tuple[list[str], str]:
    fenced = re.search(r"```java\s*([\s\S]*?)\s*```", output, re.IGNORECASE)
    if not fenced:
        fenced = re.search(r"```\s*([\s\S]*?)\s*```", output, re.IGNORECASE)
    code = fenced.group(1) if fenced else output
    import_section = re.search(
        r"//\s*IMPORTS\s*\n([\s\S]*?)//\s*END_IMPORTS",
        code,
        re.IGNORECASE,
    )
    imports = []
    if import_section:
        imports = [
            line.strip()
            for line in import_section.group(1).splitlines()
            if line.strip().startswith("import ") and line.strip().endswith(";")
        ]
        code = code[:import_section.start()] + code[import_section.end():]
    return imports, code.strip()


def merge_imports_into_scaffold(scaffold_code: str, new_imports: list[str]) -> str:
    existing = {line.strip() for line in scaffold_code.splitlines() if line.strip().startswith("import ")}
    additions = [item for item in new_imports if item not in existing]
    if not additions:
        return scaffold_code
    lines = scaffold_code.splitlines()
    insertion = max(
        [index for index, line in enumerate(lines) if line.strip().startswith("import ")],
        default=max([index for index, line in enumerate(lines) if line.strip().startswith("package ")], default=-1),
    )
    lines[insertion + 1:insertion + 1] = additions
    return "\n".join(lines)


def inject_test_method(scaffold_code: str, test_method_code: str) -> str:
    lines = normalize_method_code(test_method_code)
    if not lines:
        return scaffold_code
    block = "\n".join(CLASS_INDENT + line if line.strip() else "" for line in lines)
    closing_brace = scaffold_code.rfind("}")
    if closing_brace < 0:
        return scaffold_code + "\n" + block
    return scaffold_code[:closing_brace] + "\n" + block + "\n\n" + scaffold_code[closing_brace:]


def _method_name(method_code: str, scenario_id: str) -> str:
    match = re.search(r"(?:public\s+)?void\s+(\w+)\s*\(", method_code)
    return match.group(1) if match else f"testScenario_{scenario_id}"


def _normalise_actual_result(result: ExecutionResult) -> str:
    if result.method_results:
        method_result = result.method_results[0]
        return f"{method_result.exception_type or result.status}: {method_result.exception_message or ''}".strip()
    return f"{result.status}: {result.raw_output[-1000:]}".strip()


def _sanitise_diagnostic(text: str) -> str:
    text = re.sub(r"(?:[A-Za-z]:)?[^\s:()]+\.java:\[?\d+(?:,\d+)?\]?", "<test-source>", text)
    text = re.sub(r"\([^()]*\.java:\d+\)", "(<source-location>)", text)
    return text[-3000:]


def validate_generated_test_method(method_code: str, coverage_probe_only: bool = False) -> list[str]:
    issues = []
    if len(re.findall(r"@Test\b", method_code)) != 1:
        issues.append("Exactly one @Test method is required")
    has_assertion = bool(re.search(r"\bassert\w*\s*\(|\bfail\s*\(", method_code))
    if not has_assertion and not coverage_probe_only:
        issues.append("The generated method has no test oracle")
    if coverage_probe_only and has_assertion:
        issues.append("A temporary coverage probe must not contain an assertion")
    forbidden = (
        "java.lang.reflect",
        "getDeclaredField",
        "getDeclaredMethod",
        "setAccessible(",
        "sun.misc.Unsafe",
        "jdk.internal.misc.Unsafe",
    )
    if any(item in method_code for item in forbidden):
        issues.append("Reflection and Unsafe are forbidden")
    if re.search(r"\bassertTrue\s*\(\s*true\s*\)", method_code):
        issues.append("Placeholder assertions are forbidden")
    if re.search(r"\bassertFalse\s*\(\s*false\s*\)", method_code):
        issues.append("Placeholder assertions are forbidden")
    return issues


def remove_test_method(test_class_code: str, method_name: str) -> str:
    declaration = re.search(
        rf"@Test\b(?:(?!@Test\b)[\s\S])*?\bvoid\s+{re.escape(method_name)}\s*\([^)]*\)\s*\{{",
        test_class_code,
    )
    if declaration is None:
        return test_class_code
    brace_start = test_class_code.find("{", declaration.start())
    depth = 0
    quote = None
    escaped = False
    for index in range(brace_start, len(test_class_code)):
        char = test_class_code[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                while end < len(test_class_code) and test_class_code[end] in " \t\r\n":
                    end += 1
                return test_class_code[:declaration.start()] + test_class_code[end:]
    return test_class_code


class TestAgent:
    MAX_SCAFFOLD_RETRIES = 2

    def __init__(
        self,
        project_name: str,
        project_loc: str,
        test_loc: str,
        src_loc: str,
        method_map: dict,
        class_map: dict,
        attribution_voter: Optional[AttributionVoter] = None,
    ):
        self.project_name = project_name
        self.project_loc = project_loc
        self.test_loc = test_loc
        self.src_loc = src_loc
        self.method_map = method_map
        self.class_map = class_map
        self.executor = TestExecutor(project_name, project_loc, test_loc)
        self.coverage_analyzer = CoverageAnalyzer(project_name, project_loc, src_loc, test_loc)
        self.coverage_analyzer.witness_binder.set_class_map(class_map)
        self.attribution_voter = attribution_voter or AttributionVoter()
        self.constructor_info: dict[str, dict] = {}
        self.test_environment: dict = {}
        self.target_method = None
        self.test_class_sig = None
        self.test_class_code = ""
        self.public_context = ""
        self.reviewer_agent = None
        self.coverage_goals: dict[str, dict] = {}
        self.witness_binder = None
        self.coverage_verifier: Optional[Callable[[str, str, str], tuple[bool, dict]]] = None

    def set_reviewer_agent(self, reviewer_agent):
        self.reviewer_agent = reviewer_agent

    def set_public_context(self, context: str) -> None:
        self.public_context = context

    def set_coverage_runtime(self, goals=None, witness_binder=None, verifier=None) -> None:
        self.coverage_goals = goals or {}
        self.witness_binder = witness_binder
        if witness_binder is not None and hasattr(witness_binder, "set_class_map"):
            witness_binder.set_class_map(self.class_map)
        self.coverage_verifier = verifier

    def _call_model(self, messages, **kwargs):
        validate_and_record_prompt(messages, "test", self.target_method)
        selected = model.require_model(model.SELECTED_MODEL, "selected")
        return selected.call(messages, **kwargs)

    def search_constructor(self, class_name: str) -> str:
        class_obj = self.class_map.get(class_name)
        if class_obj is None:
            return json.dumps({"class": class_name, "error": "class_not_found"})
        return json.dumps(construction_options(class_obj), ensure_ascii=False)

    def search_class_skeleton(self, class_name: str) -> str:
        class_obj = self.class_map.get(class_name)
        return class_skeleton(class_obj) if class_obj is not None else "Class not found"

    def search_method_contract(self, class_name: str, method_name: str) -> str:
        class_obj = self.class_map.get(class_name)
        if class_obj is None:
            return "Class not found"
        contracts = [
            {"signature": item.signature, "javadoc": item.javadoc or "No javadoc available."}
            for item in class_obj.methods
            if item.name_no_package == method_name and is_visible_declaration(item.content)
        ]
        return json.dumps(contracts, ensure_ascii=False)

    def search_field_definition(self, class_name: str, field_name: str) -> str:
        class_obj = self.class_map.get(class_name)
        if class_obj is None or field_name not in class_obj.fields:
            return "Field not found"
        statement = class_obj.fields[field_name]
        if not is_visible_declaration(statement):
            return "Field is not part of the visible API"
        return sanitise_field_declaration(statement)

    def discover_test_environment(self) -> dict:
        environment = {
            "build_adapter": "maven" if os.path.isfile(os.path.join(self.project_loc, "pom.xml")) else "defects4j",
            "junit_style": "unknown",
            "test_imports": [],
            "test_method_prefixes": [],
        }
        pom = os.path.join(self.project_loc, "pom.xml")
        if os.path.isfile(pom):
            try:
                with open(pom, "r", encoding="utf-8") as handle:
                    pom_text = handle.read()
                artifacts = re.findall(r"<artifactId>\s*([^<]+)\s*</artifactId>", pom_text)
                environment["test_dependencies"] = sorted({
                    item.strip() for item in artifacts
                    if any(marker in item.lower() for marker in ("junit", "testng", "assertj", "hamcrest"))
                })
            except OSError:
                environment["test_dependencies"] = []

        test_root = self.test_loc if os.path.isabs(self.test_loc) else os.path.join(self.project_loc, self.test_loc)
        imports = set()
        prefixes = set()
        junit_counts = {"junit5": 0, "junit4": 0, "junit3": 0}
        for path in glob.glob(os.path.join(test_root, "**", "*.java"), recursive=True)[:50]:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except OSError:
                continue
            imports.update(re.findall(r"^\s*import\s+([^;]+);", text, re.MULTILINE))
            if "org.junit.jupiter.api.Test" in text:
                junit_counts["junit5"] += 1
            if "org.junit.Test" in text:
                junit_counts["junit4"] += 1
            if "junit.framework.TestCase" in text:
                junit_counts["junit3"] += 1
            for name in re.findall(r"@Test(?:\([^)]*\))?\s+(?:public\s+)?void\s+(\w+)\s*\(", text):
                prefix = re.match(r"[a-z]+", name)
                if prefix:
                    prefixes.add(prefix.group(0))
        if max(junit_counts.values(), default=0) > 0:
            environment["junit_style"] = max(junit_counts, key=junit_counts.get)
        environment["test_imports"] = sorted(imports)[:30]
        environment["test_method_prefixes"] = sorted(prefixes)[:10]
        self.test_environment = environment
        return environment

    def current_test_imports(self) -> list[str]:
        return sorted({
            line.strip()
            for line in self.test_class_code.splitlines()
            if line.strip().startswith("import ") and line.strip().endswith(";")
        })

    def collect_constructor_info(self, target_method, max_turns: int = 8):
        self.target_method = target_method
        custom_types = [
            parameter for parameter in target_method.parameters_list
            if parameter.rsplit(".", 1)[-1] not in {
                "byte", "short", "int", "long", "float", "double", "boolean", "char",
                "Byte", "Short", "Integer", "Long", "Float", "Double", "Boolean", "Character", "String",
            }
        ]
        if not getattr(target_method, "is_static", False):
            receiver_type = target_method.belong_class.name
            if receiver_type not in custom_types:
                custom_types.append(receiver_type)
        if not custom_types:
            return

        thread = MessageThread()
        thread.add_system(CONSTRUCTOR_TOOL_SYSTEM_PROMPT)
        thread.add_user(
            f"Target method: {target_method.signature}\nCustom constructible parameter/receiver types:\n" +
            "\n".join(f"- {item}" for item in custom_types)
        )

        for _ in range(max_turns):
            response = self._call_model(thread.to_msg(), tools=self._constructor_tools())
            if "[ANALYSIS_COMPLETE]" in response.content:
                break
            thread.add_model(response.content, response.tool_calls)
            if not response.tool_calls:
                thread.add_user("Call a public-context tool or output [ANALYSIS_COMPLETE].")
                continue
            for tool_call in response.tool_calls:
                try:
                    args = json.loads(tool_call.function.arguments)
                except (TypeError, json.JSONDecodeError):
                    args = {}
                tool_name = tool_call.function.name
                if tool_name == "search_constructor":
                    result = self.search_constructor(args.get("class_name", ""))
                    try:
                        self.constructor_info[args.get("class_name", "")] = json.loads(result)
                    except json.JSONDecodeError:
                        pass
                elif tool_name == "search_class_skeleton":
                    result = self.search_class_skeleton(args.get("class_name", ""))
                elif tool_name == "search_method_contract":
                    result = self.search_method_contract(
                        args.get("class_name", ""), args.get("method_name", "")
                    )
                elif tool_name == "search_field_definition":
                    result = self.search_field_definition(
                        args.get("class_name", ""), args.get("field_name", "")
                    )
                else:
                    result = "Unknown tool"
                thread.add_tool_result(tool_call.id, result)

    @staticmethod
    def _constructor_tools() -> list[dict]:
        class_name = {
            "type": "string",
            "description": "Fully qualified project class name",
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_constructor",
                    "description": "Get visible constructors, factories, builders, setters, and implementations",
                    "parameters": {"type": "object", "properties": {"class_name": class_name}, "required": ["class_name"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_class_skeleton",
                    "description": "Get a source-free public class skeleton",
                    "parameters": {"type": "object", "properties": {"class_name": class_name}, "required": ["class_name"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_method_contract",
                    "description": "Get visible method signatures and javadocs",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "class_name": class_name,
                            "method_name": {"type": "string"},
                        },
                        "required": ["class_name", "method_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_field_definition",
                    "description": "Get a visible field declaration without its initializer",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "class_name": class_name,
                            "field_name": {"type": "string"},
                        },
                        "required": ["class_name", "field_name"],
                    },
                },
            },
        ]

    def generate_scaffold(self, test_scenarios: dict, import_map: dict) -> tuple[str, str]:
        del test_scenarios
        test_environment = self.discover_test_environment()
        target = self.target_method
        class_name = target.belong_class.name_no_package
        package_name = target.belong_package.name
        generated_class_name = f"{class_name}GeneratedTest"
        test_class_sig = f"{package_name}.{generated_class_name}"
        thread = MessageThread()
        thread.add_system(SCAFFOLD_SYSTEM_PROMPT)
        thread.add_user(
            f"Target class: {class_name}\nPackage: {package_name}\n"
            f"Required test class name: {generated_class_name}\n"
            f"Method signature: {target.signature}\nAvailable imports:\n" +
            ("\n".join(import_map.values()) if import_map else "No project imports") +
            "\nSource-free project test environment:\n" +
            json.dumps(test_environment, ensure_ascii=False, indent=2)
        )

        scaffold = ""
        for _ in range(self.MAX_SCAFFOLD_RETRIES):
            response = self._call_model(thread.to_msg()).content
            scaffold = self._extract_java_code(response)
            if not scaffold:
                thread.add_user("Return one Java code block containing the scaffold.")
                continue
            ok, error = self.executor.compile_test(scaffold, test_class_sig)
            if ok:
                self.test_class_sig = test_class_sig
                self.test_class_code = scaffold
                return test_class_sig, scaffold
            thread.add_user(f"The scaffold did not compile. Diagnostic:\n{_sanitise_diagnostic(error)}")

        self.test_class_sig = test_class_sig
        self.test_class_code = scaffold
        return test_class_sig, ""

    def generate_test_for_scenario(self, scenario: dict, scenario_file: dict) -> dict:
        contract_issues = validate_scenario(scenario)
        record = ExecutionRecord(scenario_id=scenario.get("id", "unknown"))
        if contract_issues or scenario.get("status") != "approved":
            record.diagnostics.extend(contract_issues or ["Scenario is not approved"])
            return record.to_dict()

        previous_code = self.test_class_code
        previous_method = ""
        feedback = ""
        coverage_probe_only = scenario.get("oracle_basis") == "coverage_probe_only"
        max_rounds = int(CONFIG["pipeline"]["test_repair_retries"])
        construction_limit = int(CONFIG["pipeline"]["object_construction_retries"])
        construction_attempts = 0

        for round_index in range(max_rounds):
            output = self._generate_test_method(
                scenario,
                scenario_file,
                previous_method=previous_method,
                feedback=feedback,
            )
            imports, generated_method = extract_imports_and_method(output)
            if not generated_method:
                feedback = "No JUnit method was returned."
                record.diagnostics.append(feedback)
                continue
            method_issues = validate_generated_test_method(
                generated_method,
                coverage_probe_only=coverage_probe_only,
            )
            if method_issues:
                feedback = "; ".join(method_issues)
                record.diagnostics.append(feedback)
                previous_method = generated_method
                continue

            materialized_method = generated_method
            goal_id = scenario.get("coverage_goal_id")
            if goal_id and self.witness_binder is not None:
                try:
                    materialized_method = self.witness_binder.materialize_test_method(goal_id, generated_method)
                except Exception as exc:
                    construction_attempts += 1
                    feedback = f"Witness materialization failed: {exc}"
                    record.diagnostics.append(feedback)
                    previous_method = generated_method
                    if construction_attempts >= construction_limit:
                        record.final_status = self._construction_failure_status(goal_id)
                        self.executor.write_test(previous_code, self.test_class_sig)
                        return record.to_dict()
                    continue

            method_name = _method_name(materialized_method, scenario["id"])
            candidate = merge_imports_into_scaffold(previous_code, imports)
            candidate = inject_test_method(candidate, materialized_method)
            execution = self.executor.diagnose(candidate, self.test_class_sig, method_name)

            record.method_name = method_name
            record.retries = round_index
            record.compile_status = "passed" if execution.status != "COMPILATION_ERROR" else "failed"
            record.run_status = execution.status
            record.failure_kind = None if execution.status == "ALL_PASSED" else execution.status

            if execution.status == "ALL_PASSED":
                target_hit, coverage_delta = self._verify_coverage_goal(goal_id, method_name, candidate)
                record.target_hit = target_hit
                record.coverage_delta = coverage_delta
                if goal_id and not target_hit:
                    construction_attempts += 1
                    feedback = "The test passed but did not hit its CoverageGoal. Change only the construction route or witness placeholders."
                    record.diagnostics.append(feedback)
                    previous_method = generated_method
                    if construction_attempts >= construction_limit:
                        record.final_status = self._construction_failure_status(goal_id)
                        self.executor.write_test(previous_code, self.test_class_sig)
                        return record.to_dict()
                    continue
                self.test_class_code = candidate
                scenario["status"] = "implemented"
                record.final_status = "coverage_probe" if coverage_probe_only else "passed"
                return record.to_dict()

            if execution.status == "ASSERTION_FAILURE":
                target_hit, coverage_delta = self._verify_coverage_goal(goal_id, method_name, candidate)
                record.target_hit = target_hit
                record.coverage_delta = coverage_delta
                if goal_id and not target_hit:
                    construction_attempts += 1
                    feedback = "The assertion failed before the required CoverageGoal was verified. Repair path reachability first."
                    record.diagnostics.append(feedback)
                    previous_method = generated_method
                    if construction_attempts >= construction_limit:
                        record.final_status = self._construction_failure_status(goal_id)
                        self.executor.write_test(previous_code, self.test_class_sig)
                        return record.to_dict()
                    continue

                attribution = self.attribution_voter.attribute(
                    scenario=scenario,
                    test_method=generated_method,
                    actual_result=_normalise_actual_result(execution),
                    method_signature=self.target_method.signature,
                    javadoc=self.target_method.javadoc or "",
                    public_context=self.public_context,
                )
                record.attribution = attribution
                if attribution["decision"] == "source_bug":
                    self.test_class_code = candidate
                    scenario["status"] = "implemented"
                    record.final_status = "source_bug"
                    return record.to_dict()
                if attribution["decision"] == "test_issue":
                    feedback = attribution.get("fix_instructions") or "Repair the test oracle or implementation."
                    record.diagnostics.append(feedback)
                    previous_method = generated_method
                    continue
                record.final_status = "ambiguous"
                record.diagnostics.append(attribution.get("reason", "Attribution was ambiguous"))
                self.executor.write_test(previous_code, self.test_class_sig)
                return record.to_dict()

            feedback = self._repair_feedback(execution)
            record.diagnostics.append(feedback)
            previous_method = generated_method

        record.final_status = "test_invalid"
        self.executor.write_test(previous_code, self.test_class_sig)
        return record.to_dict()

    def _verify_coverage_goal(self, goal_id: Optional[str], method_name: str, candidate: str) -> tuple[Optional[bool], dict]:
        if not goal_id:
            return None, {}
        if self.coverage_verifier is None:
            return False, {"status": "coverage_unverified"}
        try:
            return self.coverage_verifier(goal_id, method_name, candidate)
        except Exception as exc:
            return False, {"status": "coverage_verification_error", "reason": str(exc)}

    def _construction_failure_status(self, goal_id: Optional[str]) -> str:
        if goal_id and self.witness_binder is not None:
            try:
                if any(
                    slot.get("role") == "custom_object_leaf"
                    for slot in self.witness_binder.public_slots(goal_id)
                ):
                    return "uncontrollable_object_state"
            except KeyError:
                pass
        return "unconstructable_input"

    def _generate_test_method(
        self,
        scenario: dict,
        scenario_file: dict,
        previous_method: str = "",
        feedback: str = "",
    ) -> str:
        constructor_context = json.dumps(self.constructor_info, ensure_ascii=False, indent=2)
        goal = self.coverage_goals.get(scenario.get("coverage_goal_id", ""))
        witness_slots = []
        if goal and self.witness_binder is not None:
            witness_slots = self.witness_binder.public_slots(goal["goal_id"])

        thread = MessageThread()
        thread.add_system(FIX_METHOD_SYSTEM_PROMPT if previous_method else TEST_METHOD_SYSTEM_PROMPT)
        payload = {
            "method_signature": self.target_method.signature,
            "method_intent": scenario_file.get("method_intent", ""),
            "scenario": scenario,
            "public_constructor_context": self.constructor_info,
            "test_environment": self.test_environment,
            "current_test_imports": self.current_test_imports(),
            "coverage_goal": goal,
            "witness_placeholders": witness_slots,
        }
        message = json.dumps(payload, ensure_ascii=False, indent=2)
        if previous_method:
            message += f"\n\nPrevious generated method:\n```java\n{previous_method}\n```\n\nDiagnostic:\n{feedback[-3000:]}"
        thread.add_user(message)
        return self._call_model(thread.to_msg()).content

    @staticmethod
    def _repair_feedback(execution: ExecutionResult) -> str:
        if execution.status == "COMPILATION_ERROR":
            return "Compilation failed:\n" + _sanitise_diagnostic("\n".join(execution.compile_errors[:10]))
        if execution.status == "RUNTIME_ERROR":
            return "Test setup or invocation failed before a valid assertion result:\n" + _normalise_actual_result(execution)
        if execution.status == "TIMEOUT":
            return "The isolated test timed out; simplify setup and avoid unbounded operations."
        return f"Execution was not successful ({execution.status}):\n{_sanitise_diagnostic(execution.raw_output)}"

    @staticmethod
    def _scenario_to_method_name(scenario: dict) -> str:
        words = re.sub(r"[^A-Za-z0-9\s]", "", scenario.get("description", "test")).split()
        return "test" + "".join(word[:1].upper() + word[1:] for word in words) if words else "testScenario"

    @staticmethod
    def _extract_java_code(response: str) -> str:
        match = re.search(r"```java\s*([\s\S]*?)\s*```", response, re.IGNORECASE)
        if not match:
            match = re.search(r"```\s*([\s\S]*?)\s*```", response, re.IGNORECASE)
        return match.group(1).strip() if match else ""
