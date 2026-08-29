from types import SimpleNamespace

from generate_for_buggy.utils.public_api import class_skeleton, construction_options


def test_source_free_context_omits_private_members_bodies_and_initializers():
    public_method = SimpleNamespace(
        signature="demo.Widget#build()",
        name_no_package="build",
        parameters_list=[],
        return_type="demo.Widget",
        content="public static Widget build() { return secret; }",
    )
    private_method = SimpleNamespace(
        signature="demo.Widget#secret()",
        name_no_package="secret",
        parameters_list=[],
        return_type="int",
        content="private int secret() { return 7331; }",
    )
    widget = SimpleNamespace(
        name="demo.Widget",
        name_no_package="Widget",
        is_interface=False,
        is_enum=False,
        father_class_name=None,
        implement_interfaces={},
        fields={
            "visible": "public static final int visible = 7331;",
            "hidden": "private int hidden = 9001;",
        },
        constructor=set(),
        methods=[public_method, private_method],
        son_classes=set(),
    )
    skeleton = class_skeleton(widget)
    assert "7331" not in skeleton
    assert "9001" not in skeleton
    assert "return secret" not in skeleton
    assert "secret()" not in skeleton
    assert "public static final int visible;" in skeleton

    options = construction_options(widget)
    assert options["factories"] == ["demo.Widget#build()"]
