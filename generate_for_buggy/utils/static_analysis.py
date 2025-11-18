import os
import sys

# import tree_sitter_java as ts_java
from tree_sitter_languages import get_language
from ..basic_class.base_class import Class
from ..basic_class.base_method import Method
from tree_sitter import Language , Parser
import queue

# JAVA_LANGUAGE = Language(ts_java.language(), name="java")
JAVA_LANGUAGE = get_language("java")
parser = Parser()
# parser.language = JAVA_LANGUAGE
parser.set_language(JAVA_LANGUAGE)

def find_package(node):
    if node.type == "package_declaration":
        for child in node.children:
            if child.type == "scoped_identifier":
                return child.text.decode("utf-8")   # package name
    
    for child in node.children:
        result = find_package(child)
        if result is not None:
            return result
    
    return None

def find_package_use_source_code(source_code):
    '''
        根据源代码解析出的语法树找到包声明
    '''
    tree = parser.parse(bytes(source_code , "utf8"))  # 解析为字节流
    root_node = tree.root_node   # root node 保存了代码的语法结构
    package_name = find_package(root_node)
    return package_name

def find_classes(node , package , out_class_name , class_queue , single_file):
    '''
        递归寻找类，不包含内部类
    '''
    if node.type in ['class_declaration', 'interface_declaration', 'enum_declaration']:
        class_name_node = node.child_by_field_name("name")
        class_name = class_name_node.text.decode()
        class_content = node.text.decode()

        if out_class_name != None:
            # 处理内部类
            class_name = f"{out_class_name}.{class_name}"
        
        # (name , belong_package , name_no_package , content , node)
        new_class = Class(f"{package.name}.{class_name}" , package , class_name , class_content , node)
        package.add_class(new_class)
        single_file.add_class(new_class)

        class_queue.put([new_class , node.child_by_field_name("body")])
        package.import_map[class_name] = f'{package.name}.{class_name}'

        return 
    
    for child in node.children:
        find_classes(child , package , out_class_name , class_queue , single_file)

def get_type_to_full_name(node , method):
    import_map = method.import_map

    if node.type in ['integral_type', 'floating_point_type', 'void_type']:  # 整型 ， 浮点 ，void
        return node.text.decode()
    if node.type in ['scoped_type_identifier', 'type_identifier']:   # 简单类或作用域类 eg: List , java.util.List
        return import_map[node.text.decode()] if node.text.decode() in import_map else node.text.decode()
    if node.type == 'generic_type':   # 泛型 eg: List<String> type_identifier = List
        type_identifier = None
        for child in node.children:
            if child.type == 'type_identifier':
                type_identifier = child.text.decode()
            elif child.type == "scoped_type_identifier":  # java.lang.Class<?>
                type_identifier = child.text.decode()

        return import_map[type_identifier] if type_identifier in import_map else type_identifier
    if node.type == 'array_type':  # 数组类型 element + dimensions 递归处理，能够做到处理多维数组的能力 因为多维数组的element中依然是array_type
        return get_type_to_full_name(node.child_by_field_name('element'), method) + node.child_by_field_name('dimensions').text.decode()
    return node.text.decode()

def find_method(node , new_class , package , single_file):
    if node.type in ["class_declaration" , "interface_declaration" , "enum_declaration"]:
        return 
    if node.type in ['constructor_declaration' , 'method_declaration']:
        method_modifier = None
        is_target = False
        for child in node.children:
            if child.type == 'method_modifier':
                method_modifier = child.text.decode()
        if method_modifier == "public":
            is_target = True
        
        method_content = node.text.decode()
        
        # return type
        if node.type == "method_declaration":
            method_return_type = get_type_to_full_name(node.child_by_field_name('type'), new_class)
        else:  #构造函数返回的是本身自己的类
            method_return_type = new_class.name

        # parameters
        parameters = []
        name_node = node.child_by_field_name('name')
        parameters_node = node.child_by_field_name('parameters')
        if name_node is None or parameters is None:
            return 
        
        method_name = name_node.text.decode()
        for child in parameters_node.children:
            if child.type == 'formal_parameter':
                parameter_node = child.child_by_field_name('type')
                if parameter_node is None:
                    continue
                parameter_type = get_type_to_full_name(parameter_node, new_class)
                parameters.append(parameter_type)
        
        # (name_no_package , name , belong_package , belong_class , parameters_list , content , return_type , node)
        method = Method(method_name , f'{new_class.name}.{method_name}' , package , new_class , parameters , method_content , method_return_type , node)
        if is_target:
            method.is_target = True
        
        new_class.add_method(method)
        package.add_method(method)
        single_file.add_method(method)

        if node.type == "constructor_declaration":
            new_class.add_constructor(method)
    
    for child in node.children:
        find_method(child , new_class , package , single_file)


def find_import(node , single_file , package_map , method_map):
     #import com.example.classA;
    if node.type == 'import_declaration':
        # if package.name == 'org.jfree.chart.renderer.xy':
        #     print(node.text.decode())
        flag = False
        import_node = None
        # 判断是否存在import static java.util.* 形式
        for child in node.children:
            if child.type == 'asterisk':  # 代表有import *
                flag = True
            if child.type == 'scoped_identifier' or child.type == 'identifier':
                import_node = child # com.exampke.classA
        # print(flag)
        if flag and import_node is not None:   # 如果 import * 就把这个包的所有class和method加入到single_file.import_map和method_map中
            package_name = import_node.text.decode()  # eg: java.utils.*  package_name = java.utils
            if package_name in package_map:
                package = package_map[package_name]
                # 把这个包的class都加进去
                for classs in package.classes:
                    single_file.import_map[classs.name_no_package] = classs.name  # File.import_map =  class_name_no_package : class_name_with_package
                    for method in classs.methods:
                        method_map[(method.name, tuple(method.parameters_list))] = method  # method_map = (method_name , (method_parameters)) : Method
        elif import_node is not None:
            class_name_node = import_node.child_by_field_name('name')
            package_name = import_node.child_by_field_name('scope').text.decode()  # eg: com.example.classA  package_name = com.example | class_name = classA
            class_name = class_name_node.text.decode() # classA 
            # 把这个类里面的方法加到method_map里面
            if package_name in package_map:
                single_package = package_map[package_name]
                for classs in single_package.classes:
                    if classs.name == class_name:  
                        for method in classs.methods:
                            method_map[(method.name, tuple(method.parameters_list))] = method
            single_file.import_map[class_name] = import_node.text.decode()  # 完整类名
            # print(package.name)
            # if package.name == 'org.jfree.chart.renderer.xy':
            #     print(node.text.decode())
            #     print(f'假{class_name} : {import_node.text.decode()}')
    for child in node.children:
        find_import(child, single_file, package_map, method_map)


def get_package_import(single_file , source_code , all_packages):
    tree = parser.parse(bytes(source_code , "utf8"))
    root_node = tree.root_node
    package_map = {}
    
    # package_name : Package
    for single_package in all_packages:
        package_map[single_package.name] = single_package
    
    method_map = {}
    find_import(root_node , single_file , package_map , method_map)
    # return method_map

def find_father_class(node , classs):
    if node.type in ['class_declaration', 'interface_declaration', 'enum_declaration']:
        #   #   #   #
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode()
        if name is None or name != classs.name_no_package:
            return
        
        if node.child_by_field_name('superclass') is not None:   # extends father_class
            superclass_node = node.child_by_field_name('superclass')
            superclass_name = ""
            for child in superclass_node.children:
                if child.type == 'type_identifier':
                    superclass_name = child.text.decode()
            # print('yes')
            if superclass_name == "":
                return
            if superclass_name in classs.import_map:
                classs.add_father_class_name(classs.import_map[superclass_name])
            else:  # 默认父类在一个包里面
                classs.add_father_class_name(f'{classs.belong_package.name}.{superclass_name}')
            return
        superclass_node = None
        for child in node.children:   
            if child.type == 'extends_interfaces':   # extends interface
                superclass_node = child
                break
            if child.type == 'super_interfaces':    # implements interface
                superclass_node = child
                break
        if superclass_node is None:
            return
        
        type_node = None
        for child in superclass_node.children:   # 获取接口列表
            if child.type == 'type_list':
                type_node = child
                break
        if type_node is None:
            return
        
        superclass_name = None
        for child in type_node.children:
            if child.type == 'type_identifier':
                superclass_name = child.text.decode()
        if superclass_name is None:
            return
        if superclass_name in classs.import_map:
            classs.add_father_class_name(classs.import_map[superclass_name])
        else:
            classs.add_father_class_name(f'{classs.belong_package.name}.{superclass_name}')
        return
    
    for child in node.children:
        find_father_class(child, classs)   # ？？？ 内部类的父类难道不会更新为该类的父类吗

def add_classes_and_methods_in_package(package , source_code , single_file):
    """
    Adds classes and methods in a package based on the given source code.

    Args:
        package (Package): The name of the package.
        source_code (str): The source code to analyze.
        single_file (File): Flag indicating whether the source code is a single file or multiple files.

    Returns:
        None
    """
    # Rest of the code...
    tree = parser.parse(bytes(source_code, "utf8"))
    root_node = tree.root_node
    class_queue = queue.Queue()
    find_classes(root_node, package, None, class_queue, single_file)  # 不包括内部类
    while not class_queue.empty():
        now_class, now_node = class_queue.get()
        out_class_name = now_class.name_no_package  # 寻找内部类
        find_classes(now_node, package, out_class_name, class_queue, single_file)
        find_method(now_node, now_class, package, single_file)