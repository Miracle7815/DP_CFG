import json
import os
import re

from ..config import CONFIG


DATA_INFO_PATH = CONFIG["data_info_path"]

def get_method_info(project_name):
    project_group = project_name.split('_')[0]
    info_file_path = os.path.join(DATA_INFO_PATH , project_group , project_name , "buggy_fix_info.json")
    with open(info_file_path , 'r' , encoding='utf-8') as f:
        info_json = json.load(f)
    
    # print(info_json)
    return info_json

def process_method_info(project_name , project_root):
    info_json = get_method_info(project_name)
    fix_changes = info_json['fixing_changes']
    class_method_map = {}

    for fix_change in fix_changes:
        for change_class in fix_change['changed_class']:
            change_class_name = change_class.replace(project_root + "/" , "")
            change_class_name = change_class_name.replace('/' , '.')
            change_class_name = change_class_name.replace('.java' , "")
            if change_class_name not in class_method_map:
                class_method_map[change_class_name] = []
        
        for change_function in fix_change['changed_functions'][0]['qualified_names']:
            if change_function not in fix_change['changed_functions'][1]['qualified_names']:
                continue
            change_class_name = change_function.split(':')[0]
            change_method_name = change_function.split(":")[1]
            method_parameter = change_function.split(":")[2][1 : -1]
            return_type = change_function.split(":")[3]
            if (change_method_name , method_parameter , return_type) not in class_method_map[change_class_name]:
                class_method_map[change_class_name].append((change_method_name , method_parameter , return_type))

    return class_method_map

def _split_parameters(parameters: str) -> list[str]:
    parameters = parameters.strip()
    if not parameters:
        return []

    parts = []
    current = []
    depth = 0
    for char in parameters:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def _normalise_declared_type(parameter: str) -> str:
    parameter = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", parameter).strip()
    parameter = re.sub(r"\b(final|volatile|transient)\b\s*", "", parameter).strip()
    tokens = parameter.split()
    if len(tokens) > 1:
        parameter = " ".join(tokens[:-1])
    return re.sub(r"\s+", "", parameter).replace("...", "[]")


def _types_match(expected: str, actual: str) -> bool:
    expected = _normalise_declared_type(expected)
    actual = _normalise_declared_type(actual)
    if expected == actual:
        return True
    if "." not in expected:
        return expected == actual.rsplit(".", 1)[-1]
    return False


def get_callable_method(all_packages , class_name , method_info):
    package_name = ".".join(class_name.split('.')[:-1])
    target_package = None
    for package in all_packages:
        if package_name == package.name:
            target_package = package
            break
    
    if target_package is None:
        return None

    target_class = None
    for classs in target_package.classes:
        if classs.name == class_name:
            target_class = classs
            break
    
    if target_class is None:
        return None

    expected_parameters = _split_parameters(method_info[1])
    candidates = []
    for method in target_class.methods:
        if method.name_no_package != method_info[0]:
            continue
        if len(method.parameters_list) != len(expected_parameters):
            continue
        if not all(
            _types_match(expected, actual)
            for expected, actual in zip(expected_parameters, method.parameters_list)
        ):
            continue
        if not _types_match(method_info[2], method.return_type):
            continue
        candidates.append(method)

    if len(candidates) == 1:
        return candidates[0]
    return None
