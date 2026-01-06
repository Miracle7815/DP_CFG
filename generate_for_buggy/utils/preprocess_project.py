import os
from queue import Queue
import chardet
from ..config import CONFIG , logger
from .static_analysis import find_package_name_use_source_code , add_file_classes_and_methods_to_package , get_package_import , find_father_class , find_call_method , find_method_java_doc
from ..basic_class.base_package import Package
from ..basic_class.base_method import Method
from ..basic_class.base_file import File
from ..basic_class.base_class import Class
from ..basic_class.base_test_program import TestProgram
from ..cfg.src.comex.codeviews.combined_graph.combined_driver import CombinedDriver
# from .cfg_generate import generate_cfg_for_file

def find_java_files(directory):
    java_files = []

    for root , _ , files in os.walk(directory):
        for file in files:
            if file.endswith('.java'):
                file_path = os.path.join(root , file)
                java_files.append(file_path)

    return java_files

def get_packages(project_path , src_path):
    all_files = []     # all files
    all_packages_map = {}        # package_name -> Package
    method_map = {}    
    class_map = {}      # class_name -> Class

    # get all classes and methods 
    all_package_path = os.path.join(project_path , src_path)
    java_files = find_java_files(all_package_path)
    
    for java_path in java_files:
    #     print(java_path)
        try:
            with open(java_path, 'rb') as file:   # 二进制模式读文件
                raw_data = file.read()
            # detect encode
            result = chardet.detect(raw_data)   # 通过chardet猜测编码方式再进行解码
            encoding = result['encoding']
            java_content = raw_data.decode(encoding)
        except UnicodeDecodeError as e:
            print(f"UnicodeDecodeError: {e}")
            continue
        except Exception as e:
            print(f"An error occurred: {e}")
            continue
        
        package_name = find_package_name_use_source_code(java_content)
        # print(package_name)

        if package_name is None:
            continue
            
        single_package_path = package_name.replace('.' , os.path.sep)
        single_package_path = os.path.join(src_path , single_package_path)

        if package_name not in all_packages_map:
            single_package = Package(package_name , single_package_path)
            all_packages_map[package_name] = single_package
        
        single_package = all_packages_map[package_name]
        single_file = File(java_path , java_content , single_package)    # path content belong_package
        single_package.add_file(single_file)
        all_files.append(single_file)

        add_file_classes_and_methods_to_package(single_package , single_file)  # get classes and methods in file

    all_packages = list(all_packages_map.values())
    

    # parse files
    for single_file in all_files:
        java_content = single_file.content
        get_package_import(single_file , java_content , all_packages)  # single_file.import_map record the imported class

        for classs in single_file.belong_package.classes:      # class in the same package
            single_file.import_map[classs.name_no_package] = classs.name        # name_no_package -> name
        for classs in single_file.classes:
            class_map[classs.name] = classs
            classs.import_map = single_file.import_map
            for method in classs.methods:
                method.import_map = classs.import_map

    for single_file in all_files:
        for classs in single_file.classes:
            # print(classs.name + ": ")
            classs.belong_file = single_file
            for method in classs.methods:
                method.belong_file = single_file
                # print(method.name)
                if method.return_type in method.import_map:
                    method.return_type = method.import_map[method.return_type]
                if method.return_type in class_map:
                    method.return_type = class_map[method.return_type].name

                new_parameters_list = []
                for parameter in method.parameters_list:
                    if parameter in method.import_map:
                        parameter = method.import_map[parameter]
                    new_parameters_list.append(parameter)
                method.parameters_list = new_parameters_list
                # print(method.name , method.return_type , method.parameters_list)
                method.set_method_signature()     # signature = belong_package.name#belong_class.name_no_package#method.name_no_package({parameters_string})
    
    # father class
    for single_file in all_files:
        for classs in single_file.classes:
            find_father_class(classs.node , classs)

    for single_file in all_files:
        for classs in single_file.classes:
            if classs.father_class_name in class_map:
                father_class = class_map[classs.father_class_name]
                classs.father_class = father_class
                father_class.son_classes.add(classs)
    
    for single_file in all_files:
        for classs in single_file.classes:
            if not classs.is_interface:
                for method in classs.methods:
                    arguments_list = tuple(method.parameters_list)
                    method_map[(method.name , arguments_list)]= method  # method_map = (method_name , parameters_list) : Metod
    
    top_class = [classs for classs in class_map.values() if classs.father_class == None]   # 没有父类的类
    class_queue = Queue()
    for item in top_class:
        class_queue.put(item)
            
    # inherent methods to son classes
    while not class_queue.empty():
        classs = class_queue.get()
        for son_class in classs.son_classes:
            class_queue.put(son_class)
            for method in classs.methods:
                # deepcopy
                son_class_name = son_class.name
                new_method_name = son_class_name + '.' + method.name_no_package
                
                new_method = Method(method.name_no_package, new_method_name, son_class.belong_package, son_class, method.parameters_list, method.parameters_modifiers_list , method.content, method.return_type, method.node)
                
                new_method.import_map = method.import_map
                new_method.is_target = method.is_target
                new_method.is_static = method.is_static

                new_method.set_method_signature()
                arguments_list = tuple(method.parameters_list)
                
                flag = True
                # override
                for son_method in son_class.methods:
                    if son_method.signature == new_method.signature:
                        flag = False
                if flag:
                    son_class.add_method(method)
                    method_map[(new_method_name, arguments_list)]= method  # method_map = (method_name , parameters_list) : Metod
    
    all_packages = list(all_packages_map.values())

    # method line range
    for single_package in all_packages:
        for method in single_package.methods:
            method_node = method.node
            
            for i in range(method_node.start_point[0], method_node.end_point[0] + 1):
                method.line_range.add(i + 1)
                method.line_number.add(str(i + 1))
            name = method.name
            arguments_list = tuple(method.parameters_list)
            method_map[(name, arguments_list)]= method

    # cfg dfg
    # for sigle_file in all_files:
    
    for single_file in all_files:
        try:
            file_content = single_file.content
            cfg_driver = CombinedDriver('java' , file_content)
            cfg_obj = cfg_driver.file_obj
            cls_objs = cfg_obj['class_objects']

            single_file.file_obj = cfg_obj
            single_file.node_id_to_line_number = cfg_driver.node_id_to_line_number
            single_file.line_number_to_node_id = cfg_driver.line_number_to_node_id

            for cls_obj in cls_objs:
                exist_class_flag = False
                for classs in single_file.classes:
                    if classs.name_no_package == cls_obj['class_declaration']['name']:
                        exist_class_flag = True
                        break
                if exist_class_flag:
                    for method_under_test in cls_obj["methods_under_test"]:
                        method_name = method_under_test["method_declaration"]["name"]
                        method_declaration_line = cfg_driver.node_id_to_line_number[method_under_test["method_declaration"]["id"]][0]
                        for method in single_file.methods:
                            if method.name_no_package == method_name and method_declaration_line in method.line_range:
                                method.cfg_info = method_under_test
                                break
        except Exception as e:
            single_file.file_obj = None
            single_file.node_id_to_line_number = None
            single_file.line_number_to_node_id = None

    all_packages = list(all_packages_map.values()) 


    for single_package in all_packages:
        find_call_method(single_package , method_map , class_map)
        # package_name = single_package.name

    for single_file in all_files:
        find_method_java_doc(single_file)
    # print(len(all_files))
    # for single_file in all_files:
    #     if single_file.file_name == "NumberUtils.java":
    #         print(single_file.file_name + ":")
    #         # generate_cfg_for_file(project_path , single_file)
    #         # for classs in single_file.classes:
    #         #     print(classs.name_no_package , classs.father_class_name)
    #         #     for method in classs.methods:
    #         #         print("    " + method.signature , method.return_type , method.line_range , method.import_map)
    
    all_packages = list(all_packages_map.values()) 

    return all_packages, method_map, class_map



def setup_single_package(all_methods_in_package , method_map , class_map):
    for single_method in all_methods_in_package:
        for name_and_arguments_list in single_method.called_method_name:
            called_methods = []
            if name_and_arguments_list in method_map:
                called_methods = [method_map[name_and_arguments_list]]
            # 粗粒度 已经实现对于名字相同的方法，通过参数列表长度锁定
            else:
                called_class_name = '.'.join(name_and_arguments_list[0].split('.')[:-1])
                called_class = class_map.get(called_class_name)
                if called_class is None:
                    continue
                method_name = name_and_arguments_list[0]
                arguments_list = list(name_and_arguments_list[1])
                arguments_list_len = len(arguments_list)
                called_methods = []
                for maybe_method in called_class.methods:   # 把所有可能的method加入call_methods（根据方法名和参数数量）
                    if method_name == maybe_method.name and arguments_list_len == len(maybe_method.parameters_list):
                        called_methods.append(maybe_method)
                    
            for called_method in called_methods:
                single_method.add_called_method(called_method)   # 添加调用关系
                called_method.add_callee_method(single_method)
                called_class_name = '.'.join(name_and_arguments_list[0].split('.')[:-1])
                called_class = class_map.get(called_class_name)
                single_method.add_called_method_and_class(single_method, called_class)
                        
        # 与分支相关的方法
        for name_and_arguments_list in single_method.branch_related_called_methods_name:
            branch_related_called_method = None
            if name_and_arguments_list in method_map:
                branch_related_called_method = method_map[name_and_arguments_list]
                
            if branch_related_called_method is not None:
                single_method.add_branch_related_called_method(branch_related_called_method)
                branch_related_called_class_name = '.'.join(name_and_arguments_list[0].split('.')[:-1])
                branch_related_called_class = class_map.get(branch_related_called_class_name)
                single_method.add_branch_related_called_methods_and_class(branch_related_called_method, branch_related_called_class)

    # for single_method in all_methods_in_package:
    #     paths_from_single_func = find_all_chains(single_method)
    #     for i in paths_from_single_func:
    #         if len(i[1:]) != 0:
    #             single_method.add_called_chain(i[1:])    # 不包括自己的后续链加入
    # pass

def setup_all_packages(project_name , all_packages , method_map , class_map):
    all_methods_in_package = set()
    for i in range(len(all_packages)):
        single_package = all_packages[i]
        all_methods_in_package = single_package.methods | all_methods_in_package

    logger.debug(f"Begin extracting context for {project_name}")
    setup_single_package(all_methods_in_package, method_map, class_map)
    logger.debug(f"Finish extracting context for {project_name}")

def analyze_project(project_name):
    """
        static analysis the project
    """
    project_group = project_name.split('_')[0]   # eg: Lang
    project_path = os.path.join(CONFIG['mappings']['buggy_loc'] , project_group , project_name)
    src_path = CONFIG['mappings']['src']   # src/main/java

    # eg: loc = "/home/miracle/DP_CFG/project_under_test/Lang/Lang_1_buggy"  src = "src/main/java"
    all_packages, method_map, class_map = get_packages(project_path , src_path)
    setup_all_packages(project_name, all_packages, method_map, class_map)
    return all_packages, method_map, class_map

# if __name__ == "__main__":
#     project_path = "/home/miracle/DP_CFG/project_under_test/Lang/Lang_1_buggy"
#     src_path = "src/main/java"
#     get_packages(project_path , src_path)