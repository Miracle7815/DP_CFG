from types import SimpleNamespace

from generate_for_buggy.agents.symbolic_coverage import (
    CoverageSanitizer,
    ObjectBinding,
    ObjectLeafBinding,
    SymbolicPathBuilder,
    WitnessBinder,
)
from generate_for_buggy.contracts import PrivatePathFormula, PrivatePredicate


def target_method():
    return SimpleNamespace(
        signature="demo.Calc#classify(int)",
        javadoc="Returns a documented category. Preserves the input contract.",
    )


def formula(*predicates):
    return PrivatePathFormula(
        goal_id="CG_private",
        target_node_id=9,
        target_outcome=True,
        path_node_ids=(4, 9),
        path_outcomes=(True, True),
        predicates=tuple(predicates),
        seed_scenario_id="S1",
    )


def test_private_constant_is_replaced_by_opaque_anchor():
    private = formula(PrivatePredicate("ARG_0", "value", "greater_than", 41))
    public = CoverageSanitizer().sanitize(target_method(), private).to_public_dict()
    serialized = repr(public)
    assert "κ0" in serialized
    assert "41" not in serialized
    assert "statement" not in serialized
    assert "line" not in serialized


def test_numeric_path_constraints_are_solved_jointly():
    private = formula(
        PrivatePredicate("ARG_0", "value", "greater_than", 10),
        PrivatePredicate("ARG_0", "value", "less_than", 14),
    )
    binder = WitnessBinder()
    record = binder.register(private, ["int"])
    assert 10 < record.bindings["ARG_0"] < 14
    assert record.failure_reason is None


def test_infeasible_path_fails_closed_without_partial_binding():
    private = formula(
        PrivatePredicate("ARG_0", "value", "greater_than", 10),
        PrivatePredicate("ARG_0", "value", "less_than", 9),
    )
    record = WitnessBinder().register(private, ["int"])
    assert record.bindings == {}
    assert record.failure_reason == "infeasible_numeric_path"


def test_parameter_equality_and_difference_are_solved_jointly():
    equal_formula = formula(PrivatePredicate(
        "ARG_0", "value", "equal_to", peer_subject="ARG_1"
    ))
    equal = WitnessBinder().register(equal_formula, ["int", "int"])
    assert equal.bindings["ARG_0"] == equal.bindings["ARG_1"]

    different_formula = formula(PrivatePredicate(
        "ARG_0", "value", "different_from", peer_subject="ARG_1"
    ))
    different = WitnessBinder().register(different_formula, ["String", "String"])
    assert different.bindings["ARG_0"] != different.bindings["ARG_1"]


def test_custom_object_equality_uses_one_shared_public_recipe():
    constructor = SimpleNamespace(content="public Person() {}", parameters_list=[])
    person = SimpleNamespace(
        name="demo.Person",
        name_no_package="Person",
        methods=[],
        constructor=[constructor],
        fields={},
        is_interface=False,
        son_classes=set(),
        content="public class Person {}",
    )
    private = formula(PrivatePredicate(
        "ARG_0", "value", "equal_to", peer_subject="ARG_1"
    ))
    binder = WitnessBinder({"demo.Person": person})
    record = binder.register(private, ["demo.Person", "demo.Person"])
    assert record.bindings["ARG_0"] == record.bindings["ARG_1"]
    materialized = binder.materialize_test_method(
        "CG_private",
        "__DP_WITNESS_SETUP_ARG_0__\n__DP_WITNESS_SETUP_ARG_1__\n"
        "consume(__DP_WITNESS_ARG_0__, __DP_WITNESS_ARG_1__);",
    )
    assert materialized.count("new demo.Person()") == 1
    assert "consume(__dp_arg_0, __dp_arg_0)" in materialized


def test_array_and_collection_length_constraints_create_local_recipes():
    private = formula(PrivatePredicate("ARG_0", "length", "equal_to", 3))
    array_binder = WitnessBinder()
    array_record = array_binder.register(private, ["int[]"])
    assert isinstance(array_record.bindings["ARG_0"], ObjectBinding)
    array_method = array_binder.materialize_test_method(
        "CG_private",
        "__DP_WITNESS_SETUP_ARG_0__ consume(__DP_WITNESS_ARG_0__);",
    )
    assert "new int[3]" in array_method

    collection_binder = WitnessBinder()
    collection_record = collection_binder.register(private, ["java.util.List<String>"])
    assert isinstance(collection_record.bindings["ARG_0"], ObjectBinding)
    collection_method = collection_binder.materialize_test_method(
        "CG_private",
        "__DP_WITNESS_SETUP_ARG_0__ consume(__DP_WITNESS_ARG_0__);",
    )
    assert "java.util.ArrayList" in collection_method
    assert "< 3" in collection_method

    empty_formula = formula(PrivatePredicate("ARG_0", "emptiness", "is_empty"))
    empty_record = WitnessBinder().register(empty_formula, ["java.util.List<String>"])
    assert isinstance(empty_record.bindings["ARG_0"], ObjectBinding)


def test_simple_affine_condition_is_reduced_to_argument_constraint():
    predicates = SymbolicPathBuilder()._parse_expression(
        "local < 8",
        {"x": "ARG_0"},
        {"local": "x + 2"},
        True,
    )
    assert len(predicates) == 1
    assert predicates[0].subject == "ARG_0"
    assert predicates[0].relation == "less_than"
    assert predicates[0].concrete_constant == 6


def test_not_and_or_conditions_are_transformed_soundly():
    builder = SymbolicPathBuilder()
    predicates = builder._parse_expression(
        "!(x > 3 || y == 2)",
        {"x": "ARG_0", "y": "ARG_1"},
        {},
        True,
    )
    assert [item.relation for item in predicates] == ["at_or_below", "different_from"]
    assert [item.connective for item in predicates] == ["and", "and"]


def test_receiver_state_is_abstracted_and_bound_through_public_api():
    predicates = SymbolicPathBuilder()._parse_expression(
        "age > 18",
        {},
        {},
        True,
        {"age"},
    )
    assert predicates[0].subject == "RECEIVER"
    setter = SimpleNamespace(
        name_no_package="setAge",
        content="public void setAge(int age) {}",
        parameters_list=["int"],
    )
    constructor = SimpleNamespace(content="public Person() {}", parameters_list=[])
    person = SimpleNamespace(
        name="demo.Person", name_no_package="Person", methods=[setter],
        constructor=[constructor], fields={}, is_interface=False,
        son_classes=set(), content="public class Person {}",
    )
    private = formula(*predicates)
    binder = WitnessBinder({"demo.Person": person})
    record = binder.register(private, [], receiver_type="demo.Person")
    assert isinstance(record.bindings["RECEIVER"], ObjectLeafBinding)
    assert binder.public_slots("CG_private")[0]["placeholder"] == "__DP_WITNESS_LEAF_RECEIVER__"
    public = CoverageSanitizer().sanitize(target_method(), private).to_public_dict()
    assert "age" not in repr(public).lower()
    assert "18" not in repr(public)


def test_object_state_uses_public_setter_but_public_goal_hides_property_name():
    constructor = SimpleNamespace(content="public Person() {}", parameters_list=[])
    setter = SimpleNamespace(
        name_no_package="setAge",
        content="public void setAge(int age) {}",
        parameters_list=["int"],
    )
    person = SimpleNamespace(
        name="demo.Person",
        name_no_package="Person",
        methods=[setter],
        constructor=[constructor],
        fields={},
        is_interface=False,
        son_classes=set(),
        content="public class Person {}",
    )
    private = formula(PrivatePredicate(
        "ARG_0", "object_state", "greater_than", 17, control_path="getAge()"
    ))
    binder = WitnessBinder({"demo.Person": person})
    record = binder.register(private, ["demo.Person"])
    assert isinstance(record.bindings["ARG_0"], ObjectLeafBinding)
    slots = binder.public_slots("CG_private")
    assert slots[0]["role"] == "custom_object_leaf"
    assert slots[0]["placeholder"] == "__DP_WITNESS_LEAF_ARG_0__"
    materialized = binder.materialize_test_method(
        "CG_private",
        "Person person = new Person(); person.setAge(__DP_WITNESS_LEAF_ARG_0__);",
    )
    assert "__DP_WITNESS" not in materialized
    assert binder.vault.get("CG_private").construction_recipe["kind"] == "generated_public_api_recipe"

    public = CoverageSanitizer().sanitize(target_method(), private).to_public_dict()
    assert "getAge" not in repr(public)
    assert "17" not in repr(public)


def test_immutable_object_builder_route_is_recognized_without_exposing_leaf_value():
    builder_entry = SimpleNamespace(
        name_no_package="builder",
        content="public static Builder builder() {}",
        parameters_list=[],
        return_type="demo.Person.Builder",
    )
    immutable = SimpleNamespace(
        name="demo.Person",
        name_no_package="Person",
        methods=[builder_entry],
        constructor=[],
        fields={},
        is_interface=False,
        son_classes=set(),
        content="public final class Person {}",
    )
    age = SimpleNamespace(
        name_no_package="age",
        content="public Builder age(int age) {}",
        parameters_list=["int"],
        return_type="demo.Person.Builder",
    )
    build = SimpleNamespace(
        name_no_package="build",
        content="public Person build() {}",
        parameters_list=[],
        return_type="demo.Person",
    )
    builder_class = SimpleNamespace(
        name="demo.Person.Builder",
        name_no_package="Person.Builder",
        methods=[age, build],
        constructor=[],
        fields={},
        is_interface=False,
        son_classes=set(),
        content="public class Builder {}",
    )
    private = formula(PrivatePredicate(
        "ARG_0", "object_state", "at_or_above", 21, control_path="getAge()"
    ))
    binder = WitnessBinder({
        "demo.Person": immutable,
        "demo.Person.Builder": builder_class,
    })
    record = binder.register(private, ["demo.Person"])
    assert isinstance(record.bindings["ARG_0"], ObjectLeafBinding)
    assert record.failure_reason is None


def test_nested_object_leaf_is_bound_through_public_accessors():
    address_getter = SimpleNamespace(
        name_no_package="getAddress",
        content="public Address getAddress() {}",
        parameters_list=[],
        return_type="demo.Address",
    )
    person = SimpleNamespace(
        name="demo.Person",
        name_no_package="Person",
        methods=[address_getter],
        constructor=[],
        fields={},
        is_interface=False,
        son_classes=set(),
        content="public class Person {}",
    )
    address_constructor = SimpleNamespace(content="public Address() {}", parameters_list=[])
    zip_setter = SimpleNamespace(
        name_no_package="setZip",
        content="public void setZip(int zip) {}",
        parameters_list=["int"],
    )
    address = SimpleNamespace(
        name="demo.Address",
        name_no_package="Address",
        methods=[zip_setter],
        constructor=[address_constructor],
        fields={},
        is_interface=False,
        son_classes=set(),
        content="public class Address {}",
    )
    private = formula(PrivatePredicate(
        "ARG_0",
        "object_state",
        "equal_to",
        5,
        control_path="getAddress().getZip()",
    ))
    binder = WitnessBinder({"demo.Person": person, "demo.Address": address})
    record = binder.register(private, ["demo.Person"])
    assert isinstance(record.bindings["ARG_0"], ObjectLeafBinding)
    assert record.failure_reason is None


def test_interface_with_multiple_implementations_fails_closed():
    implementation_a = SimpleNamespace(
        name="demo.A", name_no_package="A", is_interface=False,
        methods=[], constructor=[], fields={}, son_classes=set(), content="public class A {}",
    )
    implementation_b = SimpleNamespace(
        name="demo.B", name_no_package="B", is_interface=False,
        methods=[], constructor=[], fields={}, son_classes=set(), content="public class B {}",
    )
    interface = SimpleNamespace(
        name="demo.Contract",
        name_no_package="Contract",
        methods=[],
        constructor=[],
        fields={},
        is_interface=True,
        son_classes=[implementation_a, implementation_b],
        content="public interface Contract {}",
    )
    private = formula(PrivatePredicate("ARG_0", "value", "is_present"))
    record = WitnessBinder({"demo.Contract": interface}).register(private, ["demo.Contract"])
    assert record.bindings == {}
    assert record.failure_reason == "ambiguous_concrete_implementation"


def test_unsupported_predicate_produces_no_witness():
    private = formula(PrivatePredicate(
        "UNRESOLVED", "unsupported", "unknown", supported=False
    ))
    record = WitnessBinder().register(private, ["int"])
    assert record.bindings == {}
    assert record.failure_reason == "unsupported_constraint"
