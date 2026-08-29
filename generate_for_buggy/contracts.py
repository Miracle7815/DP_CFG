from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Iterable, Optional


SCENARIO_FIELDS = {
    "id",
    "origin",
    "category",
    "description",
    "input_constraints",
    "expected_behavior_constraints",
    "oracle_basis",
    "priority",
    "status",
}
OPTIONAL_SCENARIO_FIELDS = {"coverage_goal_id"}
LEGACY_SCENARIO_FIELDS = {"type", "input", "expected_behavior"}
VALID_ORIGINS = {"initial", "coverage"}
VALID_CATEGORIES = {"normal_path", "boundary", "edge_case", "suspicious"}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_SCENARIO_STATUSES = {"draft", "approved", "rejected", "implemented", "failed"}


class ContractError(ValueError):
    pass


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


_CONCRETE_LITERAL = re.compile(
    r"(?:[\"'][^\"']+[\"']|(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![A-Za-z_]))"
)


def _abstract_value_issues(value: Any, path: str) -> list[str]:
    issues = []
    if isinstance(value, bool) or value is None:
        return issues
    if isinstance(value, (int, float)):
        return [f"{path} must not contain a concrete value"]
    if isinstance(value, str):
        if _CONCRETE_LITERAL.search(value):
            issues.append(f"{path} must describe properties without concrete literals")
        return issues
    if isinstance(value, dict):
        for key, child in value.items():
            issues.extend(_abstract_value_issues(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_abstract_value_issues(child, f"{path}[{index}]"))
    return issues


def _input_output_spec_issues(value: Any, path: str = "input_output_spec") -> list[str]:
    issues = []
    concrete_keys = {"example", "examples", "value", "values", "literal", "default", "sample_input", "sample_output"}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in concrete_keys:
                issues.append(f"{child_path} must not provide concrete input or output values")
            else:
                issues.extend(_input_output_spec_issues(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_input_output_spec_issues(child, f"{path}[{index}]"))
    return issues


def validate_scenario(scenario: dict, expected_origin: Optional[str] = None) -> list[str]:
    issues: list[str] = []
    scenario_id = scenario.get("id", "unknown") if isinstance(scenario, dict) else "unknown"
    if not isinstance(scenario, dict):
        return ["Scenario must be an object"]

    legacy = LEGACY_SCENARIO_FIELDS.intersection(scenario)
    if legacy:
        issues.append(f"{scenario_id}: legacy fields are forbidden: {sorted(legacy)}")

    missing = SCENARIO_FIELDS.difference(scenario)
    if missing:
        issues.append(f"{scenario_id}: missing required fields: {sorted(missing)}")

    unknown = set(scenario).difference(SCENARIO_FIELDS | OPTIONAL_SCENARIO_FIELDS | {"rationale"})
    if unknown:
        issues.append(f"{scenario_id}: unknown fields: {sorted(unknown)}")

    for field_name in (
        "id",
        "description",
        "input_constraints",
        "expected_behavior_constraints",
        "oracle_basis",
    ):
        if field_name in scenario and not _non_empty_string(scenario[field_name]):
            issues.append(f"{scenario_id}: {field_name} must be a non-empty string")

    for field_name in ("input_constraints", "expected_behavior_constraints"):
        if isinstance(scenario.get(field_name), str):
            issues.extend(_abstract_value_issues(
                scenario[field_name],
                f"{scenario_id}.{field_name}",
            ))

    origin = scenario.get("origin")
    if origin not in VALID_ORIGINS:
        issues.append(f"{scenario_id}: invalid origin {origin!r}")
    if expected_origin is not None and origin != expected_origin:
        issues.append(f"{scenario_id}: origin must be {expected_origin!r}")

    if scenario.get("category") not in VALID_CATEGORIES:
        issues.append(f"{scenario_id}: invalid category {scenario.get('category')!r}")
    if scenario.get("priority") not in VALID_PRIORITIES:
        issues.append(f"{scenario_id}: invalid priority {scenario.get('priority')!r}")
    if scenario.get("status") not in VALID_SCENARIO_STATUSES:
        issues.append(f"{scenario_id}: invalid status {scenario.get('status')!r}")

    goal_id = scenario.get("coverage_goal_id")
    if origin == "coverage" and not _non_empty_string(goal_id):
        issues.append(f"{scenario_id}: coverage scenarios require coverage_goal_id")
    if origin == "initial" and goal_id is not None:
        issues.append(f"{scenario_id}: initial scenarios cannot contain coverage_goal_id")

    if scenario.get("oracle_basis") == "coverage_probe_only":
        if origin != "coverage":
            issues.append(f"{scenario_id}: coverage_probe_only is valid only for coverage scenarios")
        claimed = scenario.get("expected_behavior_constraints", "").lower()
        if re.search(r"\b(return|result|equal|throw|exception|output)\w*\b", claimed):
            issues.append(f"{scenario_id}: a coverage probe cannot claim an output or exception oracle")

    return issues


def validate_scenario_file(scenario_file: dict, expected_origin: Optional[str] = None) -> list[str]:
    if not isinstance(scenario_file, dict):
        return ["Scenario file must be an object"]

    issues: list[str] = []
    scenarios = scenario_file.get("test_scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        issues.append("test_scenarios must be a non-empty array")
        return issues

    ids: set[str] = set()
    for scenario in scenarios:
        issues.extend(validate_scenario(scenario, expected_origin))
        if isinstance(scenario, dict) and _non_empty_string(scenario.get("id")):
            if scenario["id"] in ids:
                issues.append(f"Duplicate scenario id: {scenario['id']}")
            ids.add(scenario["id"])

    if not _non_empty_string(scenario_file.get("method_intent")):
        issues.append("method_intent must be a non-empty string")
    if not isinstance(scenario_file.get("input_output_spec"), dict):
        issues.append("input_output_spec must be an object")
    else:
        issues.extend(_input_output_spec_issues(scenario_file["input_output_spec"]))
    return issues


def require_valid_scenario_file(scenario_file: dict, expected_origin: Optional[str] = None) -> dict:
    issues = validate_scenario_file(scenario_file, expected_origin)
    if issues:
        raise ContractError("; ".join(issues))
    return scenario_file


@dataclass(frozen=True)
class BranchStep:
    branch_id: str
    outcome: str

    def __post_init__(self):
        if self.outcome not in {"taken", "not_taken"}:
            raise ContractError(f"Invalid branch outcome: {self.outcome}")


@dataclass(frozen=True)
class SymbolicPredicate:
    subject: str
    feature: str
    relation: str
    anchor: Optional[str] = None
    peer_subject: Optional[str] = None
    connective: str = "and"


@dataclass(frozen=True)
class CoverageGoal:
    goal_id: str
    target_edge: BranchStep
    path_signature: tuple[BranchStep, ...]
    symbolic_predicates: tuple[SymbolicPredicate, ...]
    support_status: str
    seed_scenario_id: Optional[str] = None
    object_features: tuple[str, ...] = ()
    oracle_clauses: tuple[str, ...] = ()

    def to_public_dict(self) -> dict:
        result = {
            "goal_id": self.goal_id,
            "target_edge": asdict(self.target_edge),
            "path_signature": [asdict(item) for item in self.path_signature],
            "symbolic_predicates": [asdict(item) for item in self.symbolic_predicates],
            "support_status": self.support_status,
            "object_features": list(self.object_features),
            "oracle_clauses": list(self.oracle_clauses),
        }
        if self.seed_scenario_id:
            result["seed_scenario_id"] = self.seed_scenario_id
        assert_public_coverage_goal(result)
        return result


FORBIDDEN_COVERAGE_KEYS = {
    "value",
    "literal",
    "statement",
    "source",
    "source_code",
    "line",
    "line_number",
    "expression",
    "actual_result",
    "private_method",
    "witness",
    "witness_value",
}


def _walk_items(value: Any, path: str = "goal") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, child
            yield from _walk_items(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_items(child, f"{path}[{index}]")


def assert_public_coverage_goal(goal: dict, forbidden_fragments: Iterable[str] = ()) -> None:
    for path, value in _walk_items(goal):
        key = path.rsplit(".", 1)[-1]
        if key in FORBIDDEN_COVERAGE_KEYS:
            raise ContractError(f"CoverageGoal contains forbidden field {path}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            raise ContractError(f"CoverageGoal contains a concrete numeric value at {path}")

    serialized = repr(goal)
    for fragment in forbidden_fragments:
        normalized = fragment.strip()
        if len(normalized) >= 8 and normalized in serialized:
            raise ContractError("CoverageGoal contains a forbidden source fragment")


@dataclass
class ExecutionRecord:
    scenario_id: str
    method_name: str = ""
    compile_status: str = "not_run"
    run_status: str = "not_run"
    failure_kind: Optional[str] = None
    retries: int = 0
    attribution: Optional[dict] = None
    target_hit: Optional[bool] = None
    coverage_delta: Optional[dict] = None
    final_status: str = "test_invalid"
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PrivatePredicate:
    subject: str
    feature: str
    relation: str
    concrete_constant: Any = None
    peer_subject: Optional[str] = None
    control_path: Optional[str] = None
    connective: str = "and"
    supported: bool = True


@dataclass(frozen=True)
class PrivatePathFormula:
    goal_id: str
    target_node_id: Any
    target_outcome: bool
    path_node_ids: tuple[Any, ...]
    path_outcomes: tuple[bool, ...]
    predicates: tuple[PrivatePredicate, ...]
    seed_scenario_id: Optional[str] = None


@dataclass
class WitnessRecord:
    witness_id: str
    goal_id: str
    bindings: dict[str, Any]
    construction_recipe: Optional[dict] = None
    verified: bool = False
    target_hit: bool = False
    failure_reason: Optional[str] = None
