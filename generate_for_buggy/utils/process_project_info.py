import os
import json

DATA_INFO_PATH = "/home/miracle/DP_CFG/data_info"

def get_method_info(project_name):
    project_group = project_name.split('_')[0]
    info_file_path = os.path.join(DATA_INFO_PATH , project_group , project_name , "buggy_fix_info.json")
    with open(info_file_path , 'r' , encoding='utf-8') as f:
        info_json = json.load(f)
    
    print(info_json)
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

def get_callable_method(all_packages , class_name , method_info):
    package_name = ".".join(class_name.split('.')[:-1])
    target_package = None
    for package in all_packages:
        if package_name == package.package_name:
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

    target_method = None
    for method in target_class.methods:
        if method.name_no_package == method_info[0]:
            if all(method_parameter.strip() in method.parameters_string for method_parameter in method_info[1].split(',')) and method_info[2].strip() in method.return_type:
                target_method = method
                break
    
    return target_method