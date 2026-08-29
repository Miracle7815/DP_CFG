from types import SimpleNamespace

from generate_for_buggy.utils.process_project_info import get_callable_method


def test_target_resolution_requires_exact_overload_and_return_type():
    int_method = SimpleNamespace(
        name_no_package="parse",
        parameters_list=["int"],
        return_type="boolean",
    )
    string_method = SimpleNamespace(
        name_no_package="parse",
        parameters_list=["java.lang.String"],
        return_type="boolean",
    )
    target_class = SimpleNamespace(
        name="demo.Parser",
        methods=[int_method, string_method],
    )
    package = SimpleNamespace(name="demo", classes=[target_class])

    resolved = get_callable_method(
        [package], "demo.Parser", ("parse", "java.lang.String", "boolean")
    )
    assert resolved is string_method
    assert get_callable_method(
        [package], "demo.Parser", ("parse", "double", "boolean")
    ) is None
    assert get_callable_method(
        [package], "demo.Parser", ("parse", "int", "int")
    ) is None
