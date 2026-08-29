"""Private path modelling and source-free coverage goal construction.

Raw CFG statements and constants stay in this module. Only ``CoverageGoal.to_public_dict``
may cross an LLM boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from ..contracts import (
    BranchStep,
    ContractError,
    CoverageGoal,
    PrivatePathFormula,
    PrivatePredicate,
    SymbolicPredicate,
    WitnessRecord,
)
from ..utils.public_api import is_visible_declaration


COMPARISON_RELATIONS = {
    ">": "greater_than",
    ">=": "at_or_above",
    "<": "less_than",
    "<=": "at_or_below",
    "==": "equal_to",
    "!=": "different_from",
}

INVERTED_RELATIONS = {
    "greater_than": "at_or_below",
    "at_or_above": "less_than",
    "less_than": "at_or_above",
    "at_or_below": "greater_than",
    "equal_to": "different_from",
    "different_from": "equal_to",
    "is_absent": "is_present",
    "is_present": "is_absent",
    "is_true": "is_false",
    "is_false": "is_true",
}


def _opaque(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(item) for item in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _strip_outer_parentheses(expression: str) -> str:
    expression = expression.strip()
    while expression.startswith("(") and expression.endswith(")"):
        depth = 0
        wraps_all = True
        for index, char in enumerate(expression):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(expression) - 1:
                    wraps_all = False
                    break
        if not wraps_all:
            break
        expression = expression[1:-1].strip()
    return expression


def _condition_from_statement(statement: str) -> str:
    text = statement.strip()
    first = text.find("(")
    if first < 0:
        return text
    depth = 0
    for index in range(first, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[first + 1:index]
    return text


def _split_boolean(expression: str) -> tuple[list[str], list[str]]:
    atoms = []
    connectives = []
    current = []
    depth = 0
    quote = None
    index = 0
    while index < len(expression):
        char = expression[index]
        if quote:
            current.append(char)
            if char == quote and (index == 0 or expression[index - 1] != "\\"):
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if depth == 0 and expression[index:index + 2] in {"&&", "||"}:
            atoms.append("".join(current).strip())
            connectives.append("and" if expression[index:index + 2] == "&&" else "or")
            current = []
            index += 2
            continue
        current.append(char)
        index += 1
    if current:
        atoms.append("".join(current).strip())
    return atoms, connectives


def _literal(text: str) -> tuple[bool, Any]:
    text = text.strip()
    if text == "null":
        return True, None
    if text in {"true", "false"}:
        return True, text == "true"
    if re.fullmatch(r"[-+]?\d+[lL]?", text):
        return True, int(text.rstrip("lL"))
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[fFdD])?", text):
        return True, float(text.rstrip("fFdD"))
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return True, text[1:-1]
    return False, None


def _parameter_names(target_method) -> dict[str, str]:
    result = {}
    parameters = target_method.node.child_by_field_name("parameters") if target_method.node else None
    if parameters is None:
        return result
    index = 0
    for child in parameters.named_children:
        if child.type not in {"formal_parameter", "spread_parameter", "receiver_parameter"}:
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            identifiers = [item for item in child.named_children if item.type == "identifier"]
            name_node = identifiers[-1] if identifiers else None
        if name_node is not None:
            result[name_node.text.decode("utf-8")] = f"ARG_{index}"
            index += 1
    return result


def _local_definitions(target_method, before_line: Optional[int]) -> dict[str, str]:
    definitions = {}

    def visit(node):
        if before_line is not None and node.start_point[0] + 1 >= before_line:
            return
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is not None and value is not None:
                definitions[name.text.decode("utf-8")] = value.text.decode("utf-8")
        elif node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None and right is not None and left.type == "identifier":
                definitions[left.text.decode("utf-8")] = right.text.decode("utf-8")
        for child in node.named_children:
            visit(child)

    if target_method.node is not None:
        visit(target_method.node)
    return definitions


def _resolve_local(expression: str, definitions: dict[str, str], depth: int = 0) -> str:
    expression = expression.strip()
    if depth >= 3:
        return expression
    if expression in definitions:
        return _resolve_local(definitions[expression], definitions, depth + 1)
    return expression


@dataclass
class _ParsedSubject:
    subject: str
    feature: str
    object_feature: Optional[str] = None
    offset: float = 0


@dataclass(frozen=True)
class ObjectBinding:
    setup_code: str
    expression: str


@dataclass(frozen=True)
class ObjectLeafBinding:
    value: Any
    public_type: str
    private_control_path: str


def _parse_subject(
    expression: str,
    parameters: dict[str, str],
    definitions: dict[str, str],
    receiver_members: Optional[set[str]] = None,
) -> Optional[_ParsedSubject]:
    expression = _strip_outer_parentheses(_resolve_local(expression, definitions))
    receiver_members = receiver_members or set()
    if expression.startswith("this."):
        return _ParsedSubject("RECEIVER", "object_state", expression[len("this."):])
    if expression in receiver_members:
        return _ParsedSubject("RECEIVER", "object_state", expression)
    for name, subject in parameters.items():
        if expression == name:
            return _ParsedSubject(subject, "value")
        affine = re.fullmatch(
            rf"{re.escape(name)}\s*([+-])\s*([-+]?\d+(?:\.\d+)?)",
            expression,
        )
        if affine:
            offset = float(affine.group(2))
            if affine.group(1) == "-":
                offset = -offset
            return _ParsedSubject(subject, "value", offset=offset)
        if expression in {f"{name}.length", f"{name}.length()"}:
            return _ParsedSubject(subject, "length")
        if expression in {f"{name}.size()", f"{name}.size"}:
            return _ParsedSubject(subject, "size")
        if expression == f"{name}.isEmpty()":
            return _ParsedSubject(subject, "emptiness")
        if expression.startswith(name + "."):
            # This path is private solver state. It is replaced with an opaque feature
            # by CoverageSanitizer before anything can cross an LLM boundary.
            feature = expression[len(name) + 1:]
            return _ParsedSubject(subject, "object_state", feature)
    return None


class SymbolicPathBuilder:
    def build(self, target_method, path_nodes: list[dict], target_index: int,
              target_outcome: bool, seed_scenario_id: Optional[str] = None) -> PrivatePathFormula:
        parameters = _parameter_names(target_method)
        receiver_members = set(getattr(target_method.belong_class, "fields", {}).keys())
        receiver_members.update(
            method.name_no_package + "()"
            for method in getattr(target_method.belong_class, "methods", [])
            if not method.parameters_list
        )
        predicates: list[PrivatePredicate] = []
        path_ids = []
        path_outcomes = []
        target_node_id = path_nodes[target_index].get("id")

        for index, node in enumerate(path_nodes[:target_index + 1]):
            conditional = node.get("conditional")
            if conditional is None:
                continue
            outcome = bool(conditional)
            if index == target_index:
                outcome = target_outcome
            path_ids.append(node.get("id"))
            path_outcomes.append(outcome)
            statement = node.get("statement", "")
            line = None
            mapping = getattr(target_method.belong_file, "node_id_to_line_number", None) or {}
            lines = mapping.get(node.get("id"), [])
            if lines:
                line = lines[0]
            definitions = _local_definitions(target_method, line)
            predicates.extend(self._parse_expression(
                _condition_from_statement(statement),
                parameters,
                definitions,
                outcome,
                receiver_members,
            ))

        goal_id = _opaque("CG", target_method.signature, target_node_id, target_outcome)
        return PrivatePathFormula(
            goal_id=goal_id,
            target_node_id=target_node_id,
            target_outcome=target_outcome,
            path_node_ids=tuple(path_ids),
            path_outcomes=tuple(path_outcomes),
            predicates=tuple(predicates),
            seed_scenario_id=seed_scenario_id,
        )

    def _parse_expression(
        self,
        expression,
        parameters,
        definitions,
        outcome,
        receiver_members: Optional[set[str]] = None,
    ) -> list[PrivatePredicate]:
        expression = _strip_outer_parentheses(expression)
        if expression.startswith("!") and not expression.startswith("!="):
            return self._parse_expression(
                _strip_outer_parentheses(expression[1:]),
                parameters,
                definitions,
                not outcome,
                receiver_members,
            )
        atoms, connectives = _split_boolean(expression)
        if len(set(connectives)) > 1:
            return [PrivatePredicate(
                subject="UNRESOLVED",
                feature="unsupported",
                relation="mixed_boolean_formula",
                supported=False,
            )]
        if outcome and connectives and all(item == "or" for item in connectives):
            atoms = atoms[:1]
            connectives = []
        elif not outcome and connectives and all(item == "and" for item in connectives):
            atoms = atoms[:1]
            connectives = []
        elif not outcome and connectives and all(item == "or" for item in connectives):
            # De Morgan: every disjunct must be false.
            connectives = ["and"] * len(connectives)

        predicates = []
        for index, atom in enumerate(atoms):
            predicate = self._parse_atom(atom, parameters, definitions, receiver_members)
            if predicate is None:
                predicates.append(PrivatePredicate(
                    subject="UNRESOLVED",
                    feature="unsupported",
                    relation="unknown",
                    connective=connectives[index - 1] if index else "and",
                    supported=False,
                ))
                continue
            relation = predicate.relation if outcome else INVERTED_RELATIONS.get(predicate.relation, "unknown")
            predicates.append(PrivatePredicate(
                subject=predicate.subject,
                feature=predicate.feature,
                relation=relation,
                concrete_constant=predicate.concrete_constant,
                peer_subject=predicate.peer_subject,
                control_path=predicate.control_path,
                connective=connectives[index - 1] if index else "and",
                supported=predicate.supported and relation != "unknown",
            ))
        return predicates

    def _parse_atom(
        self,
        atom,
        parameters,
        definitions,
        receiver_members: Optional[set[str]] = None,
    ) -> Optional[PrivatePredicate]:
        atom = _strip_outer_parentheses(atom.strip())

        comparison = re.search(r"(?<![=!<>])(?:>=|<=|==|!=|>|<)(?![=])", atom)
        if comparison:
            operator = comparison.group(0)
            left = atom[:comparison.start()].strip()
            right = atom[comparison.end():].strip()
            left_subject = _parse_subject(left, parameters, definitions, receiver_members)
            right_subject = _parse_subject(right, parameters, definitions, receiver_members)
            right_is_literal, right_literal = _literal(right)
            left_is_literal, left_literal = _literal(left)

            if left_subject and right_is_literal:
                if right_literal is None and operator in {"==", "!="}:
                    relation = "is_absent" if operator == "==" else "is_present"
                    return PrivatePredicate(
                        left_subject.subject,
                        left_subject.feature,
                        relation,
                        control_path=left_subject.object_feature,
                    )
                return PrivatePredicate(
                    left_subject.subject,
                    left_subject.feature,
                    COMPARISON_RELATIONS[operator],
                    concrete_constant=(
                        right_literal - left_subject.offset
                        if isinstance(right_literal, (int, float)) else right_literal
                    ),
                    control_path=left_subject.object_feature,
                )
            if right_subject and left_is_literal:
                reversed_operator = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "==", "!=": "!="}[operator]
                if left_literal is None and operator in {"==", "!="}:
                    relation = "is_absent" if operator == "==" else "is_present"
                    return PrivatePredicate(
                        right_subject.subject,
                        right_subject.feature,
                        relation,
                        control_path=right_subject.object_feature,
                    )
                return PrivatePredicate(
                    right_subject.subject,
                    right_subject.feature,
                    COMPARISON_RELATIONS[reversed_operator],
                    concrete_constant=(
                        left_literal - right_subject.offset
                        if isinstance(left_literal, (int, float)) else left_literal
                    ),
                    control_path=right_subject.object_feature,
                )
            if left_subject and right_subject:
                return PrivatePredicate(
                    left_subject.subject,
                    left_subject.feature,
                    COMPARISON_RELATIONS[operator],
                    peer_subject=right_subject.subject,
                    control_path=left_subject.object_feature,
                )
            return None

        subject = _parse_subject(atom, parameters, definitions, receiver_members)
        if subject:
            relation = "is_true"
            if subject.feature == "emptiness":
                relation = "is_empty"
            return PrivatePredicate(subject.subject, subject.feature, relation, control_path=subject.object_feature)
        return None


class CoverageSanitizer:
    def sanitize(self, target_method, formula: PrivatePathFormula) -> CoverageGoal:
        predicates = []
        object_features = []
        support = "supported"
        anchor_index = 0
        for predicate in formula.predicates:
            if not predicate.supported:
                support = "unsupported_constraint"
            anchor = None
            if predicate.concrete_constant is not None:
                anchor = f"κ{anchor_index}"
                anchor_index += 1
            feature = predicate.feature
            if feature == "object_state":
                object_feature = _opaque("OBJECT_FEATURE", predicate.subject, len(object_features))
                object_features.append(object_feature)
                feature = object_feature
            predicates.append(SymbolicPredicate(
                subject=predicate.subject,
                feature=feature,
                relation=predicate.relation,
                anchor=anchor,
                peer_subject=predicate.peer_subject,
                connective=predicate.connective,
            ))

        path_steps = tuple(
            BranchStep(
                branch_id=_opaque("BRANCH", target_method.signature, node_id),
                outcome="taken" if outcome else "not_taken",
            )
            for node_id, outcome in zip(formula.path_node_ids, formula.path_outcomes)
        )
        target_step = BranchStep(
            branch_id=_opaque("BRANCH", target_method.signature, formula.target_node_id),
            outcome="taken" if formula.target_outcome else "not_taken",
        )
        clauses = tuple(
            f"DOC_{index + 1}"
            for index, part in enumerate(re.split(r"(?<=[.!?])\s+", target_method.javadoc or ""))
            if part.strip()
        )
        return CoverageGoal(
            goal_id=formula.goal_id,
            seed_scenario_id=formula.seed_scenario_id,
            target_edge=target_step,
            path_signature=path_steps,
            symbolic_predicates=tuple(predicates),
            object_features=tuple(object_features),
            oracle_clauses=clauses,
            support_status=support,
        )


class WitnessVault:
    def __init__(self):
        self._records: dict[str, WitnessRecord] = {}

    def put(self, record: WitnessRecord) -> None:
        self._records[record.goal_id] = record

    def get(self, goal_id: str) -> WitnessRecord:
        if goal_id not in self._records:
            raise KeyError(f"No witness registered for {goal_id}")
        return self._records[goal_id]

    def public_reference(self, goal_id: str) -> dict:
        record = self.get(goal_id)
        return {
            "witness_id": record.witness_id,
            "goal_id": record.goal_id,
            "verified": record.verified,
            "target_hit": record.target_hit,
            "failure_reason": record.failure_reason,
        }


class WitnessBinder:
    """Small deterministic solver for the explicitly supported first-version fragment."""

    def __init__(self, class_map: Optional[dict] = None):
        self.vault = WitnessVault()
        self._slot_types: dict[str, dict[str, str]] = {}
        self.class_map = class_map or {}

    def set_class_map(self, class_map: dict) -> None:
        self.class_map = class_map

    def register(
        self,
        formula: PrivatePathFormula,
        parameter_types: list[str],
        receiver_type: Optional[str] = None,
    ) -> WitnessRecord:
        bindings: dict[str, Any] = {}
        failure_reason = None
        predicates = list(formula.predicates)
        subjects: set[str] = set()
        for predicate in formula.predicates:
            if not predicate.supported or not (
                predicate.subject.startswith("ARG_") or predicate.subject == "RECEIVER"
            ):
                failure_reason = "unsupported_constraint"
                break
            subjects.add(predicate.subject)
            if predicate.peer_subject:
                if not predicate.peer_subject.startswith("ARG_"):
                    failure_reason = "unsupported_constraint"
                    break
                if predicate.feature != "value" or predicate.relation not in {"equal_to", "different_from"}:
                    failure_reason = "unsupported_relational_feature"
                    break
                subjects.add(predicate.peer_subject)

        if failure_reason is None and not subjects:
            failure_reason = "no_bindable_path_constraint"

        if failure_reason is None:
            try:
                receiver_predicates = [item for item in predicates if item.subject == "RECEIVER"]
                argument_predicates = [item for item in predicates if item.subject != "RECEIVER"]
                argument_subjects = {item for item in subjects if item != "RECEIVER"}
                if argument_subjects:
                    bindings = self._solve_bindings(
                        argument_predicates,
                        argument_subjects,
                        parameter_types,
                    )
                if receiver_predicates:
                    if not receiver_type:
                        raise ValueError("receiver_type_unavailable")
                    if any(item.peer_subject for item in receiver_predicates):
                        raise ValueError("receiver_relation_not_supported")
                    bindings["RECEIVER"] = self._binding_for_constraints(
                        receiver_predicates,
                        receiver_type,
                        -1,
                    )
            except (ValueError, TypeError) as exc:
                bindings = {}
                failure_reason = str(exc)

        record = WitnessRecord(
            witness_id=f"W_{uuid.uuid4().hex[:12]}",
            goal_id=formula.goal_id,
            bindings=bindings,
            verified=False,
            target_hit=False,
            failure_reason=failure_reason,
        )
        slot_types = {
            f"ARG_{index}": type_name
            for index, type_name in enumerate(parameter_types)
        }
        if receiver_type:
            slot_types["RECEIVER"] = receiver_type
        self._slot_types[formula.goal_id] = slot_types
        self.vault.put(record)
        return record

    def _solve_bindings(
        self,
        predicates: list[PrivatePredicate],
        subjects: set[str],
        parameter_types: list[str],
    ) -> dict[str, Any]:
        parent = {subject: subject for subject in subjects}

        def find(subject):
            while parent[subject] != subject:
                parent[subject] = parent[parent[subject]]
                subject = parent[subject]
            return subject

        def union(left, right):
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        inequalities = []
        for predicate in predicates:
            if not predicate.peer_subject:
                continue
            if predicate.relation == "equal_to":
                union(predicate.subject, predicate.peer_subject)
            else:
                inequalities.append((predicate.subject, predicate.peer_subject))

        members: dict[str, list[str]] = {}
        unary: dict[str, list[PrivatePredicate]] = {}
        for subject in subjects:
            members.setdefault(find(subject), []).append(subject)
        for predicate in predicates:
            if not predicate.peer_subject:
                unary.setdefault(find(predicate.subject), []).append(predicate)

        root_values = {}
        for root, root_members in members.items():
            root_members.sort()
            indices = [int(item.split("_")[-1]) for item in root_members]
            if any(index >= len(parameter_types) for index in indices):
                raise ValueError("parameter_slot_out_of_range")
            types = [parameter_types[index] for index in indices]
            scalar_relation = self._relation_types_supported(types)
            same_erased_type = len({item.split("<", 1)[0].strip() for item in types}) == 1
            if len(root_members) > 1 and not scalar_relation and not same_erased_type:
                raise ValueError("relational_object_witness_not_supported")
            if unary.get(root):
                root_values[root] = self._binding_for_constraints(
                    unary[root],
                    types[0],
                    indices[0],
                )
            elif len(root_members) > 1 and not scalar_relation:
                root_values[root] = self._plain_object_binding(types[0], indices[0])
            else:
                try:
                    root_values[root] = self._default_scalar_binding(types[0])
                except ValueError:
                    if any(
                        left in root_members or right in root_members
                        for left, right in inequalities
                    ):
                        root_values[root] = self._plain_object_binding(types[0], indices[0])
                    else:
                        raise

        for left, right in inequalities:
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                raise ValueError("infeasible_relational_path")
            if root_values[left_root] == root_values[right_root]:
                right_index = int(members[right_root][0].split("_")[-1])
                alternative = self._different_scalar(
                    parameter_types[right_index],
                    root_values[right_root],
                )
                if not all(self._satisfies(item, alternative) for item in unary.get(right_root, [])):
                    raise ValueError("infeasible_relational_path")
                root_values[right_root] = alternative

        return {
            subject: root_values[find(subject)]
            for subject in sorted(subjects)
        }

    @staticmethod
    def _erased_short_type(parameter_type: str) -> str:
        erased = parameter_type.split("<", 1)[0].strip()
        return erased.rsplit(".", 1)[-1]

    def _relation_types_supported(self, parameter_types: list[str]) -> bool:
        short_types = [self._erased_short_type(item) for item in parameter_types]
        numeric = {
            "byte", "short", "int", "long", "float", "double",
            "Byte", "Short", "Integer", "Long", "Float", "Double",
        }
        return all(item in numeric for item in short_types) or len(set(short_types)) == 1 and short_types[0] in {
            "boolean", "Boolean", "String",
        }

    def _default_scalar_binding(self, parameter_type: str):
        short_type = self._erased_short_type(parameter_type)
        if short_type in {
            "byte", "short", "int", "long", "float", "double",
            "Byte", "Short", "Integer", "Long", "Float", "Double",
        }:
            return 0
        if short_type in {"boolean", "Boolean"}:
            return False
        if short_type == "String":
            return ""
        raise ValueError("relational_object_witness_not_supported")

    def _different_scalar(self, parameter_type: str, current: Any):
        short_type = self._erased_short_type(parameter_type)
        if short_type in {
            "byte", "short", "int", "long", "float", "double",
            "Byte", "Short", "Integer", "Long", "Float", "Double",
        }:
            return current + 1
        if short_type in {"boolean", "Boolean"}:
            return not current
        if short_type == "String":
            return current + "x"
        raise ValueError("relational_object_witness_not_supported")

    def public_slots(self, goal_id: str) -> list[dict]:
        record = self.vault.get(goal_id)
        slot_types = self._slot_types.get(goal_id, {})
        slots = []
        for subject in sorted(record.bindings):
            binding = record.bindings[subject]
            slot = {
                "subject": subject,
                "type": slot_types.get(subject, "unknown"),
                "placeholder": f"__DP_WITNESS_{subject}__",
            }
            if isinstance(binding, ObjectBinding):
                slot["setup_placeholder"] = f"__DP_WITNESS_SETUP_{subject}__"
            elif isinstance(binding, ObjectLeafBinding):
                slot["placeholder"] = f"__DP_WITNESS_LEAF_{subject}__"
                slot["role"] = "custom_object_leaf"
                slot["instruction"] = (
                    "Choose a legal public constructor/factory/builder/setter route from the public context "
                    "and place this symbolic leaf placeholder in the documented object property."
                )
            slots.append(slot)
        return slots

    def materialize_test_method(self, goal_id: str, method_code: str) -> str:
        record = self.vault.get(goal_id)
        if not record.bindings:
            raise ContractError(record.failure_reason or "No concrete witness binding is available")
        materialized = method_code
        emitted_object_recipes: set[tuple[str, str]] = set()
        for subject, concrete in record.bindings.items():
            placeholder = f"__DP_WITNESS_{subject}__"
            if isinstance(concrete, ObjectBinding):
                if placeholder not in materialized:
                    raise ContractError(f"Generated test omitted required witness placeholder {placeholder}")
                setup_placeholder = f"__DP_WITNESS_SETUP_{subject}__"
                if setup_placeholder not in materialized:
                    raise ContractError(f"Generated test omitted required witness placeholder {setup_placeholder}")
                recipe_key = (concrete.setup_code, concrete.expression)
                setup_code = "" if recipe_key in emitted_object_recipes else concrete.setup_code
                emitted_object_recipes.add(recipe_key)
                materialized = materialized.replace(setup_placeholder, setup_code)
                materialized = materialized.replace(placeholder, concrete.expression)
            elif isinstance(concrete, ObjectLeafBinding):
                leaf_placeholder = f"__DP_WITNESS_LEAF_{subject}__"
                if leaf_placeholder not in materialized:
                    raise ContractError(f"Generated test omitted required witness placeholder {leaf_placeholder}")
                materialized = materialized.replace(
                    leaf_placeholder,
                    self._java_literal(concrete.value),
                )
            else:
                if placeholder not in materialized:
                    raise ContractError(f"Generated test omitted required witness placeholder {placeholder}")
                materialized = materialized.replace(placeholder, self._java_literal(concrete))
        if any(isinstance(item, ObjectLeafBinding) for item in record.bindings.values()):
            record.construction_recipe = {
                "kind": "generated_public_api_recipe",
                "test_method": materialized,
            }
        return materialized

    def mark_verified(self, goal_id: str, target_hit: bool) -> None:
        record = self.vault.get(goal_id)
        record.verified = True
        record.target_hit = target_hit
        if not target_hit:
            record.failure_reason = "target_miss"

    def _binding_for_constraints(
        self,
        predicates: list[PrivatePredicate],
        parameter_type: str,
        argument_index: int,
    ):
        object_predicates = [item for item in predicates if item.feature == "object_state"]
        if object_predicates:
            if len(object_predicates) != 1 or len(predicates) != 1:
                raise ValueError("multi_property_object_recipe_not_supported")
            return self._object_binding(object_predicates[0], parameter_type, argument_index)

        short_type = self._erased_short_type(parameter_type)
        if short_type in {
            "byte", "short", "int", "long", "float", "double",
            "Byte", "Short", "Integer", "Long", "Float", "Double",
        }:
            integral_type = short_type not in {"float", "double", "Float", "Double"}
            boundaries = [
                item.concrete_constant for item in predicates
                if isinstance(item.concrete_constant, (int, float))
            ]
            candidates = [0]
            for boundary in boundaries:
                candidates.extend((boundary - 1, boundary, boundary + 1))
            for raw_candidate in candidates:
                if integral_type and isinstance(raw_candidate, float) and not raw_candidate.is_integer():
                    continue
                candidate = int(raw_candidate) if integral_type else raw_candidate
                if all(self._satisfies(item, candidate) for item in predicates):
                    return candidate
            raise ValueError("infeasible_numeric_path")

        if short_type in {"boolean", "Boolean"}:
            for candidate in (True, False):
                if all(self._satisfies(item, candidate) for item in predicates):
                    return candidate
            raise ValueError("infeasible_boolean_path")

        if short_type == "String":
            candidates: list[Any] = [None, "", "x"]
            for item in predicates:
                constant = item.concrete_constant
                if isinstance(constant, str):
                    candidates.extend((constant, constant + "x"))
                if item.feature in {"length", "size"} and isinstance(constant, int):
                    length = self._numeric_choice(item.relation, constant)
                    if length >= 0:
                        candidates.append("x" * length)
            for candidate in candidates:
                if all(self._satisfies(item, candidate) for item in predicates):
                    return candidate
            raise ValueError("infeasible_string_path")

        if any(
            item.feature in {"length", "size", "emptiness"}
            or item.relation in {"is_empty", "is_non_empty"}
            for item in predicates
        ):
            if len(predicates) != 1:
                raise ValueError("container_joint_constraints_not_supported")
            predicate = predicates[0]
            if predicate.relation in {"is_empty", "is_non_empty"}:
                length = 0 if predicate.relation == "is_empty" else 1
            elif isinstance(predicate.concrete_constant, int):
                length = self._numeric_choice(predicate.relation, predicate.concrete_constant)
            else:
                raise ValueError("container_joint_constraints_not_supported")
            if length < 0:
                raise ValueError("negative_container_size")
            return self._container_binding(parameter_type, length, argument_index)

        if len(predicates) == 1:
            return self._binding_for(predicates[0], [parameter_type])
        raise ValueError("custom_object_joint_constraints_not_supported")

    def _container_binding(self, parameter_type: str, length: int, argument_index: int) -> ObjectBinding:
        erased = parameter_type.split("<", 1)[0].strip()
        short_type = erased.rsplit(".", 1)[-1]
        if erased.endswith("[]"):
            return ObjectBinding("", f"new {erased[:-2]}[{length}]")

        variable = f"__dp_arg_{argument_index}"
        loop = f"__dp_i_{argument_index}"
        if short_type in {"List", "Collection", "Iterable"}:
            setup = (
                f"java.util.List {variable} = new java.util.ArrayList();\n"
                f"for (int {loop} = 0; {loop} < {length}; {loop}++) {variable}.add({loop});"
            )
        elif short_type in {"Set", "SortedSet", "NavigableSet"}:
            setup = (
                f"java.util.Set {variable} = new java.util.HashSet();\n"
                f"for (int {loop} = 0; {loop} < {length}; {loop}++) {variable}.add({loop});"
            )
        elif short_type in {"Map", "SortedMap", "NavigableMap"}:
            setup = (
                f"java.util.Map {variable} = new java.util.HashMap();\n"
                f"for (int {loop} = 0; {loop} < {length}; {loop}++) {variable}.put({loop}, {loop});"
            )
        elif short_type in {"Queue", "Deque"}:
            setup = (
                f"java.util.Queue {variable} = new java.util.ArrayDeque();\n"
                f"for (int {loop} = 0; {loop} < {length}; {loop}++) {variable}.add({loop});"
            )
        else:
            raise ValueError("container_recipe_required")
        return ObjectBinding(setup, variable)

    @staticmethod
    def _satisfies(predicate: PrivatePredicate, candidate: Any) -> bool:
        relation = predicate.relation
        if relation == "is_absent":
            return candidate is None
        if relation == "is_present":
            return candidate is not None
        if relation == "is_true":
            return candidate is True
        if relation == "is_false":
            return candidate is False
        if relation == "is_empty":
            return candidate is not None and len(candidate) == 0
        if relation == "is_non_empty":
            return candidate is not None and len(candidate) > 0
        if candidate is None:
            return False
        observed = len(candidate) if predicate.feature in {"length", "size"} else candidate
        expected = predicate.concrete_constant
        comparisons = {
            "greater_than": lambda: observed > expected,
            "at_or_above": lambda: observed >= expected,
            "less_than": lambda: observed < expected,
            "at_or_below": lambda: observed <= expected,
            "equal_to": lambda: observed == expected,
            "different_from": lambda: observed != expected,
        }
        return comparisons.get(relation, lambda: False)()

    def _binding_for(self, predicate: PrivatePredicate, parameter_types: list[str]):
        index = int(predicate.subject.split("_")[-1])
        source_type = parameter_types[0] if len(parameter_types) == 1 else (
            parameter_types[index] if index < len(parameter_types) else ""
        )
        parameter_type = source_type.rsplit(".", 1)[-1]
        relation = predicate.relation
        constant = predicate.concrete_constant

        if predicate.feature == "object_state":
            return self._object_binding(predicate, source_type, index)

        if relation == "is_absent":
            return None
        if relation == "is_present":
            if parameter_type == "String":
                return "x"
            erased = source_type.split("<", 1)[0].strip()
            short_type = erased.rsplit(".", 1)[-1]
            if erased.endswith("[]") or short_type in {
                "List", "Collection", "Iterable", "Set", "SortedSet", "NavigableSet",
                "Map", "SortedMap", "NavigableMap", "Queue", "Deque",
            }:
                return self._container_binding(source_type, 0, index)
            return self._plain_object_binding(source_type, index)
        if relation in {"is_true", "is_false"} and parameter_type in {"boolean", "Boolean"}:
            return relation == "is_true"
        if relation in {"is_empty", "is_non_empty"} and parameter_type == "String":
            return "" if relation == "is_empty" else "x"

        if predicate.feature in {"length", "size"}:
            if not isinstance(constant, int):
                raise ValueError("non_integral_size_boundary")
            length = WitnessBinder._numeric_choice(relation, constant)
            if length < 0:
                raise ValueError("negative_container_size")
            if parameter_type == "String":
                return "x" * length
            raise ValueError("container_recipe_required")

        if isinstance(constant, (int, float)):
            return WitnessBinder._numeric_choice(relation, constant)
        if isinstance(constant, str) and relation == "equal_to":
            return constant
        if isinstance(constant, str) and relation == "different_from":
            return constant + "x"
        raise ValueError("unsupported_witness_constraint")

    def _object_binding(self, predicate: PrivatePredicate, class_name: str, argument_index: int) -> ObjectLeafBinding:
        class_obj = self._resolve_constructible_class(class_name)
        full_control_path = predicate.control_path or ""
        class_obj, control_path = self._resolve_nested_object_owner(
            class_obj,
            full_control_path,
        )
        accessor = re.fullmatch(r"(?:get|is)([A-Z][A-Za-z0-9_]*)\(\)", control_path)
        property_name = accessor.group(1) if accessor else control_path
        if not property_name or not re.fullmatch(r"[A-Za-z_$][\w$]*", property_name):
            raise ValueError("object_state_requires_public_control_path")

        setter_name = "set" + property_name[:1].upper() + property_name[1:]
        setters = [
            method for method in class_obj.methods
            if method.name_no_package == setter_name
            and len(method.parameters_list) == 1
            and is_visible_declaration(method.content)
        ]
        base_expression = self._zero_arg_constructor(class_obj)
        if setters and base_expression:
            leaf_value = self._leaf_binding(predicate, setters[0].parameters_list[0])
            return ObjectLeafBinding(
                leaf_value,
                setters[0].parameters_list[0],
                full_control_path,
            )

        field_statement = class_obj.fields.get(property_name[:1].lower() + property_name[1:])
        if field_statement and is_visible_declaration(field_statement) and base_expression:
            field_name = property_name[:1].lower() + property_name[1:]
            field_type = self._field_type(field_statement, field_name)
            leaf_value = self._leaf_binding(predicate, field_type)
            return ObjectLeafBinding(leaf_value, field_type, full_control_path)

        constructor_binding = self._constructor_property_binding(
            class_obj,
            property_name,
            predicate,
        )
        if constructor_binding:
            return constructor_binding
        builder_binding = self._builder_property_binding(
            class_obj,
            property_name,
            predicate,
        )
        if builder_binding:
            return builder_binding
        factory_binding = self._factory_property_binding(
            class_obj,
            property_name,
            predicate,
        )
        if factory_binding:
            return factory_binding
        raise ValueError("uncontrollable_object_state")

    def _resolve_nested_object_owner(self, class_obj, control_path: str):
        parts = control_path.split(".") if control_path else []
        if not parts:
            raise ValueError("object_state_requires_public_control_path")
        owner = class_obj
        for segment in parts[:-1]:
            accessor_name = segment[:-2] if segment.endswith("()") else segment
            accessors = [
                method for method in owner.methods
                if method.name_no_package == accessor_name
                and not method.parameters_list
                and is_visible_declaration(method.content)
            ]
            if len(accessors) != 1:
                raise ValueError("nested_object_state_requires_public_accessor")
            return_type = accessors[0].return_type
            try:
                owner = self._resolve_constructible_class(return_type)
            except ValueError as exc:
                raise ValueError("nested_object_state_type_unavailable") from exc
        return owner, parts[-1]

    def _plain_object_binding(self, class_name: str, argument_index: int) -> ObjectBinding:
        class_obj = self._resolve_constructible_class(class_name)
        expression = self._zero_arg_constructor(class_obj)
        if not expression:
            constructors = [
                item for item in class_obj.constructor
                if is_visible_declaration(item.content)
            ]
            constructors.sort(key=lambda item: len(item.parameters_list))
            if not constructors:
                raise ValueError("unconstructable_input")
            args = [self._default_java_expression(item) for item in constructors[0].parameters_list]
            expression = f"new {class_obj.name}({', '.join(args)})"
        variable = f"__dp_arg_{argument_index}"
        return ObjectBinding(f"{class_obj.name} {variable} = {expression};", variable)

    def _resolve_constructible_class(self, class_name: str):
        class_obj = self.class_map.get(class_name)
        if class_obj is None:
            matches = [item for item in self.class_map.values() if item.name_no_package == class_name.rsplit(".", 1)[-1]]
            class_obj = matches[0] if len(matches) == 1 else None
        if class_obj is None:
            raise ValueError("custom_class_not_found")
        if class_obj.is_interface or " abstract " in f" {class_obj.content.split('{', 1)[0]} ":
            implementations = [item for item in class_obj.son_classes if not item.is_interface]
            if len(implementations) != 1:
                raise ValueError("ambiguous_concrete_implementation")
            class_obj = implementations[0]
        return class_obj

    @staticmethod
    def _zero_arg_constructor(class_obj) -> Optional[str]:
        visible = [item for item in class_obj.constructor if is_visible_declaration(item.content)]
        if not class_obj.constructor:
            return f"new {class_obj.name}()"
        if any(not item.parameters_list for item in visible):
            return f"new {class_obj.name}()"
        return None

    def _constructor_property_binding(self, class_obj, property_name, predicate) -> Optional[ObjectLeafBinding]:
        wanted = property_name[:1].lower() + property_name[1:]
        constructors = [item for item in class_obj.constructor if is_visible_declaration(item.content)]
        constructors.sort(key=lambda item: len(item.parameters_list))
        for constructor in constructors:
            names = self._formal_parameter_names(constructor)
            if wanted not in names:
                continue
            for name, type_name in zip(names, constructor.parameters_list):
                if name == wanted:
                    return ObjectLeafBinding(
                        self._leaf_binding(predicate, type_name),
                        type_name,
                        predicate.control_path or "",
                    )
        return None

    def _builder_property_binding(self, class_obj, property_name, predicate) -> Optional[ObjectLeafBinding]:
        builder_entries = [
            method for method in class_obj.methods
            if is_visible_declaration(method.content)
            and method.name_no_package.lower() == "builder"
            and not method.parameters_list
        ]
        wanted = property_name[:1].lower() + property_name[1:]
        for entry in builder_entries:
            builder_class = self.class_map.get(entry.return_type)
            if builder_class is None:
                matches = [
                    item for item in self.class_map.values()
                    if item.name_no_package.rsplit(".", 1)[-1] == entry.return_type.rsplit(".", 1)[-1]
                ]
                builder_class = matches[0] if len(matches) == 1 else None
            if builder_class is None:
                continue
            has_build = any(
                method.name_no_package == "build"
                and not method.parameters_list
                and is_visible_declaration(method.content)
                and method.return_type.rsplit(".", 1)[-1] == class_obj.name_no_package
                for method in builder_class.methods
            )
            if not has_build:
                continue
            for method in builder_class.methods:
                if (
                    len(method.parameters_list) == 1
                    and is_visible_declaration(method.content)
                    and self._method_controls_property(method.name_no_package, wanted)
                ):
                    type_name = method.parameters_list[0]
                    return ObjectLeafBinding(
                        self._leaf_binding(predicate, type_name),
                        type_name,
                        predicate.control_path or "",
                    )
        return None

    def _factory_property_binding(self, class_obj, property_name, predicate) -> Optional[ObjectLeafBinding]:
        wanted = property_name[:1].lower() + property_name[1:]
        for method in class_obj.methods:
            declaration = method.content.split("{", 1)[0]
            if (
                not is_visible_declaration(method.content)
                or not re.search(r"\bstatic\b", declaration)
                or method.return_type.rsplit(".", 1)[-1] != class_obj.name_no_package
            ):
                continue
            names = self._formal_parameter_names(method)
            for index, type_name in enumerate(method.parameters_list):
                name_matches = index < len(names) and names[index] == wanted
                method_matches = len(method.parameters_list) == 1 and self._method_controls_property(
                    method.name_no_package,
                    wanted,
                )
                if name_matches or method_matches:
                    return ObjectLeafBinding(
                        self._leaf_binding(predicate, type_name),
                        type_name,
                        predicate.control_path or "",
                    )
        return None

    @staticmethod
    def _method_controls_property(method_name: str, property_name: str) -> bool:
        lowered = method_name.lower()
        property_lower = property_name.lower()
        return lowered in {
            property_lower,
            "set" + property_lower,
            "with" + property_lower,
            "of" + property_lower,
            "from" + property_lower,
        }

    @staticmethod
    def _formal_parameter_names(method) -> list[str]:
        parameters = method.node.child_by_field_name("parameters") if method.node else None
        if parameters is None:
            return []
        result = []
        for child in parameters.named_children:
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                result.append(name_node.text.decode("utf-8"))
        return result

    def _leaf_binding(self, predicate: PrivatePredicate, type_name: str):
        leaf = PrivatePredicate(
            subject="ARG_0",
            feature="value",
            relation=predicate.relation,
            concrete_constant=predicate.concrete_constant,
            supported=predicate.supported,
        )
        return self._binding_for(leaf, [type_name])

    def _default_java_expression(self, type_name: str) -> str:
        short = type_name.rsplit(".", 1)[-1]
        if short in {"byte", "short", "int", "long", "Byte", "Short", "Integer", "Long"}:
            return "0"
        if short in {"float", "double", "Float", "Double"}:
            return "0.0"
        if short in {"boolean", "Boolean"}:
            return "false"
        if short in {"char", "Character"}:
            return "'\\0'"
        if short == "String":
            return '""'
        class_obj = self.class_map.get(type_name)
        if class_obj is not None:
            expression = self._zero_arg_constructor(class_obj)
            if expression:
                return expression
        return "null"

    @staticmethod
    def _field_type(statement: str, field_name: str) -> str:
        head = statement.split("=", 1)[0].replace(";", " ")
        tokens = [item for item in head.split() if item not in {"public", "protected", "static", "final", "volatile", "transient"}]
        if len(tokens) >= 2 and tokens[-1] == field_name:
            return tokens[-2]
        return "Object"

    @staticmethod
    def _numeric_choice(relation: str, boundary):
        if relation == "greater_than":
            return boundary + 1
        if relation == "at_or_above":
            return boundary
        if relation == "less_than":
            return boundary - 1
        if relation == "at_or_below":
            return boundary
        if relation == "equal_to":
            return boundary
        if relation == "different_from":
            return boundary + 1
        raise ValueError("unsupported_numeric_relation")

    @staticmethod
    def _java_literal(value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, (int, float)):
            return repr(value)
        raise ContractError(f"Cannot render witness value of type {type(value).__name__}")
