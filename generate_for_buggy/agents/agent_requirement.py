# for test scenario generation
import json
import os
import re
from copy import deepcopy
from ..config import logger
from ..basic_class.llm_message import MessageThread, FunctionCall, ContextManager
from ..models import model
from .agent_reviewer import ReviewAgent

SYSTEM_PROMPT = '''You are an experienced software test engineer specialized in test scenario analysis. Your task is to analyze a Java method and gather the necessary code context for generating test scenarios.'''

CONTEXT_SYSTEM_PROMPT = '''You are an experienced software test engineer specialized in test scenario generation and static analysis.
Your task is to analyze a Java method and gather the necessary code context for generating test scenarios.'''

TOOL_PROMPT = '''The method may rely on class fields, constants, or other methods that are not visible in the snippet provided.
You should use tools to retrieve the definition of what you need.

### Constraint:
- Collect context ONLY from current project. Do NOT analyze the internal implementation of Java standard library methods.
- Do NOT recursively analyze all called methods. Only extract called methods if they affect the observable behavior of the target method.
- **IMPORTANT**: The `class_name` parameter MUST be a fully qualified class name with its full package path (e.g., `org.apache.commons.lang3.math.NumberUtils`). Do NOT use short names like `NumberUtils` or partial names like `lang3.NumberUtils`. If you are unsure of the full package, check the import list provided.
- **IMPORTANT**: The `method_name` parameter MUST be a method defined in the current project, NOT a standard library API. Do NOT call `search_method_contract` for methods from `java.lang`, `java.util`, `java.io`, or other `java.*` packages (e.g., `toString`, `equals`, `hashCode`, `size`, `get`, `substring`).

### Available Tools:
- `search_class_skeleton(class_name)`: Get class structure (fields + method signatures, no method bodies)
- `search_method_contract(class_name, method_name)`: Get signature and javadoc of a called method. ONLY call this when a specific called method's semantics (what it returns, side effects, or special behavior) are needed to understand the target method's behavior. Do NOT call this for obvious standard operations or methods whose intent is already clear from their name.
- `search_field_definition(class_name, field_name)`: Get field definition
- `search_use_examples()`: Find local code snippets where the method under test is called by other methods in the project
- `search_called_methods()`: Get the list of all method signatures called by the target method under test

### Output Format:
There are ONLY two valid response types. You MUST choose exactly ONE:

**Type A: Need more context (analysis + JSON tool calls)**

Analysis: Write your reasoning about what you know so far and what context you still need.

JSON tool calls:
```json
{
    "tool_calls":[
        {
            "tool_name": "tool_name",
            "args": {
                "parameter_name": "parameter"
            }
        }
    ]
}
```

**Type B**
If you have gathered all necessary information, output ONLY this token and nothing else:
[ANALYSIS_COMPLETE]
'''

# ── Scenario Generation Prompts ───────────────────────────────────────

# Examples of GOOD vs BAD:
# - BAD input: "String '0xFF'"
# - GOOD input_constraints: "A non-empty string that starts with '0x' or '0X', followed by one or more valid hexadecimal digits"
# - BAD expected_behavior: "Returns integer 255"
# - GOOD expected_behavior_constraints: "Returns a positive integer equal to the numeric value of the hexadecimal digits. Does not throw any exception."

SCENARIO_GENERATION_SYSTEM = '''You are an expert test engineer specializing in test scenario generation. Your task is to generate comprehensive test scenarios for a Java method based on its documentation and code context.'''

SCENARIO_GENERATION_PROMPT = '''You will be given:
- Method signature
- Javadoc documentation
- Collected context from tool calls (class skeletons, called methods, field definitions)

IMPORTANT: You do NOT have access to the method's source code. Generate scenarios based ONLY on javadoc, method signature, and collected context.

Output a JSON object with:
1. method_intent: High-level summary of what the method should do
2. input_output_spec: Parameter semantics and return value expectations
3. test_scenarios: Array of test scenarios

Each scenario must have:
- id: Unique identifier (S1, S2, ...)
- category: One of "normal_path", "boundary", "edge_case", "suspicious"
- description: What this scenario tests
- input_constraints: Description of characteristic constraints on the input — describe the properties, types, ranges, and conditions the input must satisfy. Use only abstract descriptions of properties. Do NOT provide concrete example values.
- expected_behavior_constraints: Description of characteristic constraints on the expected behavior — describe what SHOULD happen given inputs matching the input constraints, in terms of observable properties, return value characteristics, or exception types. Do NOT provide concrete example values.
- rationale: Why this scenario is needed (which javadoc rule it covers)
- priority: "high", "medium", or "low"

Scenario categories:
- normal_path: Normal behavior explicitly described in javadoc
- boundary: Edge cases (null, empty, extreme values)
- edge_case: Unusual input combinations
- suspicious: Undocumented behaviors that may indicate bugs (mark as [Not Specified] or [Inferred])

Rules:
- Scenarios for normal_path and boundary MUST be derived ONLY from javadoc and method signature
- Suspicious scenarios may include inferred behaviors but MUST be clearly marked
- input_constraints and expected_behavior_constraints must describe abstract properties ONLY — NEVER include concrete example values or illustrative examples
- Every scenario must have a specific, testable expected behavior

Output Format:
You MUST output ONLY a valid JSON object enclosed in ```json ... ``` code blocks. Do NOT include any explanatory text, analysis, or additional content outside the JSON object.

```json
{
    "method_intent": "...",
    "input_output_spec": { ... },
    "test_scenarios": [
        {
            "id": "S1",
            "category": "normal_path",
            "description": "...",
            "input_constraints": "...",
            "expected_behavior_constraints": "...",
            "rationale": "...",
            "priority": "high"
        }
    ]
}
```
'''

# ── Scenario Revision Prompt (Reviewer Feedback Optimization) ─────────

SCENARIO_REVISION_SYSTEM = '''You are an expert test engineer specializing in refining and optimizing test scenarios based on reviewer feedback. Your task is to improve existing test scenarios, not generate from scratch.'''

SCENARIO_REVISION_PROMPT = '''You are given:
- Method signature and javadoc
- Collected context (class skeletons, called method signatures/javadoc, field definitions)
- Previous test scenarios file
- Reviewer feedback with specific issues and suggestions

Task: Fix and optimize the test scenarios file based on the reviewer's feedback.

For each issue in the feedback:
1. If an existing scenario is flagged, MODIFY or REMOVE it as appropriate.
2. If a new scenario is needed, ADD it with proper structure.
3. Preserve all scenarios that the reviewer did NOT flag as issues.

Output the COMPLETE, CORRECTED test scenarios file with the following structure:
1. method_intent: High-level summary of what the method should do
2. input_output_spec: Parameter semantics and return value expectations
3. test_scenarios: Full array of scenarios (both preserved and new/modified)

Each scenario must have:
- id: Unique identifier (S1, S2, ...) — preserve original IDs for unmodified scenarios
- category: One of "normal_path", "boundary", "edge_case", "suspicious"
- description: What this scenario tests
- input_constraints: Description of characteristic constraints on the input — abstract properties only, NO concrete example values
- expected_behavior_constraints: Description of characteristic constraints on the expected behavior — abstract properties only, NO concrete example values
- rationale: Why this scenario is needed (which javadoc rule or reviewer suggestion it addresses)
- priority: "high", "medium", or "low"

Output Format:
You MUST output ONLY a valid JSON object enclosed in ```json ... ``` code blocks. Do NOT include any explanatory text, analysis, or additional content outside the JSON object.

```json
{
    "method_intent": "...",
    "input_output_spec": { ... },
    "test_scenarios": [ ... ]
}
```
'''

# ── Coverage-Driven Supplementation Prompt ────────────────────────────

COVERAGE_SUPPLEMENT_PROMPT = '''You are generating additional test scenarios to improve code coverage.

You will be given:
- Existing test scenarios
- Uncovered branch conditions from the control flow graph
- Method signature and javadoc

Task: Generate NEW test scenarios that would exercise the uncovered branch conditions.

For each uncovered branch condition:
1. Analyze what input conditions would make the branch evaluate to the opposite direction
2. Generate a new scenario with type "edge_case" or "suspicious"
3. Clearly mark the rationale as coverage-driven

Output only NEW scenarios as a JSON array.
Each scenario must have: id, type, description, input_constraints, expected_behavior_constraints, rationale, priority.'''

BASE_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'results', 'detailed_res_info')


class RequirementAgent:
    def __init__(self, project_name, all_packages, class_map, method_map):
        self.project_name = project_name
        self.all_packages = all_packages
        self.class_map = class_map
        self.method_map = method_map

        self.context_request_memory = []
        self.target_method = None

    # ── Tool Implementations ──────────────────────────────────────────

    def search_field_definition(self, class_name, field_name):
        for classs_name, classs in self.class_map.items():
            if classs_name == class_name:
                for field, statement in classs.fields.items():
                    if field == field_name:
                        return statement
        return None

    def search_use_example(self):
        """Extract local code snippets around the call site using tree-sitter AST.

        Returns up to 3 short code excerpts containing the call.
        """
        from tree_sitter_languages import get_language, get_parser

        language = get_language("java")
        parser = get_parser("java")

        results = []
        for method in self.method_map.values():
            for method_info in method.called_method_site:
                if method_info[0] == self.target_method.name and method_info[1] == tuple(self.target_method.parameters_list):
                    call_line = method_info[2]  # 0-indexed line number in method.content

                    # Parse the method body to find the statement containing the call
                    code_bytes = method.content.encode("utf-8", errors="replace")
                    tree = parser.parse(code_bytes)

                    lines = method.content.split("\n")

                    def find_statement_at_line(node, target):
                        for child in node.children:
                            if target < child.start_point[0] or target > child.end_point[0]:
                                continue
                            if child.type in ("expression_statement", "if_statement", "while_statement",
                                            "for_statement", "do_statement", "return_statement",
                                            "variable_declaration", "local_variable_declaration",
                                            "enhanced_for_statement", "try_with_resources_statement"):
                                return child
                            result = find_statement_at_line(child, target)
                            if result is not None:
                                return result
                        return None

                    stmt_node = find_statement_at_line(tree.root_node, call_line)

                    if stmt_node:
                        context_lines = []
                        for i in range(max(0, stmt_node.start_point[0] - 2), min(len(lines), stmt_node.end_point[0] + 3)):
                            prefix = ">>> " if i == call_line else "    "
                            context_lines.append(f"{prefix}{lines[i]}")
                        snippet = "\n".join(context_lines)
                    else:
                        # Fallback: show the call line with 2 lines of context
                        context_lines = []
                        for i in range(max(0, call_line - 2), min(len(lines), call_line + 3)):
                            prefix = ">>> " if i == call_line else "    "
                            context_lines.append(f"{prefix}{lines[i]}")
                        snippet = "\n".join(context_lines)

                    results.append(snippet)
                    if len(results) >= 3:
                        return results
        return results

    def search_method_contract(self, class_name, method_name):
        result = []
        for classs_name, classs in self.class_map.items():
            if classs_name == class_name:
                for method in classs.methods:
                    if method.name_no_package == method_name and method in self.target_method.called_methods:
                        javadoc = method.javadoc if method.javadoc else "No javadoc available."
                        result.append({"signature": method.signature, "javadoc": javadoc})
                break
        return result

    def search_called_methods(self):
        """Return the list of all non-standard-library method signatures called by the target method."""
        standard_packages = ("java.lang", "java.util", "java.io", "java.nio", "java.net", "java.math", "java.time", "java.text", "java.sql", "javax")
        results = []
        for m in self.target_method.called_methods:
            pkg = m.belong_package.name if m.belong_package else ""
            if any(pkg.startswith(sp) for sp in standard_packages):
                continue
            sig = m.signature if m.signature else f"{m.name}()"
            results.append({"signature": sig})
        return results

    def search_class_skeleton(self, class_name):
        skeleton_str = ""
        for classs_name, classs in self.class_map.items():
            if classs_name == class_name:
                skeleton_str += classs.signature + "\n"
                skeleton_str += '   -Fields:\n'
                for class_field, statement in classs.fields.items():
                    skeleton_str += f"      {statement}\n"
                skeleton_str += '   -Constructors:\n'
                for constructor in classs.constructor:
                    skeleton_str += f"      {constructor.signature}\n"
                skeleton_str += '   -Methods:\n'
                for method in classs.methods:
                    skeleton_str += f"      {method.signature}\n"
                break
        if skeleton_str == "":
            skeleton_str = "Class not found"
        return skeleton_str

    def _extract_analysis_text(self, response: str) -> str:
        """Extract the analysis text that appears before the JSON block in the response."""
        pattern = re.compile(r"```(?:json)?\s*[\s\S]", re.IGNORECASE)
        match = pattern.search(response)
        if match:
            analysis = response[:match.start()].strip()
            return analysis
        return response.strip()

    def _parse_tool_calls_from_response(self, response: str) -> list[dict]:
        """Parse tool calls from LLM's JSON output.

        Expected format:
        ```json
        {
            "tool_calls": [
                {"tool_name": "search_method_contract", "args": {"class_name": "...", "method_name": "..."}}
            ]
        }
        ```
        """
        pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
        match = pattern.search(response)
        if match:
            try:
                data = json.loads(match.group(1))
                return data.get("tool_calls", [])
            except json.JSONDecodeError:
                pass

        # Fallback: try to find JSON object without code block
        pattern2 = re.compile(r"\{[\s\S]*\}", re.DOTALL)
        match2 = pattern2.search(response)
        if match2:
            try:
                data = json.loads(match2.group(0))
                return data.get("tool_calls", [])
            except json.JSONDecodeError:
                pass

        return []

    # ── Phase 1: Context Collection (ReAct Loop) ─────────────────────

    def collect_context(self, target_method, max_turns=10):
        """ReAct loop: LLM outputs JSON with tool calls, we parse and execute them.

        Full tool results are stored ONLY in ContextManager.
        Each round, the model's tool_calls + collected context summary
        is added via thread.add_model() as role='assistant'.
        """
        self.target_method = target_method
        self.context_request_memory = []

        javadoc = target_method.javadoc if target_method.javadoc is not None else "No Java doc"
        import_string = ""
        for import_info in target_method.import_map.values():
            import_string += import_info + "\n"
        import_string = import_string if import_string != "" else "No import information"

        # Initialize ContextManager to store all tool results
        self.context_manager = ContextManager(
            target_method.belong_class, target_method.name,
            import_string, target_method.signature, javadoc=javadoc
        )

        thread = MessageThread()
        thread.add_system(CONTEXT_SYSTEM_PROMPT + '\n' + TOOL_PROMPT)

        user_prompt = (
            f"Here is the method under test:\n"
            f"  Class: {target_method.belong_class.name_no_package}\n"
            f"  Signature: {target_method.signature}\n"
            f"  Javadoc: {javadoc}\n"
            f"  Imports: {import_string}\n"
        )
        thread.add_user(user_prompt)

        logger.info(f"RequirementAgent - Context Collection Prompt:\n{thread.to_msg()}")

        response, *_ = model.SELECTED_MODEL.call(thread.to_msg())
        logger.info(f"RequirementAgent - Context Collection Response:\n{response}")

        tool_id = 0
        for try_num in range(max_turns):
            if "[ANALYSIS_COMPLETE]" in response:
                break

            # Parse tool calls from JSON response
            tool_calls = self._parse_tool_calls_from_response(response)

            if tool_calls:
                # Execute tools — store FULL results in ContextManager and collect for user_summary
                full_results = []
                for tc in tool_calls:
                    tool_name = tc.get('tool_name', '')
                    args = tc.get('args', {})

                    if tool_name == 'search_method_contract':
                        class_name = args.get('class_name', '')
                        method_name = args.get('method_name', '')
                        result = self.search_method_contract(class_name, method_name)
                        if len(result) > 0:
                            for m in result:
                                key = f"{class_name}.{method_name}"
                                sig_javadoc = f"{m['signature']}\n\n{m['javadoc']}"
                                self.context_manager.update_collected_methods(key, sig_javadoc)
                                full_results.append(f"### Signature & Javadoc: {key}\n```\n{sig_javadoc}\n```")
                        else:
                            full_results.append(f"### Signature & Javadoc: {class_name}.{method_name}\nMethod not found.")

                    elif tool_name == 'search_called_methods':
                        result = self.search_called_methods()
                        if result:
                            sigs = "\n".join(f"  - {r['signature']}" for r in result)
                            full_results.append(f"### Called Methods:\n{sigs}")
                            # Store in ContextManager using dedicated method
                            for r in result:
                                self.context_manager.update_collected_called_methods(r['signature'])
                        else:
                            full_results.append("### Called Methods:\nNo non-standard-library called methods found.")

                    elif tool_name == 'search_class_skeleton':
                        class_name = args.get('class_name', '')
                        skeleton = self.search_class_skeleton(class_name)
                        self.context_manager.update_collected_skeletons(class_name, skeleton)
                        full_results.append(f"### Skeleton: {class_name}\n```\n{skeleton}\n```")

                    elif tool_name == 'search_field_definition':
                        class_name = args.get('class_name', '')
                        field_name = args.get('field_name', '')
                        result = self.search_field_definition(class_name, field_name)
                        if result is not None:
                            self.context_manager.update_collected_fields(class_name, field_name, result)
                            full_results.append(f"### Field: {class_name}.{field_name}\n```\n{result}\n```")
                        else:
                            full_results.append(f"### Field: {class_name}.{field_name}\nField not found.")

                    elif tool_name == 'search_use_examples':
                        result = self.search_use_example()
                        if result:
                            for i, r in enumerate(result):
                                self.context_manager.update_collected_examples(r)
                                full_results.append(f"### Use Example {i+1}\n```\n{r}\n```")
                        else:
                            full_results.append("### Use Examples\nNo use examples found.")

                    else:
                        full_results.append(f"### Tool Error\nTool '{tool_name}' not found.")

                    # Track in memory
                    fc = FunctionCall(tool_id, tool_name, args)
                    fc.set_result(" (stored in ContextManager)", True)
                    self.context_request_memory.append(fc)
                    tool_id += 1

                # Add model analysis + full tool results as assistant + user messages
                analysis_text = self._extract_analysis_text(response)
                tool_results_text = "\n\n".join(full_results)

                assistant_content = (
                    f"{analysis_text}\n\n"
                    # f"**Tool calls executed this round:**\n"
                )
                thread.add_model(assistant_content)

                user_summary = (
                    f"**Full results from tool calls:**\n\n{tool_results_text}"
                )
                thread.add_user(user_summary)

            logger.info(f"RequirementAgent - Context Collection Prompt:\n{thread.to_msg()}")
            response, *_ = model.SELECTED_MODEL.call(thread.to_msg())
            logger.info(f"RequirementAgent - Context Collection Response:\n{response}")

    # ── Phase 2: Initial Scenario Generation ─────────────────────────

    def generate_initial_scenarios(self) -> dict:
        """Generate test scenarios from javadoc + collected context (no source code).

        Returns the scenario dict with method_intent, input_output_spec, test_scenarios.
        """
        javadoc = self.target_method.javadoc if self.target_method.javadoc is not None else "No Java doc"
        import_string = ""
        for import_info in self.target_method.import_map.values():
            import_string += import_info + "\n"

        context_str = self.context_manager.format_for_prompt()

        thread = MessageThread()
        thread.add_system(SCENARIO_GENERATION_SYSTEM)

        user_prompt = (
            f"Method under Test:\n"
            f"  Class: {self.target_method.belong_class.name_no_package}\n"
            f"  Signature: {self.target_method.signature}\n"
            f"  Javadoc: {javadoc}\n"
            f"  Imports: {import_string}\n\n"
            f"Collected Context:\n{context_str}\n\n"
            f"{SCENARIO_GENERATION_PROMPT}"
        )
        thread.add_user(user_prompt)

        logger.info(f"RequirementAgent - Scenario Generation Prompt:\n{thread.to_msg()}")

        for retry in range(5):
            response, _, _, _, reason = model.SELECTED_MODEL.call(thread.to_msg() , temperature=0.4)
            if reason == 'length':
                logger.info("Retry scenario generation due to length limit")
                continue

            logger.info(f"RequirementAgent - Scenario Generation Response:\n{response}")
            scenario_data = self._extract_scenario_json(response)
            if scenario_data is not None:
                return scenario_data

            thread.add_user("Could not parse your response as JSON. Please output a valid JSON object as specified.")

        return {"method_intent": "", "input_output_spec": {}, "test_scenarios": []}

    def _extract_scenario_json(self, response: str) -> dict | None:
        """Extract the scenario JSON from LLM response."""
        pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
        match = pattern.search(response)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        pattern2 = re.compile(r"\{[\s\S]*\}", re.DOTALL)
        match2 = pattern2.search(response)
        if match2:
            try:
                return json.loads(match2.group(0))
            except json.JSONDecodeError:
                pass
        return None

    def _extract_json_array(self, response: str) -> list:
        """Extract a JSON array from response."""
        pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
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

    # ── Phase 3: Reviewer Iteration ──────────────────────────────────

    def iterate_with_reviewer(self, scenario_data: dict, max_rounds=5) -> dict:
        """Submit scenarios to ReviewerAgent for audit, iterate until approved or max rounds."""
        review_agent = ReviewAgent('scenario')
        review_agent.set_up(self.all_packages, self.class_map, self.method_map)

        javadoc = self.target_method.javadoc if self.target_method.javadoc is not None else "No Java doc"

        history_issue = []

        for round_idx in range(max_rounds):
            review_result = review_agent.review_test_scenarios(
                scenario_file=scenario_data,
                method_signature=self.target_method.signature,
                javadoc=javadoc,
            )

            if review_result is None:
                break

            if review_result.get('result', 'Yes').strip() == "Yes":
                logger.info(f"Scenario review passed at round {round_idx + 1}")
                return scenario_data

            # Review rejected — generate corrected scenarios
            issues = review_result.get('issues', [])
            suggestions = review_result.get('suggestions', '')

            new_issues = []
            for issue in issues:
                suggested_fix = issue.get('suggested_fix', 'No specific fix suggested.')
                issue_desc = (
                    f"**Scenario: {issue.get('scenario_id', 'unknown')}**\n"
                    f"  - Error type: {issue.get('error_type', 'unknown')}\n"
                    f"  - Analysis: {issue.get('error_analysis', '')}\n"
                    f"  - Suggested fix: {suggested_fix}"
                )
                if issue_desc not in history_issue:
                    history_issue.append(issue_desc)
                    new_issues.append(issue_desc)

            history_message = "\n".join(f"{i+1}. {h}" for i, h in enumerate(history_issue))
            issue_text = "\n".join(f"{i+1}. {n}" for i, n in enumerate(new_issues))

            feedback = (
                f"The Lead Auditor has REJECTED your previous test scenarios file.\n\n"
                f"### CURRENT CRITICAL ISSUES\n{issue_text}\n\n"
                f"### AUDITOR'S SUGGESTIONS\n{suggestions}\n\n"
                f"Address each issue above and output the COMPLETE corrected file."
            )

            # Use dedicated revision prompt (not the initial generation prompt)
            thread = MessageThread()
            thread.add_system(SCENARIO_REVISION_SYSTEM)

            javadoc_full = self.target_method.javadoc if self.target_method.javadoc is not None else "No Java doc"
            import_string = ""
            for import_info in self.target_method.import_map.values():
                import_string += import_info + "\n"
            context_str_full = self.context_manager.format_for_prompt()

            user_prompt = (
                f"Method under Test:\n"
                f"  Class: {self.target_method.belong_class.name_no_package}\n"
                f"  Signature: {self.target_method.signature}\n"
                f"  Javadoc: {javadoc_full}\n"
                f"  Imports: {import_string}\n\n"
                f"Collected Context:\n{context_str_full}\n\n"
                f"Previous Scenarios:\n{json.dumps(scenario_data, indent=2)}\n\n"
                f"Reviewer Feedback:\n{feedback}\n\n"
                f"{SCENARIO_REVISION_PROMPT}"
            )
            thread.add_user(user_prompt)

            logger.info(f"RequirementAgent - Scenario Correction Prompt:\n{thread.to_msg()}")
            response, _, _, _, reason = model.SELECTED_MODEL.call(thread.to_msg())

            if reason == 'length':
                logger.info("Retry due to length limit during correction")
                continue

            logger.info(f"RequirementAgent - Scenario Correction Response:\n{response}")
            new_data = self._extract_scenario_json(response)
            if new_data is not None:
                scenario_data = new_data
                group = self.project_name.split('_')[0]
                dir_path = os.path.join(BASE_PATH, group, self.project_name)
                save_to_file(dir_path, 'test_scenarios.json', json.dumps(scenario_data, indent=4))

        return scenario_data

    # ── Phase 4: Coverage-Driven Scenario Supplementation ─────────────

    def generate_supplement_scenarios(self, existing_scenarios: dict,
                                       branch_conditions: list,
                                       max_rounds=3) -> dict:
        """Generate new scenarios based on uncovered branch conditions from the best path.

        Since the model does NOT know the source code, we only provide:
        - The branch conditions (statement + conditional direction) from the best path
        - Method signature + javadoc
        - Existing scenarios

        Returns the updated scenario dict.
        """
        javadoc = self.target_method.javadoc if self.target_method.javadoc is not None else "No Java doc"

        # Build branch conditions message (no line numbers, no source code)
        branch_msg = ""
        for bc in branch_conditions:
            branch_msg += (
                f"  - Statement: {bc.get('statement', '')}\n"
                f"    Conditional: {bc.get('conditional', '')}\n"
            )

        thread = MessageThread()
        thread.add_system(SCENARIO_GENERATION_SYSTEM)

        user_prompt = (
            f"Method under Test:\n"
            f"  Signature: {self.target_method.signature}\n"
            f"  Javadoc: {javadoc}\n\n"
            f"Existing Scenarios:\n{json.dumps(existing_scenarios.get('test_scenarios', []), indent=2)}\n\n"
            f"Uncovered Branch Conditions:\n{branch_msg}\n\n"
            f"{COVERAGE_SUPPLEMENT_PROMPT}"
        )
        thread.add_user(user_prompt)

        logger.info(f"RequirementAgent - Coverage Supplement Prompt:\n{thread.to_msg()}")
        response, _, _, reason = model.SELECTED_MODEL.call(thread.to_msg())

        if reason == 'length':
            return existing_scenarios

        logger.info(f"RequirementAgent - Coverage Supplement Response:\n{response}")

        # Extract new scenarios
        new_scenarios = self._extract_json_array(response)
        if not new_scenarios:
            logger.info("No new scenarios generated from coverage analysis")
            return existing_scenarios

        # Review new scenarios
        review_agent = ReviewAgent('scenario')
        review_agent.set_up(self.all_packages, self.class_map, self.method_map)

        supplement_file = {
            "method_intent": existing_scenarios.get("method_intent", ""),
            "input_output_spec": existing_scenarios.get("input_output_spec", {}),
            "test_scenarios": new_scenarios
        }

        for round_idx in range(max_rounds):
            review_result = review_agent.review_test_scenarios(
                scenario_file=supplement_file,
                method_signature=self.target_method.signature,
                javadoc=javadoc,
            )
            if review_result is None or review_result.get('result', 'Yes').strip() == "Yes":
                break

        # Append new scenarios to existing
        existing_ids = {s["id"] for s in existing_scenarios.get("test_scenarios", [])}
        for s in new_scenarios:
            if s["id"] not in existing_ids:
                existing_scenarios.setdefault("test_scenarios", []).append(s)
                existing_ids.add(s["id"])

        return existing_scenarios

    # ── Main Entry ────────────────────────────────────────────────────

    def write_scenarios_for_method(self, target_method) -> dict:
        """Full pipeline: collect context -> generate scenarios -> review -> return.

        Returns the final test scenarios dict.
        """
        logger.info("========= Stage 1: Context gathering =========")
        self.collect_context(target_method)

        logger.info("========= Stage 2: Initial scenario generation =========")
        scenario_data = self.generate_initial_scenarios()

        group = self.project_name.split('_')[0]
        dir_path = os.path.join(BASE_PATH, group, self.project_name)
        save_to_file(dir_path, 'test_scenarios.json', json.dumps(scenario_data, indent=4))

        logger.info("========= Stage 3: Reviewer iteration =========")
        scenario_data = self.iterate_with_reviewer(scenario_data)

        # Save to file
        group = self.project_name.split('_')[0]
        dir_path = os.path.join(BASE_PATH, group, self.project_name)
        save_to_file(dir_path, 'test_scenarios.json', json.dumps(scenario_data, indent=4))

        logger.info(f"Test scenarios saved to {dir_path}/test_scenarios.json")
        return scenario_data


def save_to_file(dir_path, file_name, content):
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, file_name), 'w', encoding='utf-8') as f:
        f.write(content)
