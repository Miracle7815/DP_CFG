# for reviewing test scenarios and diagnosing test failures
import json
import re
import os
from ..config import logger
from ..basic_class.llm_message import MessageThread
from ..contracts import validate_scenario_file
from ..models import model
from ..privacy import validate_and_record_prompt

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
5. ORACLE: Does oracle_basis identify a javadoc clause or public contract? Reject undocumented assertions.
   Exception: a coverage-origin scenario may use oracle_basis="coverage_probe_only" only when it contains no
   claimed return value or exception; it is temporary and cannot enter the final test suite.
6. PRIVACY: Reject concrete hidden values, source statements, line numbers, or implementation-derived expectations.

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
        pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
        results = []
        for m in pattern.finditer(response):
            raw = m.group(1)
            try:
                results.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        if not results:
            bare = re.search(r"\{[\s\S]*\}", response)
            if bare:
                try:
                    results.append(json.loads(bare.group(0)))
                except json.JSONDecodeError:
                    pass
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
        contract_issues = validate_scenario_file(scenario_file)
        if contract_issues:
            return {
                "result": "No",
                "issues": [{
                    "scenario_id": None,
                    "error_type": "not_testable",
                    "error_analysis": issue,
                    "suggested_fix": "Return the complete scenario file using the required canonical schema.",
                } for issue in contract_issues],
                "suggestions": "Fix the schema before semantic review.",
            }

        thread = self.construct_init_thread()

        user_prompt = (
            f"Method Signature: {method_signature}\n\n"
            f"Javadoc:\n{javadoc or 'No javadoc available.'}\n\n"
            f"Test Scenarios:\n{json.dumps(scenario_file, indent=2)}\n\n"
            f"{SCENARIO_REVIEW_PROMPT}"
        )
        thread.add_user(user_prompt)

        logger.info("ReviewerAgent - submitting source-free scenario review prompt")
        reviewer = model.require_model(model.REVIEW_MODEL, "review")
        validate_and_record_prompt(thread.to_msg(), "scenario_review", self.target_method)
        response = reviewer.call(thread.to_msg()).content
        logger.info(f"ReviewerAgent - scenario review response received ({len(response)} chars)")

        results = self.analyse_review(response)
        if results:
            return results[0]

        logger.warning("ReviewerAgent - Could not parse scenario review result as JSON")
        return {
            "result": "No",
            "issues": [{
                "scenario_id": None,
                "error_type": "not_testable",
                "error_analysis": "Reviewer output could not be parsed as the required JSON object.",
                "suggested_fix": "Review the complete scenario file again.",
            }],
            "suggestions": "Reviewer parsing failed; fail closed.",
        }

    # ═══════════════════════════════════════════════════════
    # Role B: Diagnose Test Failure
    # ═══════════════════════════════════════════════════════

    def review_test_failure(self, scenario: dict, test_code: str,
                             failure_type: str, failure_details,
                             javadoc: str = None,
                             context_without_source: list[str] = None) -> dict:
        """Compatibility entry point for deterministic failures only.

        Assertion failures deliberately fail closed here and must be sent to
        AttributionVoter by TestAgent.
        """
        # Step 1: Deterministic classification
        if failure_type == "COMPILATION_ERROR":
            return self._diagnose_compilation_error(failure_details)

        if failure_type == "ASSERTION_FAILURE":
            return {
                "diagnosis": "ambiguous",
                "confidence": 0.0,
                "reasoning": "Assertion attribution is reserved for AttributionVoter's three independent models.",
                "speculation_match": None,
                "fix_instructions": "",
                "accept_as_bug_detecting": False,
            }

        if failure_type == "RUNTIME_ERROR":
            return {
                "diagnosis": "test_issue",
                "confidence": 1.0,
                "reasoning": "Execution did not reach a valid assertion; use the deterministic repair flow.",
                "speculation_match": None,
                "fix_instructions": "Repair test setup or invocation from the structured runtime diagnostic.",
                "accept_as_bug_detecting": False,
            }

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
