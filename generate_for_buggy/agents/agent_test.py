"""
TestAgent - Generates JUnit test scaffolds, collects constructor info via tools,
generates per-scenario test methods with proper import merging and indentation,
and integrates with ReviewerAgent for error diagnosis.
"""
import json
import os
import re

from ..config import logger
from ..basic_class.llm_message import MessageThread
from ..models import model

from .test_executor import TestExecutor
from .coverage_analyzer import CoverageAnalyzer


# ── Prompt Templates ──────────────────────────────────────────────────

SCAFFOLD_SYSTEM_PROMPT = """You are an expert Java test engineer. Your task is to generate a JUnit test class SKELETON that compiles successfully.

You will be given:
- Target method signature
- Target class name
- Available imports from the project
- Parameter types of the target method

Generate a Java test class with:
1. Correct package declaration (same package as the target class)
2. Only imports that exist in the project (use the provided import map)
3. Test class name: {ClassName}Test
4. @Before method for common test setup (if needed)
5. Empty @Test method stubs for each scenario (method name + comment, body is assertTrue(true))
6. @After method (if cleanup is needed)

IMPORTANT:
- The skeleton MUST compile. Do not use classes or methods that don't exist.
- Do NOT implement the test logic yet -- just create the structure.
- Use JUnit 4 annotations (@Test, @Before, @After).
- Each @Test stub should have a descriptive name like testScenario_NormalInput and contain only: // Scenario: <description>\n    assertTrue(true);

Output the complete Java code enclosed in ```java ... ```"""

CONSTRUCTOR_TOOL_SYSTEM_PROMPT = """You are gathering constructor information to construct test inputs.

Available tools:
- `search_constructor(class_name)`: Get all constructor signatures for a class
- `search_class_skeleton(class_name)`: Get class structure (fields + method signatures)
- `search_method_source(class_name, method_name)`: Get source code of a called method
- `search_field_definition(class_name, field_name)`: Get field definition

Process:
1. For each parameter type of the target method that is NOT a primitive or java.lang type, gather constructor/type information
2. Call tools to understand how to construct instances of those types
3. When you have sufficient context, output [ANALYSIS_COMPLETE]

Output format for tool calls:
```json
{
    "tool_calls": [
        {"tool_name": "search_constructor", "args": {"class_name": "..."}}
    ]
}
```

When analysis is complete, output only: [ANALYSIS_COMPLETE]"""

TEST_METHOD_SYSTEM_PROMPT = """You are an expert Java test engineer. Generate a single JUnit @Test method for a specific test scenario.

You will be given:
- The test scenario (id, type, description, input, expected_behavior)
- The target method signature
- Constructor/type information for building test inputs
- The test class so far (with other test methods)

Generate ONE @Test method that:
1. Constructs the input as described in the scenario
2. Calls the target method with the constructed input
3. Asserts the expected behavior using Assert.assertEquals, Assert.assertTrue, etc.
4. Has a clear method name describing what is tested

Rules:
- Use only classes and methods available in the test class's imports
- Handle necessary setup (object instantiation, etc.)
- The method must be self-contained and independent

OUTPUT FORMAT (strictly follow this structure):
```java
// IMPORTS
import com.example.SomeClass;
import java.util.List;
// END_IMPORTS

@Test
public void testMethodName() {
    // test body
}
```

The IMPORTS section should ONLY contain imports that are NOT already in the current test class.
If no new imports are needed, leave the IMPORTS section empty."""

FIX_METHOD_SYSTEM_PROMPT = """Your test method failed to compile or run. Fix it based on the feedback.

Previous method code:
```java
{previous_method_code}
```

Failure details:
{failure_details}

OUTPUT FORMAT (strictly follow this structure):
```java
// IMPORTS
{new or updated imports, empty if none}
// END_IMPORTS

@Test
public void testMethodName() {
    // corrected test body
}
```"""

# ── Utility: Code Indentation Handling ────────────────────────────────

CLASS_INDENT = "    "       # 4 spaces: one level inside class body
METHOD_INDENT = "        "  # 8 spaces: two levels inside class body + method body


def normalize_method_code(test_method_code: str) -> list[str]:
    """Strip markdown fences, remove common leading indentation,
    and return cleaned lines."""
    lines = test_method_code.strip().split("\n")

    # Remove ```java / ``` fences if present
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    # Remove empty leading/trailing lines
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()

    if not lines:
        return []

    # Detect and strip common leading indent
    min_indent = None
    for line in lines:
        if line.strip() == "":
            continue
        leading = len(line) - len(line.lstrip())
        if min_indent is None or leading < min_indent:
            min_indent = leading

    if min_indent and min_indent > 0:
        lines = [line[min_indent:] if len(line) >= min_indent else line.lstrip() for line in lines]

    return lines


def extract_imports_and_method(test_method_output: str) -> tuple[list[str], str]:
    """Parse LLM output that contains both IMPORTS section and @Test method code.

    Returns:
        (new_imports, method_code_without_imports)
    """
    # Extract the java code block first
    pattern = re.compile(r"```java\s*([\s\S]*?)\s*```", re.IGNORECASE)
    match = pattern.search(test_method_output)
    if not match:
        # Fallback: try any code block
        pattern2 = re.compile(r"```\s*([\s\S]*?)\s*```", re.IGNORECASE)
        match2 = pattern2.search(test_method_output)
        if match2:
            code_block = match2.group(1)
        else:
            return [], test_method_output
    else:
        code_block = match.group(1)

    # Extract imports section
    import_pattern = re.compile(
        r"//\s*IMPORTS\s*\n([\s\S]*?)//\s*END_IMPORTS",
        re.IGNORECASE
    )
    import_match = import_pattern.search(code_block)

    new_imports = []
    if import_match:
        import_section = import_match.group(1).strip()
        if import_section:
            for line in import_section.split("\n"):
                line = line.strip()
                if line.startswith("import ") and line.endswith(";"):
                    new_imports.append(line)
        # Remove the imports section from the code block
        code_block = code_block[:import_match.start()] + code_block[import_match.end():]

    return new_imports, code_block


def merge_imports_into_scaffold(scaffold_code: str, new_imports: list[str]) -> str:
    """Merge new imports into the scaffold's import section.

    Only adds imports that don't already exist in the scaffold.
    Inserts new imports after the last existing import line.
    """
    if not new_imports:
        return scaffold_code

    # Collect existing imports
    existing_imports = set()
    for line in scaffold_code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("import "):
            existing_imports.add(stripped)

    # Filter out duplicates
    unique_new = [imp for imp in new_imports if imp not in existing_imports]
    if not unique_new:
        return scaffold_code

    # Find the last import line in the scaffold
    lines = scaffold_code.split("\n")
    last_import_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("import "):
            last_import_idx = i

    if last_import_idx >= 0:
        # Insert after the last import
        import_lines = "\n".join(unique_new)
        lines.insert(last_import_idx + 1, import_lines)
    else:
        # No existing imports found, add after package declaration
        pkg_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("package "):
                pkg_idx = i
        import_lines = "\n".join(unique_new)
        if pkg_idx >= 0:
            lines.insert(pkg_idx + 1, import_lines)
        else:
            # Prepend at the beginning
            lines.insert(0, import_lines)

    return "\n".join(lines)


def inject_test_method(scaffold_code: str, test_method_code: str) -> str:
    """Inject a generated test method into the scaffold before the closing '}'.

    Handles indentation: normalizes the generated code and adds CLASS_INDENT (4 spaces)
    to each line so it sits correctly inside the class body.

    The generated test method from LLM typically looks like:
        @Test
        public void methodName() {
            ...body...
        }

    After injection, each line gets 4 spaces prepended:
            @Test
            public void methodName() {
                ...body... (extra 4 spaces)
            }
    """
    lines = normalize_method_code(test_method_code)
    if not lines:
        return scaffold_code

    # Add CLASS_INDENT to every line
    indented_lines = []
    for line in lines:
        if line.strip() == "":
            indented_lines.append("")
        else:
            indented_lines.append(CLASS_INDENT + line)

    method_block = "\n".join(indented_lines)

    # Find the last closing brace of the class
    idx = scaffold_code.rfind("}")
    if idx == -1:
        return scaffold_code + "\n\n" + method_block + "\n"

    return scaffold_code[:idx] + "\n" + method_block + "\n\n" + scaffold_code[idx:]


def extract_last_method_name(scaffold_code: str, previous_code: str) -> str:
    """Extract the method name of the last @Test method added to scaffold_code
    that was not in previous_code."""
    # Find the @Test method that was added
    diff = scaffold_code[len(previous_code):] if len(scaffold_code) > len(previous_code) else ""
    if not diff:
        return ""

    # Look for @Test followed by method signature in the diff area
    match = re.search(r"@Test\s*\n\s*public\s+\w+\s+(\w+)\s*\(", diff)
    if match:
        return match.group(1)

    # Fallback: find the last method name in the full code
    matches = re.findall(r"@Test\s*\n\s*public\s+\w+\s+(\w+)\s*\(", scaffold_code)
    return matches[-1] if matches else ""


# ── TestAgent Class ───────────────────────────────────────────────────

class TestAgent:
    """Generates JUnit tests for a target method with scaffold-first,
    per-scenario generation, and coverage-driven iteration.

    Integrates with ReviewerAgent for error diagnosis (methods to be wired up).
    """

    MAX_SCAFFOLD_RETRIES = 2
    MAX_METHOD_ROUNDS = 3
    MAX_COVERAGE_ITERATION = 3

    def __init__(self, project_name: str, project_loc: str, test_loc: str,
                 src_loc: str, method_map: dict, class_map: dict):
        self.project_name = project_name
        self.project_loc = project_loc
        self.test_loc = test_loc
        self.src_loc = src_loc
        self.method_map = method_map
        self.class_map = class_map

        self.executor = TestExecutor(project_name, project_loc, test_loc)
        self.coverage_analyzer = CoverageAnalyzer(project_name, project_loc, src_loc)

        # Collected constructor/type info
        self.constructor_info = {}

        self.target_method = None
        self.test_class_sig = None
        self.test_class_code = ""
        self.scenario_results = {}  # {scenario_id: {"status": ..., "method_name": ..., "exec_result": ...}}

        # ReviewerAgent reference (set externally)
        self.reviewer_agent = None

    def set_reviewer_agent(self, reviewer_agent):
        """Set the ReviewerAgent reference for error diagnosis."""
        self.reviewer_agent = reviewer_agent

    # ── Tool Implementations ──────────────────────────────────────────

    def search_constructor(self, class_name: str) -> str:
        """Get constructor signatures for a class."""
        for cn, cls in self.class_map.items():
            if cn == class_name:
                constructors = cls.constructor if hasattr(cls, 'constructor') else []
                if constructors:
                    return "\n".join(f"  {c.signature}" for c in constructors)
                return f"  No constructors found for {class_name} (default constructor available)"
        return f"  Class {class_name} not found in class_map"

    def search_class_skeleton(self, class_name: str) -> str:
        """Get class structure: fields + method signatures."""
        for cn, cls in self.class_map.items():
            if cn == class_name:
                skeleton = f"{cls.signature}\n"
                skeleton += "  Fields:\n"
                for field_name, stmt in cls.fields.items():
                    skeleton += f"    {stmt}\n"
                constructors = cls.constructor if hasattr(cls, 'constructor') else []
                if constructors:
                    skeleton += "  Constructors:\n"
                    for c in constructors:
                        skeleton += f"    {c.signature}\n"
                skeleton += "  Methods:\n"
                for m in cls.methods:
                    skeleton += f"    {m.signature}\n"
                return skeleton
        return f"  Class {class_name} not found"

    def search_method_source(self, class_name: str, method_name: str) -> str:
        """Get source code of a called method."""
        for cn, cls in self.class_map.items():
            if cn == class_name:
                for method in cls.methods:
                    if method.name_no_package == method_name:
                        return f"{method.signature}\n{method.content}"
                return f"  Method {method_name} not found in {class_name}"
        return f"  Class {class_name} not found"

    def search_field_definition(self, class_name: str, field_name: str) -> str:
        """Get field definition."""
        for cn, cls in self.class_map.items():
            if cn == class_name:
                for fname, stmt in cls.fields.items():
                    if fname == field_name:
                        return stmt
                return f"  Field {field_name} not found in {class_name}"
        return f"  Class {class_name} not found"

    # ── Constructor Info Collection (ReAct) ───────────────────────────

    def collect_constructor_info(self, target_method, max_turns: int = 8):
        """ReAct loop to collect constructor/type info for non-primitive parameter types."""
        self.target_method = target_method
        self.constructor_info = {}

        # Determine which parameter types need constructor info
        non_primitive_types = []
        for param in target_method.parameters_list:
            type_name = param.split(".")[-1]
            if type_name in ("byte", "short", "int", "long", "float", "double", "boolean", "char",
                             "Byte", "Short", "Integer", "Long", "Float", "Double", "Boolean",
                             "Character", "String"):
                continue
            non_primitive_types.append(param)

        if not non_primitive_types:
            logger.info("No non-primitive parameters, skipping constructor info collection")
            return

        thread = MessageThread()
        param_desc = "\n".join(f"  - {p}" for p in non_primitive_types)
        thread.add_system(CONSTRUCTOR_TOOL_SYSTEM_PROMPT)
        thread.add_user(f"Target method: {target_method.signature}\n\n"
                        f"Parameter types that need constructor info:\n{param_desc}")

        logger.info(f"Collecting constructor info for {len(non_primitive_types)} types")

        for turn in range(max_turns):
            response, tool_calls, *_ = model.SELECTED_MODEL.call(thread.to_msg(), self._constructor_tools())

            if "[ANALYSIS_COMPLETE]" in response:
                break

            # Execute tool calls
            if tool_calls:
                for tc in tool_calls:
                    tool_name = tc.function.name
                    args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments

                    if tool_name == "search_constructor":
                        result = self.search_constructor(args.get("class_name", ""))
                        self.constructor_info[args.get("class_name", "")] = result
                        thread.add_tool_result(tc.id, result)
                    elif tool_name == "search_class_skeleton":
                        result = self.search_class_skeleton(args.get("class_name", ""))
                        thread.add_tool_result(tc.id, result)
                    elif tool_name == "search_method_source":
                        result = self.search_method_source(
                            args.get("class_name", ""), args.get("method_name", "")
                        )
                        thread.add_tool_result(tc.id, result)
                    elif tool_name == "search_field_definition":
                        result = self.search_field_definition(
                            args.get("class_name", ""), args.get("field_name", "")
                        )
                        thread.add_tool_result(tc.id, result)
            else:
                thread.add_user("If you need more information, call the tools. Otherwise output [ANALYSIS_COMPLETE]")

    def _constructor_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_constructor",
                    "description": "Get all constructor signatures for a class",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "class_name": {"type": "string", "description": "Full class name (e.g., org.apache.commons.lang3.math.NumberUtils)"}
                        },
                        "required": ["class_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_class_skeleton",
                    "description": "Get class structure (fields + method signatures)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "class_name": {"type": "string", "description": "Full class name"}
                        },
                        "required": ["class_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_method_source",
                    "description": "Get source code of a called method",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "class_name": {"type": "string", "description": "Full class name"},
                            "method_name": {"type": "string", "description": "Method name"}
                        },
                        "required": ["class_name", "method_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_field_definition",
                    "description": "Get field definition",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "class_name": {"type": "string", "description": "Full class name"},
                            "field_name": {"type": "string", "description": "Field name"}
                        },
                        "required": ["class_name", "field_name"]
                    }
                }
            },
        ]

    # ── Scaffold Generation ───────────────────────────────────────────

    def generate_scaffold(self, test_scenarios: dict, import_map: dict) -> tuple[str, str]:
        """Generate test class scaffold. Returns (test_class_sig, scaffold_code)."""
        target = self.target_method
        class_name = target.belong_class.name_no_package
        package_name = target.belong_package.name

        # Build scenario stub descriptions
        scenario_stubs = []
        for s in test_scenarios.get("test_scenarios", []):
            scenario_stubs.append({
                "id": s["id"],
                "type": s["category"],
                "description": s["description"],
                "method_name": self._scenario_to_method_name(s),
            })

        prompt = SCAFFOLD_SYSTEM_PROMPT.format(ClassName=class_name)

        import_list = "\n".join(f"  {v}" for v in import_map.values()) if import_map else "  No specific imports"

        user_msg = (
            f"Target class: {class_name}\n"
            f"Package: {package_name}\n"
            f"Method signature: {target.signature}\n"
            f"Available imports:\n{import_list}\n\n"
            f"Test scenarios to create stubs for:\n"
        )
        for stub in scenario_stubs:
            user_msg += (
                f"  - {stub['id']} ({stub['type']}): {stub['description']}\n"
                f"    Method name: {stub['method_name']}\n"
            )

        thread = MessageThread()
        thread.add_system(prompt)
        thread.add_user(user_msg)

        scaffold = None
        for attempt in range(self.MAX_SCAFFOLD_RETRIES):
            response, *_ = model.SELECTED_MODEL.call(thread.to_msg())
            logger.info(f"Scaffold generation attempt {attempt + 1}")

            # Extract Java code
            scaffold = self._extract_java_code(response)
            if not scaffold:
                thread.add_user("The code block was not found. Please output the Java code enclosed in ```java ... ```")
                continue

            # Compile check
            test_class_sig = f"{package_name}.{class_name}Test"
            ok, err = self.executor.compile_test(scaffold, test_class_sig)
            if ok:
                self.test_class_sig = test_class_sig
                self.test_class_code = scaffold
                logger.info(f"Scaffold compiled successfully: {test_class_sig}")
                return test_class_sig, scaffold

            thread.add_user(f"Compilation failed:\n{err}\n\nPlease fix and regenerate the scaffold.")

        # If all retries failed, return what we have
        self.test_class_sig = f"{package_name}.{class_name}Test"
        self.test_class_code = scaffold if scaffold else ""
        return self.test_class_sig, self.test_class_code

    # ── Per-Scenario Test Generation ──────────────────────────────────

    def generate_test_for_scenario(self, scenario: dict, test_scenarios: dict) -> dict:
        """Generate a single @Test method for a scenario and inject it.

        Returns a result dict:
        {
            "scenario_id": str,
            "method_name": str,
            "status": "passed" | "failed" | "bug_detected" | "test_issue" | "needs_regeneration",
            "exec_result": ExecutionResult | None,   # Only for the newly added method
            "reviewer_diagnosis": dict | None,       # From ReviewerAgent (when implemented)
        }

        Note: Error diagnosis is delegated to ReviewerAgent.
        The actual diagnosis logic will be wired up when ReviewerAgent is implemented.
        """
        result = {
            "scenario_id": scenario["id"],
            "method_name": "",
            "status": "failed",
            "exec_result": None,
            "reviewer_diagnosis": None,
        }

        previous_code = self.test_class_code

        for round_idx in range(self.MAX_METHOD_ROUNDS):
            # Generate test method (includes imports + code)
            test_method_output = self._generate_test_method(scenario, test_scenarios)
            if not test_method_output:
                result["status"] = "failed"
                return result

            # Parse: extract new imports and method code
            new_imports, method_code = extract_imports_and_method(test_method_output)
            if not method_code.strip():
                continue

            # Extract method name
            method_name_match = re.search(r"public\s+\w+\s+(\w+)\s*\(", method_code)
            method_name = method_name_match.group(1) if method_name_match else f"testScenario_{scenario['id']}"
            result["method_name"] = method_name

            # Merge new imports into scaffold
            self.test_class_code = merge_imports_into_scaffold(self.test_class_code, new_imports)

            # Inject test method (handles indentation)
            self.test_class_code = inject_test_method(self.test_class_code, method_code)

            # Compile
            ok, compile_err = self.executor.compile_test(self.test_class_code, self.test_class_sig)
            if not ok:
                logger.info(f"Scenario {scenario['id']} round {round_idx + 1}: compilation failed")
                # Rollback: remove the last added method and imports
                self.test_class_code = previous_code

                # TODO: Wire up ReviewerAgent for compilation error diagnosis
                # When ReviewerAgent is ready:
                # diagnosis = self.reviewer_agent.review_test_failure(
                #     scenario=scenario,
                #     test_code=method_code,
                #     failure_type="COMPILATION_ERROR",
                #     failure_details=compile_err,
                # )
                # Then act on diagnosis: fix / regenerate / skip
                return result

            # Run tests
            full_exec_result = self.executor.run_test(self.test_class_sig)

            # Extract only the newly added method's execution result
            method_result = self._extract_method_result(full_exec_result, method_name)
            result["exec_result"] = method_result

            if full_exec_result.status == "ALL_PASSED":
                logger.info(f"Scenario {scenario['id']} ({method_name}) passed on round {round_idx + 1}")
                result["status"] = "passed"
                return result

            # Test failed - delegate to ReviewerAgent for diagnosis
            logger.info(f"Scenario {scenario['id']} ({method_name}) round {round_idx + 1}: {full_exec_result.status}")

            if self.reviewer_agent is not None:
                # TODO: Wire up ReviewerAgent diagnosis flow
                # diagnosis = self.reviewer_agent.review_test_failure(
                #     scenario=scenario,
                #     test_code=method_code,
                #     failure_type=full_exec_result.status,
                #     failure_details=method_result,
                # )
                #
                # if diagnosis["diagnosis"] == "bug_detected":
                #     result["status"] = "bug_detected"
                #     result["reviewer_diagnosis"] = diagnosis
                #     return result
                # elif diagnosis["diagnosis"] == "test_issue":
                #     # Fix test method based on reviewer's instructions
                #     continue
                # elif diagnosis["diagnosis"] == "needs_regeneration":
                #     # Regenerate with different approach
                #     continue
                pass

            # Without ReviewerAgent, rollback and stop
            self.test_class_code = previous_code
            return result

        return result

    def _generate_test_method(self, scenario: dict, test_scenarios: dict) -> str:
        """LLM call to generate a single @Test method (with imports)."""
        constructor_info_str = ""
        for cls_name, info in self.constructor_info.items():
            constructor_info_str += f"\n  Constructor info for {cls_name}:\n{info}\n"

        user_msg = (
            f"Target method: {self.target_method.signature}\n"
            f"Test scenarios file:\n{json.dumps(test_scenarios, indent=2)}\n\n"
            f"Current scenario to implement:\n"
            f"  ID: {scenario['id']}\n"
            f"  Type: {scenario['type']}\n"
            f"  Description: {scenario['description']}\n"
            f"  Input: {scenario['input']}\n"
            f"  Expected: {scenario['expected_behavior']}\n\n"
            f"Constructor/type info collected:{constructor_info_str}\n\n"
            f"Current test class code (use this to know which imports already exist):\n```java\n{self.test_class_code}\n```"
        )

        thread = MessageThread()
        thread.add_system(TEST_METHOD_SYSTEM_PROMPT)
        thread.add_user(user_msg)

        response, *_ = model.SELECTED_MODEL.call(thread.to_msg())
        return response

    # ── Coverage-Driven Scenario Supplementation ──────────────────────

    def analyze_coverage_and_supplement(self, test_scenarios: dict) -> list[dict]:
        """Run coverage analysis, find uncovered regions, generate new scenarios."""
        if not self.test_class_sig:
            return []

        # Collect and analyze coverage
        coverage_info = self.coverage_analyzer.collect_and_update(self.target_method, self.test_class_sig)

        best_path = coverage_info.get("best_path")
        if not best_path:
            logger.info("No uncovered paths found via coverage analysis")
            return []

        uncovered_lines = coverage_info.get("missed_lines", [])
        branch_conditions = best_path.get("branch_conditions", [])

        logger.info(f"Uncovered lines: {uncovered_lines}")
        logger.info(f"Uncovered branch conditions: {branch_conditions}")

        context_msg = (
            f"Method: {self.target_method.signature}\n"
            f"Javadoc: {self.target_method.javadoc or 'No javadoc'}\n"
            f"Uncovered lines: {uncovered_lines}\n"
            f"Uncovered branch conditions:\n"
        )
        for bc in branch_conditions:
            context_msg += f"  Line {bc['line']}: {bc['statement']} ({bc['conditional']})\n"

        # Generate new scenarios via LLM
        thread = MessageThread()
        thread.add_system(
            "You are generating additional test scenarios to improve code coverage. "
            "For each uncovered region, analyze what input conditions might reach that code path. "
            "Generate new scenarios with type 'edge_case' or 'suspicious'. "
            "Output a JSON array of scenarios, each with: id, type, description, input, expected_behavior, rationale, priority."
        )
        thread.add_user(
            f"Original scenarios:\n{json.dumps(test_scenarios.get('test_scenarios', []), indent=2)}\n\n"
            f"Uncovered context:\n{context_msg}"
        )

        response, *_ = model.SELECTED_MODEL.call(thread.to_msg())
        new_scenarios = self._extract_json_array(response)

        if not new_scenarios:
            logger.info("No new scenarios generated from coverage analysis")
            return []

        logger.info(f"Generated {len(new_scenarios)} new scenarios from coverage feedback")
        return new_scenarios

    # ── Helper Methods ────────────────────────────────────────────────

    def _scenario_to_method_name(self, scenario: dict) -> str:
        """Convert scenario description to a valid Java method name."""
        desc = scenario.get("description", "test")
        name = re.sub(r"[^a-zA-Z0-9\s]", "", desc)
        words = name.lower().split()
        if not words:
            return "testScenario"
        return "test" + "".join(w.capitalize() for w in words)

    def _extract_java_code(self, response: str) -> str:
        """Extract Java code from markdown code block."""
        pattern = re.compile(r"```java\s*([\s\S]*?)\s*```", re.IGNORECASE)
        match = pattern.search(response)
        if match:
            return match.group(1)
        pattern2 = re.compile(r"```\s*([\s\S]*?)\s*```", re.IGNORECASE)
        match2 = pattern2.search(response)
        if match2:
            return match2.group(1)
        return ""

    def _extract_json_array(self, response: str) -> list:
        """Extract a JSON array from response."""
        pattern = re.compile(r"```\s*json\s*([\s\S]*?)\s*```", re.IGNORECASE)
        match = pattern.search(response)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        pattern2 = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)
        match2 = pattern2.search(response)
        if match2:
            try:
                return json.loads(match2.group(0))
            except json.JSONDecodeError:
                pass
        return []

    def _extract_method_result(self, full_exec_result, method_name: str):
        """Extract the execution result for a specific test method from the full ExecutionResult.

        Only returns the result for the newly added method, preserving earlier
        methods' results (which may be bug-detecting).
        """
        if full_exec_result is None:
            return None

        for mr in full_exec_result.method_results:
            if mr.method_name == method_name:
                return mr

        # If not found in method_results, return a summary
        return {
            "method_name": method_name,
            "overall_status": full_exec_result.status,
        }
