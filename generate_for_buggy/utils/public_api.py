import re
from typing import Iterable


_VISIBLE_MODIFIERS = ("public ", "protected ")


def _declaration_head(content: str) -> str:
    return content.split("{", 1)[0].strip()


def is_visible_declaration(content: str, allow_package_private: bool = False) -> bool:
    head = _declaration_head(content)
    if re.search(r"\bprivate\b", head):
        return False
    if allow_package_private:
        return True
    return any(re.search(rf"\b{modifier.strip()}\b", head) for modifier in _VISIBLE_MODIFIERS)


def sanitise_field_declaration(statement: str) -> str:
    head = statement.split("=", 1)[0].strip()
    head = head.rstrip(";").strip()
    return f"{head};" if head else ""


def class_skeleton(class_obj, allow_package_private: bool = False) -> str:
    kind = "interface" if class_obj.is_interface else "enum" if class_obj.is_enum else "class"
    lines = [f"{kind} {class_obj.name}"]

    if class_obj.father_class_name:
        lines.append(f"  extends: {class_obj.father_class_name}")
    if class_obj.implement_interfaces:
        lines.append("  implements: " + ", ".join(sorted(class_obj.implement_interfaces)))

    fields = []
    for _, statement in sorted(class_obj.fields.items()):
        if is_visible_declaration(statement, allow_package_private):
            declaration = sanitise_field_declaration(statement)
            if declaration:
                fields.append(declaration)
    if fields:
        lines.append("  fields:")
        lines.extend(f"    {item}" for item in fields)

    constructors = [
        constructor.signature
        for constructor in class_obj.constructor
        if is_visible_declaration(constructor.content, allow_package_private)
    ]
    if constructors:
        lines.append("  constructors:")
        lines.extend(f"    {item}" for item in sorted(constructors))

    methods = [
        method.signature
        for method in class_obj.methods
        if is_visible_declaration(method.content, allow_package_private)
    ]
    if methods:
        lines.append("  methods:")
        lines.extend(f"    {item}" for item in sorted(methods))
    return "\n".join(lines)


def construction_options(class_obj, allow_package_private: bool = False) -> dict:
    constructors = [
        constructor.signature
        for constructor in class_obj.constructor
        if is_visible_declaration(constructor.content, allow_package_private)
    ]
    factories = []
    builders = []
    setters = []
    for method in class_obj.methods:
        if not is_visible_declaration(method.content, allow_package_private):
            continue
        head = _declaration_head(method.content)
        if method.name_no_package.startswith("set") and len(method.parameters_list) == 1:
            setters.append(method.signature)
        if re.search(r"\bstatic\b", head) and (
            method.return_type == class_obj.name
            or method.return_type.rsplit(".", 1)[-1] == class_obj.name_no_package
        ):
            factories.append(method.signature)
        if method.name_no_package.lower() == "builder" or "Builder" in method.return_type:
            builders.append(method.signature)
    return {
        "class": class_obj.name,
        "constructors": sorted(constructors),
        "factories": sorted(factories),
        "builders": sorted(builders),
        "setters": sorted(setters),
        "implementations": sorted(item.name for item in class_obj.son_classes),
    }


def method_contracts(methods: Iterable) -> list[dict]:
    result = []
    for method in methods:
        result.append({
            "signature": method.signature,
            "javadoc": method.javadoc or "No javadoc available.",
        })
    return result
