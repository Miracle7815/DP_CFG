import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Optional

from ..basic_class.llm_message import MessageThread
from ..config import logger
from ..models import model
from ..privacy import validate_and_record_prompt


PREDICTION_SYSTEM = """You are an independent behavior analyst. Predict the correct observable behavior
from public documentation and source-free context only. You must not infer behavior from an implementation,
an execution result, or a test oracle."""

PREDICTION_PROMPT = """Method signature:
{method_signature}

Public documentation:
{javadoc}

Source-free public context:
{context}

Input scenario (the oracle is intentionally hidden):
{scenario}

Return one JSON object with predicted_behavior, oracle_basis, reasoning, and confidence."""

CLASSIFICATION_SYSTEM = """You classify an assertion failure. Use only the public contract, your own
independent prediction, the authored oracle, the assertion, and the normalized execution result. Never use
source code or a fixed program version."""

CLASSIFICATION_PROMPT = """Your independent prediction:
{prediction}

Approved source-free scenario:
{scenario}

Authored expected behavior:
{expected}

Assertion under review:
{assertion}

Normalized actual result:
{actual}

Classify the cause as exactly one of source_bug, test_oracle_error,
test_implementation_error, or ambiguous. Return one JSON object with category, reasoning, confidence,
and fix_instructions. fix_instructions must be empty unless the category is a test problem."""


VALID_CATEGORIES = {
    "source_bug",
    "test_oracle_error",
    "test_implementation_error",
    "ambiguous",
}


@dataclass
class AttributionBallot:
    model_id: str
    prediction: dict
    classification: dict


def _extract_json(response: str) -> Optional[dict]:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response, re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    bare = re.search(r"\{[\s\S]*\}", response)
    if bare:
        candidates.append(bare.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def extract_assertion(test_method: str) -> str:
    assertions = []
    for line in test_method.splitlines():
        stripped = line.strip()
        if re.search(r"\b(assert\w*|fail)\s*\(", stripped):
            assertions.append(stripped)
    return "\n".join(assertions) or "Assertion text unavailable"


class AttributionVoter:
    def __init__(self, models=None):
        self.models = list(models if models is not None else model.ATTRIBUTION_MODELS)

    def attribute(
        self,
        scenario: dict,
        test_method: str,
        actual_result: str,
        method_signature: str,
        javadoc: str,
        public_context: str = "",
    ) -> dict:
        if len(self.models) != 3:
            return self._ambiguous("Exactly three attribution models are required", [])

        identities = [
            (item.__class__.__name__, item.model_name, getattr(item, "base_url", None))
            for item in self.models
        ]
        if len(set(identities)) != 3:
            return self._ambiguous("Attribution model configurations are not independent", [])

        ballots: list[AttributionBallot] = []
        for index, voter_model in enumerate(self.models):
            try:
                prediction = self._predict(
                    voter_model,
                    scenario,
                    method_signature,
                    javadoc,
                    public_context,
                )
                if prediction is None:
                    continue
                classification = self._classify(
                    voter_model,
                    prediction,
                    scenario,
                    test_method,
                    actual_result,
                )
                if classification is None:
                    continue
                ballots.append(AttributionBallot(
                    model_id=f"{voter_model.model_name}@{getattr(voter_model, 'base_url', '') or 'default'}#{index + 1}",
                    prediction=prediction,
                    classification=classification,
                ))
            except Exception as exc:
                logger.warning(f"Attribution voter {index + 1} failed: {exc}")

        ballot_dicts = [asdict(item) for item in ballots]
        if len(ballots) != 3:
            return self._ambiguous("Fewer than three complete ballots were returned", ballot_dicts)

        categories = [item.classification["category"] for item in ballots]
        counts = Counter(categories)
        if counts["source_bug"] >= 2:
            return {
                "decision": "source_bug",
                "category": "source_bug",
                "accept_as_bug_detecting": True,
                "fix_instructions": "",
                "ballots": ballot_dicts,
            }

        test_problem_count = counts["test_oracle_error"] + counts["test_implementation_error"]
        if test_problem_count >= 2:
            if counts["test_oracle_error"] >= 2:
                category = "test_oracle_error"
            elif counts["test_implementation_error"] >= 2:
                category = "test_implementation_error"
            else:
                category = "test_issue"
            fixes = [
                item.classification.get("fix_instructions", "").strip()
                for item in ballots
                if item.classification["category"] in {"test_oracle_error", "test_implementation_error"}
                and item.classification.get("fix_instructions", "").strip()
            ]
            return {
                "decision": "test_issue",
                "category": category,
                "accept_as_bug_detecting": False,
                "fix_instructions": "\n".join(fixes),
                "ballots": ballot_dicts,
            }

        return self._ambiguous("No category reached the two-of-three threshold", ballot_dicts)

    def _predict(self, voter_model, scenario, method_signature, javadoc, context):
        hidden_oracle_scenario = {
            "id": scenario.get("id"),
            "category": scenario.get("category"),
            "input_constraints": scenario.get("input_constraints"),
        }
        thread = MessageThread()
        thread.add_system(PREDICTION_SYSTEM)
        thread.add_user(PREDICTION_PROMPT.format(
            method_signature=method_signature,
            javadoc=javadoc or "No documented contract.",
            context=context or "No additional public context.",
            scenario=json.dumps(hidden_oracle_scenario, ensure_ascii=False),
        ))
        validate_and_record_prompt(thread.to_msg(), "attribution_prediction")
        result = _extract_json(voter_model.call(thread.to_msg(), temperature=0.0).content)
        if not result or not isinstance(result.get("predicted_behavior"), str):
            return None
        return result

    def _classify(self, voter_model, prediction, scenario, test_method, actual_result):
        thread = MessageThread()
        thread.add_system(CLASSIFICATION_SYSTEM)
        source_free_scenario = {
            key: scenario.get(key)
            for key in (
                "id",
                "origin",
                "category",
                "description",
                "input_constraints",
                "expected_behavior_constraints",
                "oracle_basis",
                "coverage_goal_id",
            )
            if scenario.get(key) is not None
        }
        thread.add_user(CLASSIFICATION_PROMPT.format(
            prediction=json.dumps(prediction, ensure_ascii=False),
            scenario=json.dumps(source_free_scenario, ensure_ascii=False),
            expected=scenario.get("expected_behavior_constraints", "Unknown"),
            assertion=extract_assertion(test_method),
            actual=actual_result,
        ))
        validate_and_record_prompt(thread.to_msg(), "attribution_classification")
        result = _extract_json(voter_model.call(thread.to_msg(), temperature=0.0).content)
        if not result or result.get("category") not in VALID_CATEGORIES:
            return None
        result.setdefault("fix_instructions", "")
        return result

    @staticmethod
    def _ambiguous(reason: str, ballots: list[dict]) -> dict:
        return {
            "decision": "ambiguous",
            "category": "ambiguous",
            "accept_as_bug_detecting": False,
            "fix_instructions": "",
            "reason": reason,
            "ballots": ballots,
        }
