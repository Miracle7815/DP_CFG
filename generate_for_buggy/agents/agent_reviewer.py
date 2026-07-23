# for reviewing test scenarios and diagnosing test failures
import json
import re
import os
from ..config import logger
from ..basic_class.llm_message import MessageThread
from ..models import model

REQUIREMENT_DIR = os.path.join(os.path.dirname(__file__) , '..' , '..' , 'results' , 'detailed_res_info')

# ═══════════════════════════════════════════════════════════
# Role A: Scenario Review Prompts
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT_SCENARIO_REVIEW = '''You are an expert Software Quality Assurance Lead. Your task is to audit a set of test scenarios generated for a Java method.'''

SCENARIO_REVIEW_PROMPT = '''You will be given:
- Method signature
- Javadoc documentation
- Test scenarios file (JSON)

Each scenario contains two constraint-based fields:
- input_constraints: Abstract description of the input properties/conditions
- expected_behavior_constraints: Abstract description of the expected behavior

IMPORTANT: These are CONSTRAINTS, not concrete test values. Do NOT suggest replacing them with specific literal values. Your suggested_fix should describe what abstract properties or conditions the constraints should express.

Audit criteria:
1. COMPLETENESS: Do the scenarios cover ALL behaviors described in the javadoc? Are any obvious cases missing?
2. ACCURACY: Are the expected behaviors consistent with the javadoc and method signature? Are any scenarios over-speculating?
3. CONSISTENCY: Are there contradictions between scenarios? (Same input with different expected behaviors)
4. TESTABILITY: Can each scenario's constraints be translated into a concrete test case? Are the constraints specific enough?

IMPORTANT: Do NOT suggest changes to the Javadoc or source code. Only evaluate the test scenarios.

Output JSON:
```json
{
    "result": "Yes" or "No",
    "issues": [
        {
            "scenario_id": "S2" or null,
            "error_type": "missing_coverage" | "inaccurate_expectation" | "contradiction" | "not_testable",
            "error_analysis": "Specific description of the issue",
            "suggested_fix": "Abstract-level instructions on how to fix or add the scenario. Describe what input_constraints and expected_behavior_constraints should express — NOT concrete example values."
        }
    ],
    "suggestions": "Recommendations for additional scenarios"
}
```

Rules:
- If scenarios are adequate, output "Yes" with empty issues array. Do NOT nitpick on style.
- If rejecting, provide SPECIFIC issues with scenario IDs, actionable analysis, and concrete suggested_fix instructions.
- CRITICAL: All suggested_fix instructions must remain at the ABSTRACT constraint level. Never suggest a concrete literal value.'''

#             "suggested_fix": "Exact instructions on how to fix or add the scenario. For missing_coverage, describe the scenario to add. For existing scenarios, describe the exact changes needed to input_constraints and expected_behavior_constraints."

# ═══════════════════════════════════════════════════════════
# Role B: Speculation Sub-Agent Prompts
# ═══════════════════════════════════════════════════════════

SPECULATION_SYSTEM = '''You are an independent code behavior analyst. Your task is to PREDICT what a Java method SHOULD return or do for a given input, based ONLY on documentation and context — NOT on the method's source code.'''

SPECULATION_PROMPT = '''You will be given:
- A test scenario describing the input and the expected behavior from the test author's perspective
- The method's Javadoc (documentation comments)
- Code context: called methods' signatures, class skeletons, field definitions (NO source code)
- The current test input being used

IMPORTANT:
- Do NOT look at the target method's source code. It may contain bugs.
- Make an INDEPENDENT prediction of what the method should do.
- Base your reasoning SOLELY on the Javadoc, the code context, and the test input.

Task:
1. Read the Javadoc and understand what the method is supposed to do
2. Consider the called methods' signatures and class structure from the context
3. Predict what the correct behavior should be for the given input

Output JSON:
{
    "speculated_behavior": "What you believe the method SHOULD return/do",
    "reasoning": "Step-by-step reasoning based on Javadoc and context",
    "confidence": 0.0-1.0
}'''

# ═══════════════════════════════════════════════════════════
# Role B: Final Judgment Prompts
# ═══════════════════════════════════════════════════════════

FINAL_JUDGMENT_SYSTEM = '''You are making the final decision on a test case that failed at an assertion.'''

FINAL_JUDGMENT_PROMPT = '''You have three pieces of information:
1. The test SCENARIO's expected behavior [X]: {scenario_expected}
2. The ACTUAL execution result [Y]: {actual_result}
3. An independent SPECULATION of what the method should do [Z]: {speculation_result}

Decision rules:
- If speculation [Z] MATCHES scenario expectation [X]:
  -> This means two independent analysis agree the code should return [X], but the code returned [Y]. 
  -> Diagnosis: "bug_detected". The test is CORRECT and successfully found a bug in the source code.

- If speculation [Z] MATCHES actual result [Y]:
  -> This means the code is behaving logically according to the independent analysis. The original test expectation [X] was wrong (hallucination).
  -> Diagnosis: "test_issue". The test implementation/expectation is flawed. Provide instructions to fix the test assertion to expect [Y].
  
- If speculation [Z] matches NEITHER [X] nor [Y] 
  -> Diagnosis: "needs_regeneration". The scenario may be flawed or the situation is unclear. Request a new test design.

Output JSON:
```json
{
    "diagnosis": "bug_detected" | "test_issue" | "needs_regeneration",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed justification for the decision",
    "speculation_match": "execution" | "scenario" | "neither",
    "fix_instructions": "Specific changes to make (only for test_issue)",
    "accept_as_bug_detecting": true/false
}
```
'''


class ReviewAgent:
    def __init__(self, role: str = "scenario"):
        self.role = role
        self.history = []

        self.all_projects = None
        self.class_map = None
        self.method_map = None
        self.target_method = None

        self.now_thread = None

    def set_up(self, all_projects, class_map, method_map):
        self.all_projects = all_projects
        self.class_map = class_map
        self.method_map = method_map

    def construct_init_thread(self):
        thread = MessageThread()
        if self.role == 'scenario':
            thread.add_system(SYSTEM_PROMPT_SCENARIO_REVIEW)
        return thread

    def analyse_review(self, response):
        pattern = re.compile(r"```json\s*([\s\S]*?)\s*```", re.IGNORECASE)
        results = []
        for m in pattern.finditer(response):
            raw = m.group(1)
            results.append(json.loads(raw))
        return results

    # ═══════════════════════════════════════════════════════
    # Role A: Review Test Scenarios
    # ═══════════════════════════════════════════════════════

    def review_test_scenarios(self, scenario_file: dict, method_signature: str,
                               javadoc: str) -> dict:
        """Audit test scenarios for completeness, accuracy, consistency, testability.

        Returns:
        {
            "result": "Yes" | "No",
            "issues": [{"scenario_id": ..., "error_type": ..., "error_analysis": ..., "suggested_fix": ...}],
            "suggestions": "..."
        }
        """
        thread = self.construct_init_thread()

        user_prompt = (
            f"Method Signature: {method_signature}\n\n"
            f"Javadoc:\n{javadoc or 'No javadoc available.'}\n\n"
            f"Test Scenarios:\n{json.dumps(scenario_file, indent=2)}\n\n"
            f"{SCENARIO_REVIEW_PROMPT}"
        )
        thread.add_user(user_prompt)

        logger.info(f"ReviewerAgent - Scenario Review Prompt:\n{thread.to_msg()}")
        response, *_ = model.REVIEW_MODEL.call(thread.to_msg())
        logger.info(f"ReviewerAgent - Scenario Review Response:\n{response}")

        results = self.analyse_review(response)
        if results:
            return results[0]

        logger.warning("ReviewerAgent - Could not parse scenario review result as JSON")
        return {"result": "Yes", "issues": [], "suggestions": ""}

    # ═══════════════════════════════════════════════════════
    # Role B: Diagnose Test Failure
    # ═══════════════════════════════════════════════════════

    def review_test_failure(self, scenario: dict, test_code: str,
                             failure_type: str, failure_details,
                             javadoc: str = None,
                             context_without_source: list[str] = None) -> dict:
        """Diagnose why a test method failed.

        Step 1: Deterministic classification (non-LLM) for compilation errors.
        Step 2: For ASSERTION_FAILURE / RUNTIME_ERROR ->
                Speculation Sub-Agent + three-way comparison.

        Speculation Sub-Agent receives ONLY:
        - Test scenario description (input + expected behavior)
        - Method javadoc (NO source code)
        - Context WITHOUT source code (class skeletons, field definitions, method signatures)
        - Current test input (NOT the test code, NOT the execution result)
        """
        # Step 1: Deterministic classification
        if failure_type == "COMPILATION_ERROR":
            return self._diagnose_compilation_error(failure_details)

        if failure_type in ("ASSERTION_FAILURE", "RUNTIME_ERROR"):
            return self._speculate_and_judge(
                scenario, test_code, failure_type, failure_details,
                javadoc, context_without_source
            )

        return {
            "diagnosis": "needs_regeneration",
            "confidence": 0.5,
            "reasoning": f"Unknown failure type: {failure_type}",
            "speculation_match": None,
            "fix_instructions": "Please review and regenerate the test method.",
            "accept_as_bug_detecting": False,
        }

    def _diagnose_compilation_error(self, failure_details) -> dict:
        """Deterministic: extract compilation errors and return fix instructions."""
        error_str = failure_details if isinstance(failure_details, str) else str(failure_details)
        error_lines = error_str.split("\n")
        key_errors = []
        for line in error_lines:
            low = line.lower()
            if "error" in low or "cannot find symbol" in low or "incompatible types" in low:
                key_errors.append(line.strip())

        return {
            "diagnosis": "compilation_error",
            "confidence": 1.0,
            "reasoning": f"Test method failed to compile. {len(key_errors)} compilation errors found.",
            "speculation_match": None,
            "fix_instructions": "Compilation errors:\n" + "\n".join(key_errors[:5]),
            "accept_as_bug_detecting": False,
        }

    def _speculate_and_judge(self, scenario: dict, test_code: str,
                              failure_type: str, failure_details,
                              javadoc: str, context_without_source: list[str]) -> dict:
        """Step 2: Call Speculation Sub-Agent, then perform three-way comparison."""

        # Build context string (without source code)
        context_str = ""
        if context_without_source:
            context_str = "\n".join(context_without_source)

        # Extract actual execution info
        exec_type = "Unknown"
        exec_message = ""
        if hasattr(failure_details, 'raw_output'):
            exec_type = failure_details.status
            exc_type = ""
            exc_msg = ""
            if failure_details.method_results:
                mr = failure_details.method_results[0]
                exc_type = mr.exception_type or ""
                exc_msg = mr.exception_message or ""
            exec_message = f"{exc_type}: {exc_msg}" if exc_type else failure_details.raw_output[:500]
        elif isinstance(failure_details, dict):
            exec_type = failure_details.get("overall_status", "Unknown")
            exc_type = failure_details.get("exception_type", "")
            exc_msg = failure_details.get("exception_message", "")
            exec_message = f"{exc_type}: {exc_msg}"
        else:
            exec_message = str(failure_details)[:500]

        actual_result_str = f"{exec_type}: {exec_message}"

        # ── Step 2a: Call Speculation Sub-Agent ──
        # Inputs: scenario + javadoc + context (no source) + test input (no execution)
        speculation_result = self._call_speculation_sub_agent(
            scenario=scenario,
            javadoc=javadoc,
            context_str=context_str,
        )

        speculated_behavior = speculation_result.get("speculated_behavior", "")

        # ── Step 2b: Three-way comparison via LLM ──
        scenario_expected = scenario.get("expected_behavior", "Unknown")

        return self._three_way_judgment(
            scenario_expected=scenario_expected,
            speculated_behavior=speculated_behavior,
            actual_result=actual_result_str,
        )

    def _call_speculation_sub_agent(self, scenario: dict, javadoc: str,
                                     context_str: str) -> dict:
        """Call the LLM as an independent behavior predictor.

        Receives ONLY:
        - Test scenario description
        - Method javadoc (NO source code)
        - Context WITHOUT source code
        - Test input (NOT execution result)
        """
        thread = MessageThread()
        thread.add_system(SPECULATION_SYSTEM)

        test_input = scenario.get('input', 'unknown')

        user_prompt = (
            f"TEST SCENARIO:\n"
            f"  ID: {scenario.get('id', 'unknown')}\n"
            f"  Type: {scenario.get('type', 'unknown')}\n"
            f"  Description: {scenario.get('description', 'unknown')}\n"
            f"  Input: {test_input}\n"
            f"  Expected Behavior: {scenario.get('expected_behavior', 'unknown')}\n\n"
            f"METHOD JAVADOC:\n{javadoc or 'No javadoc available.'}\n\n"
            f"CODE CONTEXT (NO SOURCE CODE):\n{context_str or 'No additional context.'}\n\n"
            f"{SPECULATION_PROMPT}"
        )
        thread.add_user(user_prompt)

        logger.info(f"ReviewerAgent - Speculation Sub-Agent Prompt:\n{thread.to_msg()}")
        response, *_ = model.REVIEW_MODEL.call(thread.to_msg())
        logger.info(f"ReviewerAgent - Speculation Sub-Agent Response:\n{response}")

        result = self._extract_json_response(response)
        if result is None:
            logger.warning("ReviewerAgent - Could not parse speculation result as JSON")
            return {
                "speculated_behavior": "Unable to determine behavior from context.",
                "reasoning": "Could not parse LLM response as JSON.",
                "confidence": 0.3,
            }
        return result

    def _extract_json_response(self, response: str) -> dict | None:
        """Try to extract JSON object from LLM response."""
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

    def _three_way_judgment(self, scenario_expected: str, speculated_behavior: str,
                             actual_result: str) -> dict:
        """Three-way comparison via LLM call using FINAL_JUDGMENT_PROMPT."""
        thread = MessageThread()
        thread.add_system(FINAL_JUDGMENT_SYSTEM)

        user_prompt = FINAL_JUDGMENT_PROMPT.format(
            scenario_expected=scenario_expected,
            speculation_result=speculated_behavior,
            actual_result=actual_result,
        )
        thread.add_user(user_prompt)

        logger.info(f"ReviewerAgent - Final Judgment Prompt:\n{thread.to_msg()}")
        response, *_ = model.REVIEW_MODEL.call(thread.to_msg())
        logger.info(f"ReviewerAgent - Final Judgment Response:\n{response}")

        result = self._extract_json_response(response)
        if result is None:
            logger.warning("ReviewerAgent - Could not parse final judgment result as JSON")
            return {
                "diagnosis": "needs_regeneration",
                "confidence": 0.3,
                "reasoning": "Could not parse LLM response. Defaulting to regeneration.",
                "speculation_match": "neither",
                "fix_instructions": "Please regenerate the test method with a different approach.",
                "accept_as_bug_detecting": False,
            }

        return result
