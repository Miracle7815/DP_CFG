import os
from queue import Queue
import chardet
from ..data.config import CONFIG , logger
from static_analysis import find_package_use_source_code , add_classes_and_methods_in_package , get_package_import , find_father_class
from ..basic_class.base_package import Package
from ..basic_class.base_method import Method
from ..basic_class.base_file import File
from ..basic_class.base_class import Class
from ..basic_class.base_test_program import TestProgram

def find_java_files(directory):
    java_files = []

    for root , dirs , files in os.walk(directory):
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
    #     print(file_path)
        try:
            with open(java_path, 'rb') as file:   # 二进制模式读文件
                raw_data = file.read()
            # 检测文件编码
            result = chardet.detect(raw_data)   # 通过chardet猜测编码方式再进行解码
            encoding = result['encoding']
            java_content = raw_data.decode(encoding)
        except UnicodeDecodeError as e:
            print(f"UnicodeDecodeError: {e}")
            continue
        except Exception as e:
            print(f"An error occurred: {e}")
            continue

        
        package_name = find_package_use_source_code(java_content)

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

        add_classes_and_methods_in_package(single_package , java_content , single_file)  # get classes and methods in file

    all_packages = list(all_packages_map.values())

    # 以 File 为单位进行分析
    for single_file in all_files:
        java_content = single_file.content
        get_package_import(single_file , java_content , all_packages)  # single_file.import_map中记录了import的class

        for classs in single_file.belong_package.classes:      # 同一个package下的class
            single_file.import_map[classs.name_no_package] = classs.name        # name_no_package -> name
        for classs in single_file.classes:
            class_map[classs.name] = classs
            classs.import_map = single_file.import_map
            for method in classs.methods:
                method.import_map = classs.import_map

    # 根据import更新参数列表和return type
    for single_file in all_files:
        for classs in single_file.classes:
            classs.belong_file = single_file
            for method in classs.methods:
                method.belong_file = single_file

                if method.return_type in method.import_map:
                    method.return_type = method.import_map[method.return_type]
                if method.return_type in class_map.import_map:
                    method.return_type = class_map[method.return_type]

                new_parameters_list = []
                for parameter in method.parameters_list:
                    if parameter in method.import_map:
                        parameter = method.import_map[parameter]
                    new_parameters_list.append(parameter)
                method.parameters_list = new_parameters_list
                method.set_method_signature()     # signature = belong_package.name#belong_class.name_no_package#method.name_no_package({parameters_string})
        
    for single_file in all_files:
        for classs in single_file.classes:
            find_father_class(classs.node , classs)

    for single_file in all_files:
        for classs in single_file.classes:
            if classs.father_class_name in class_map:
                father_class = class_map[classs.father_class_name]
                classs.father_class = father_class
                father_class.son_classes.add(classs)
    
    top_class = [classs for classs in class_map.values() if classs.father_class == None]   # 没有父类的类
    class_queue = Queue()
    for item in top_class:
        class_queue.put(item)
            
    # 继承方法的复制与传播
    while not class_queue.empty():
        classs = class_queue.get()
        for son_class in classs.son_classes:
            class_queue.put(son_class)
            for method in classs.methods:
                #为了deepcopy
                son_class_name = son_class.name
                new_method_name = son_class_name + '.' + method.name_no_package
                
                # 子类继承父类的方法
                new_method = Method(method.name_no_package, new_method_name, son_class.belong_package, son_class, method.parameters_list, method.content, method.return_type, method.node)
                
                new_method.import_map = method.import_map
                new_method.is_target = method.is_target
                new_method.set_method_signature()
                arguments_list = tuple(method.parameters_list)
                
                flag = True
                # 子类是否覆写了父类的方法
                for son_method in son_class.methods:
                    if son_method.signature == new_method.signature:
                        flag = False
                if flag:
                    son_class.add_method(method)
                    method_map[(new_method_name, arguments_list)]= method  # method_map = (method_name , parameters_list) : Metod
    
    all_packages = list(all_packages_map.values())

    # 处理每一个method的行范围
    for single_package in all_packages:
        for method in single_package.methods:
            method_node = method.node
            # 源代码的行号
            for i in range(method_node.start_point[0], method_node.end_point[0] + 1):
                method.line_range.add(i + 1)
                method.line_number.add(str(i + 1))
            name = method.name
            arguments_list = tuple(method.parameters_list)
            method_map[(name, arguments_list)]= method

    all_packages = list(all_packages_map.values()) 
    return all_packages, method_map, class_map


def analyze_project(project_name):
    """
        通过静态分析提取到项目中的代码调用关系, 以及现有测试程序对应方法的映射
    """
    # eg: loc = "/home/miracle/DP_CFG/project_under_test/Lang/Lang_1_buggy"  src = "src/main/java"
    all_packages, method_map, class_map = get_packages(CONFIG['path_mappings'][project_name]['loc'], CONFIG['path_mappings'][project_name]['src'])
    setup_all_packages(project_name, all_packages, method_map, class_map)
    return all_packages, method_map, class_map

if __name__ == "__main__":
    project_path = "/home/miracle/DP_CFG/project_under_test/Lang/Lang_1_buggy"
    src_path = "src/main/java"
    get_packages(project_path , src_path)