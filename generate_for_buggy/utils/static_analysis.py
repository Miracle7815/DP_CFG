import os
import sys
import copy
# import tree_sitter_java as ts_java
from tree_sitter_languages import get_language
from ..basic_class.base_class import Class
from ..basic_class.base_method import Method
from tree_sitter import Language , Parser
from tarjan import topu_to_find_related
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

# 类中定义的变量
def find_class_variable(node , variable_map , classs):
    import_map = classs.import_map
    if node.type in ['class_declaration', 'interface_declaration', 'enum_declaration']:
        return
    if node.type in ['field_declaration', 'constant_declaration']: # String s = getName(a, b);  field_declaration: private int count;
        type_node = node.child_by_field_name('type')
        if type_node is None:
            return
        if type_node.type in ['integral_type', 'floating_point_type']: # 类似 int a = 1;
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句  # eg: int x = 1 , y = 2;
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode() # s = getName(a, b) -> s
                # # print(variable_name)
                variable_map[variable_name] = type_node.text.decode() 
        elif type_node.type in ['scoped_type_identifier', 'type_identifier']: # 实例化对象  ClassA a = ...;
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句   
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode()   # 变量名
                variable_value_node = variable_node.child_by_field_name('value')   # 赋值表达式
                if variable_value_node is None:  # 仅声明，则 variable_map = variable_name : type_name
                    type_name = type_node.text.decode()
                    variable_map[variable_name] = import_map[type_name] if type_name in import_map else type_name
                elif variable_value_node.type == 'identifier': # 类似 ClassA b = a;
                    decoded_text = variable_value_node.text.decode()
                    if decoded_text in variable_map:  # 是否之前定义过的变量
                        variable_map[variable_name] = variable_map[decoded_text]
                elif variable_value_node.type == 'object_creation_expression': # 类似 ClassA a = new ClassA();
                    variable_class = variable_value_node.child_by_field_name('type').text.decode() #  A.B     String
                    for child in variable_value_node.children: 
                        if child.type == 'identifier':     # 新版tree-sitter有所改动
                            variable_class = variable_map[child.text.decode()] + '.' + variable_class
                    variable_map[variable_name] = import_map[variable_class] if variable_class in import_map else variable_class # 去import map里面找全名，找不到就直接存进去
                else: # 如果上述方法都不可以的话，就直接选取前面的type作为类型; 例如 ClassA a = getClassA();
                    type_name = type_node.text.decode()
                    variable_map[variable_name] = import_map[type_name] if type_name in import_map else type_name
                    
        elif type_node.type == 'array_type': # 处理数组 int[][] a = new int[5][5];
            element_node = type_node.child_by_field_name('element') # int
            dimensions = type_node.child_by_field_name('dimensions').text.decode() # [][]
            if element_node.type in ['integral_type', 'floating_point_type']:
                element_type = element_node.text.decode()
            elif element_node.type in ['scoped_type_identifier', 'type_identifier']:
                element_type = import_map[element_node.text.decode()] if element_node.text.decode() in import_map else element_node.text.decode()
            else:
                element_type = import_map[element_node.text.decode()] if element_node.text.decode() in import_map else element_node.text.decode()
            
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode()
                variable_map[variable_name] = element_type + dimensions
        elif type_node.type == 'generic_type': # 泛型 List<String> list; 
            type_name = None
            for child in type_node.children:
                if child.type == 'type_identifier':
                    type_name = child.text.decode()
                    break
            if type_name is None:
                return
            
            for variable_node in node.children:
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode()
                variable_map[variable_name] = import_map[type_name] if type_name in import_map else type_name
        else: 
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode() # s = getName(a, b) -> s
                # # print(variable_name)
                variable_map[variable_name] = type_node.text.decode()
                
    for child in node.children:
        find_class_variable(child, variable_map, classs)


def find_method_variable(node , variable_map , method):
    import_map = method.import_map
    if node.type == 'formal_parameter': # 方法声明时候传入的参数
        variable_node = node.child_by_field_name('type') #  example.classA
        if variable_node is None:
            return
        variable_type = get_type_to_full_name(variable_node, method)
        variable_name = node.child_by_field_name('name').text.decode()
        variable_map[variable_name] = variable_type
    
    if node.type == 'spread_parameter': # 方法声明中的可变参数列表
        # print(node.text.decode())
        variable_type = None
        for child in node.children:
            if child.type in ['integral_type', 'floating_point_type', 'void_type']:
                variable_type = child.text.decode()
            if child.type in ['scoped_type_identifier', 'type_identifier']:
                variable_type = import_map[child.text.decode()] if child.text.decode() in import_map else child.text.decode()
            if child.type == 'array_type':
                variable_type = get_type_to_full_name(child.child_by_field_name('element'), method) + child.child_by_field_name('dimensions').text.decode()
            if child.type == 'variable_declarator':  # 变量名
                variable_name = child.child_by_field_name('name').text.decode()
        if variable_type != None:
            # print(variable_name)
            variable_map[variable_name] = variable_type
        
    if node.type == 'local_variable_declaration': # String s = getName(a, b);      局部变量
        # print(node.text.decode())
        type_node = node.child_by_field_name('type')
        if type_node is None:
            return
        if type_node.type in ['integral_type', 'floating_point_type']: # 类似 int a = 1;
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode() # s = getName(a, b) -> s
                # # print(variable_name)
                variable_map[variable_name] = type_node.text.decode()
        elif type_node.type in ['scoped_type_identifier', 'type_identifier']: # 实例化对象
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode()
                variable_value_node = variable_node.child_by_field_name('value')
                if variable_value_node is None:
                    type_name = type_node.text.decode()
                    variable_map[variable_name] = import_map[type_name] if type_name in import_map else type_name
                elif variable_value_node.type == 'identifier': # 类似 ClassA b = a;
                    decoded_text = variable_value_node.text.decode()
                    if decoded_text in variable_map:
                        variable_map[variable_name] = variable_map[decoded_text]
                elif variable_value_node.type == 'object_creation_expression': # 类似 ClassA a = new ClassA();
                    variable_class = variable_value_node.child_by_field_name('type').text.decode() #  A.B     String
                    for child in variable_value_node.children:
                        if child.type == 'identifier':
                            variable_class = variable_map[child.text.decode()] + '.' + variable_class
                    variable_map[variable_name] = import_map[variable_class] if variable_class in import_map else variable_class # 去import map里面找全名，找不到就直接存进去
                else: # 如果上述方法都不可以的话，就直接选取前面的type作为类型;
                    type_name = type_node.text.decode()
                    variable_map[variable_name] = import_map[type_name] if type_name in import_map else type_name
                    
        elif type_node.type == 'array_type': # 处理数组 int[][] a = new int[5][5];
            element_node = type_node.child_by_field_name('element') # int
            dimensions = type_node.child_by_field_name('dimensions').text.decode() # [][]
            if element_node.type in ['integral_type', 'floating_point_type']:
                element_type = element_node.text.decode()
            elif element_node.type in ['scoped_type_identifier', 'type_identifier']:
                element_type = import_map[element_node.text.decode()] if element_node.text.decode() in import_map else element_node.text.decode()
            else:
                element_type = import_map[element_node.text.decode()] if element_node.text.decode() in import_map else element_node.text.decode()
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode()
                variable_map[variable_name] = element_type + dimensions
        elif type_node.type == 'generic_type': 
            type_name = None
            for child in type_node.children:
                if child.type == 'type_identifier':
                    type_name = child.text.decode()
                    break
            if type_name is None:
                return
            
            for variable_node in node.children:
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode()
                variable_map[variable_name] = import_map[type_name] if type_name in import_map else type_name
        else: # List<String> list;
            for variable_node in node.children: # 一条赋值语句中可能有多个赋值语句
                if variable_node.type != 'variable_declarator':
                    continue
                variable_name = variable_node.child_by_field_name('name').text.decode() # s = getName(a, b) -> s
                # # print(variable_name)
                variable_map[variable_name] = type_node.text.decode()

    for child in node.children:
        find_method_variable(child, variable_map, method)

# 辅助函数： 获取一个函数调用的返回类型
def get_method_return(method_name, arguments_list, package, method_map):
    if (method_name, tuple(arguments_list)) in method_map:
        return method_map[method_name, tuple(arguments_list)].return_type
    return method_name

# 辅助函数：获取方法调用时传入参数的类型
def get_type_of_method_invocation(node, classs, variable_map, package, method_map, class_map, method_cache):
    import_map = classs.import_map
    if node.type == 'cast_expression':   # 类型转换 提取括号内部再分析
        type_node = node.child_by_field_name('type')
        if type_node is None:
            return None
        return get_type_of_method_invocation(type_node, classs, variable_map, package, method_map, class_map, method_cache)
    elif node.type == 'field_access':     # obj.fieldName
        obj_node = node.child_by_field_name('object')
        field_node = node.child_by_field_name('field')
        if obj_node is None or field_node is None:
            return None
        if obj_node.type == 'this':  # this只返回fieldName
            field_name = field_node.text.decode()
            return variable_map[field_name] if field_name in variable_map else field_name
        elif obj_node.type == 'identifier':  # 返回obj.fieldName
            obj_name = obj_node.text.decode()
            obj_name = variable_map[obj_name] if obj_name in variable_map else obj_name
            field_name = field_node.text.decode()
            return f'{obj_name}.{field_name}'
            
    elif node.type == 'array_access':  # 数组
        type_node = node.child_by_field_name('array')
        if type_node is None:
            return None
        array_name = type_node.text.decode()
        array_type = variable_map[array_name] if array_name in variable_map else array_name
        return array_type.split('[')[0]  # 返回单个元素类型
    elif node.type == 'true' or node.type == 'false':
        return 'boolean'
    elif node.type == 'decimal_integer_literal':
        return 'int'
    elif node.type == 'decimal_floating_point_literal':
        return 'float'
    elif node.type == 'string_literal':
        return 'String'
    elif node.type == 'type_identifier':
        return import_map[node.text.decode()] if node.text.decode() in import_map else node.text.decode()
    elif node.type == 'identifier':   # 变量
        return variable_map[node.text.decode()] if node.text.decode() in variable_map else node.text.decode()
    elif node.type == 'binary_expression': # 二元表达式  取左边表达式类型为结果
        return get_type_of_method_invocation(node.child_by_field_name('left'), classs, variable_map, package, method_map, class_map, method_cache)
    elif node.type == 'method_invocation':  # 参数方法调用的结果
        if node.text.decode() in method_cache:   
            (method_name, arguments_list) = method_cache[node.text.decode()]
        else:
            method_name, arguments_list = get_method_name_and_arguments(node, classs, variable_map, package, method_map, class_map, method_cache)
            method_cache[node.text.decode()] = (method_name, arguments_list)
        return get_method_return(method_name, arguments_list, package, method_map)
    elif node.type == 'object_creation_expression': # a.new Classa()       # 创建类型 new Class()
        variable_class = node.child_by_field_name('type').text.decode() #  A.B String
        for child in node.children:
            if child.type == 'identifier':
                variable_class = variable_map[child.text.decode()] + '.' + variable_class
        return import_map[variable_class] if variable_class in import_map else variable_class # 去import map里面找全名，找不到就直接存进去
    
    # elif node.type == 'array_creation_expression':
    #     pass
    return None

# 辅助函数：获取一个函数调用的完整名字和参数列表
def get_method_name_and_arguments(node , classs , variable_map , package , method_map , class_map , method_cache , depth=1):
    if node.text.decode() in method_cache:
        return method_cache[node.text.decode()]
    if depth > 5:   # 递归深度
        return '', []
    
    import_map = classs.import_map
    object_node = node.child_by_field_name('object')  # caller eg: foo.bar为foo
    method_name = node.child_by_field_name('name').text.decode()
    arguments_node = node.child_by_field_name('arguments')
    
    # 获取方法调用的类
    if object_node is None:
        invocation_class = classs.name   # 没有前缀，调用本类的方法
    elif object_node.type == 'method_invocation':   # 调用者是另一个方法的结果 obj.getService().run()
        if object_node.text.decode() in method_cache:   
            (method_name, arguments_list) = method_cache[object_node.text.decode()]   # 调用的语句 : (method_name , arguments_list)
        else:
            method_name, arguments_list = get_method_name_and_arguments(object_node, classs, variable_map, package, method_map, class_map, method_cache, depth + 1)
            method_cache[object_node.text.decode()] = (method_name, arguments_list)
        invocation_class = get_method_return(method_name, arguments_list, package, method_map)   # 调用者的返回类型 eg: obj.getService() return org.demo.Service
        if invocation_class is None:
            invocation_class = object_node.text.decode()
    elif object_node.type == 'super' and classs.father_class is not None:   # super.method()
        invocation_class = classs.father_class.name
    elif object_node.type == 'this':  # this.method()
        invocation_class = classs.name
    else:
        invocation_class = object_node.text.decode()
        if 'this.' in invocation_class:
            invocation_class = invocation_class.split('.')[-1]   # this.foo().bar()
    # 优先局部变量， import中的类型，最后是原名
    invocation_class = variable_map[invocation_class] if invocation_class in variable_map else import_map[invocation_class] if invocation_class in import_map else invocation_class
    
    # 获取方法调用的函数列表
    arguments_list = []
    for child in arguments_node.children:
        argument_type = get_type_of_method_invocation(child, classs, variable_map, package, method_map, class_map, method_cache)
        # eg: foo.bar(a, user.getName(), new Person()); =》 arguments_list = ["int", "java.lang.String", "org.demo.Person"]
        if argument_type != None:
            # 处理'field_access'
            if '.' in argument_type:  # 分解为class名 + 变量名
                class_name = '.'.join(argument_type.split('.')[0 : -1])
                variable_name = argument_type.split('.')[-1]
                called_class = class_map.get(class_name)
                if called_class is not None:
                    called_variable_map = called_class.variable_map
                    variable_type = called_variable_map.get(variable_name)
                    if variable_type is not None:
                        argument_type = variable_type     # 修改为该变量在类中存储的类型 # eg: class A {Address address;} 则 org.demo.A.address => org.demo.Address
                
            arguments_list.append(argument_type)

    return(invocation_class + '.' + method_name, arguments_list)


# 找到方法调用
def find_method_invocation(node , classs , method , variable_map , package , method_map , class_map , method_cache):
    import_map = method.import_map
    if node.type == 'method_invocation': # 直接的方法调用
        if node.text.decode() in method_cache:   
            (method_name, arguments_list) = method_cache[node.text.decode()]
        else:
            method_name, arguments_list = get_method_name_and_arguments(node, classs, variable_map, package, method_map, class_map, method_cache)  # 获得方法名和参数列表
            method_cache[node.text.decode()] = (method_name, arguments_list)
        method.add_call_method_name(method_name, arguments_list)   # 该函数调用的方法名和参数列表
    if node.type == 'object_creation_expression': # 实例化对象的时候会调用一次构造函数  new Class()
        class_name = node.child_by_field_name('type').text.decode() #  A.B String
        for child in node.children:  # Outer.Inner
            if child.type == 'identifier':
                class_name = variable_map[child.text.decode()] + '.' + class_name
        method_name = import_map[class_name] + '.' + class_name if class_name in import_map else class_name + '.' +  class_name # 去import map里面找全名，找不到就直接存进去
        arguments_node = node.child_by_field_name('arguments')
        arguments_list = []
        for child in arguments_node.children:
            argument_type = get_type_of_method_invocation(child, classs, variable_map, package, method_map, class_map, method_cache)
            if argument_type != None:
                arguments_list.append(argument_type)
        # # print(f'method {method.name}调用:')
        # # print(method_name)
        # # print(arguments_list)
        method.add_call_method_name(method_name, arguments_list)
    
    for child in node.children:
        find_method_invocation(child, classs, method, variable_map, package, method_map, class_map, method_cache)


# 辅助函数：找到结点中的变量和函数
def get_node_variable_and_method(node, classs, variable_map, variable_list, method_list, package, method_map, class_map, method_cache):
    if node.type == 'identifier':  # 变量
        name = node.text.decode()
        # print(variable_map)
        if name in variable_map:
            variable_list.append(name)
    if node.type == 'method_invocation':    # 方法调用
        if node.text.decode() in method_cache:   
            (method_name, arguments_list) = method_cache[node.text.decode()]
        else:
            method_name, arguments_list = get_method_name_and_arguments(node, classs, variable_map, package, method_map, class_map, method_cache)
            method_cache[node.text.decode()] = (method_name, arguments_list)
        method_list.append((method_name, tuple(arguments_list)))
        
    for child in node.children:
        get_node_variable_and_method(child, classs,variable_map, variable_list, method_list, package, method_map, class_map, method_cache)  
    pass


# 找和分支有关的变量和方法
def find_branch_related(node, method, classs, variable_map, variable_list, method_list, package, method_map, class_map, method_cache):
    if node.type in ['if_statement', 'while_statement', 'switch_expression', 'for_statement']:   # 控制语句节点
        condition_node = node.child_by_field_name('condition') 
        if condition_node is None:
            return
        # print(f'#######{condition_node.text.decode()}')
        get_node_variable_and_method(condition_node, classs, variable_map, variable_list, method_list, package, method_map, class_map, method_cache)
        pass
    
    for child in node.children:
        find_branch_related(child, method, classs, variable_map, variable_list, method_list, package, method_map, class_map, method_cache)   


# 找和每个变量有关的方法和变量  
#                                       变量名->类型   变量名->相关函数和变量
def find_variable_related(node, classs, variable_map, variable_related, package, method_map, class_map, method_cache):
    if node.type == 'assignment_expression':   # 赋值语句
        left_node = node.child_by_field_name('left')
        right_node = node.child_by_field_name('right')
        if right_node is None or left_node is None:
            return
        # print('yeyeyeyeye')
        name = left_node.text.decode()
        # print(name)
        if name in variable_map:
            if name not in variable_related:  # 存储和这个变量有关的方法和变量
                variable_related[name] = {
                    'method': [],
                    'variable': []
                }
            l = list()
            get_node_variable_and_method(right_node,  classs, variable_map, l, variable_related[name]['method'], package, method_map, class_map,method_cache)
            get_node_variable_and_method(right_node,  classs, variable_map, variable_related[name]['variable'], variable_related[name]['method'], package, method_map, class_map, method_cache)
    if node.type ==  'variable_declarator':  # 变量声明
        left_node = node.child_by_field_name('name')
        right_node = node.child_by_field_name('value')
        if right_node is None or left_node is None:
            return
        name = left_node.text.decode()
        if name in variable_map:
            if name not in variable_related:
                variable_related[name] = {
                    'method': [],
                    'variable': []
                }
            get_node_variable_and_method(right_node,  classs, variable_map, variable_related[name]['variable'], variable_related[name]['method'], package, method_map, class_map, method_cache)
    
    for child in node.children:
        find_variable_related(child, classs, variable_map, variable_related, package, method_map, class_map, method_cache)


def find_call_method(package , method_map , class_map):
    for classs in package.classes:
        class_node = classs.node
        body_node = class_node.child_by_field_name('body')
        if body_node is None:
            continue
        variable_map = {}
        find_class_variable(body_node , variable_map , classs)
        classs.add_variable_map(variable_map)

        for method in classs.methods:
            method_node = method.node
            method_variable_map = copy.deepcopy(variable_map)  # 继承类的全局变量
            find_method_variable(method_node , method_variable_map , method)
            method.add_variable_map(method_variable_map)
        
    method_cache = {}
    for classs in package.classes:
        class_node = classs.node
        variable_map = classs.variable_map

        for method in classs.methods:
            method_node = method.node
            method_variable_map = method.variable_map
            # 调用的函数 + 构造函数
            find_method_invocation(method_node , classs , method , method_variable_map , package , method_map , class_map , method_cache)
            variable_list = []
            method_list = []
            find_branch_related(method_node, method, classs, method_variable_map, variable_list, method_list, package, method_map, class_map, method_cache)
            variable_related = {}
            find_variable_related(method_node,  classs, method_variable_map, variable_related, package, method_map, class_map, method_cache)
            variable_related_copy = copy.deepcopy(variable_related)
            for var, info in variable_related_copy.items():
                for to_var in info['variable']:
                    if to_var not in variable_related:
                        variable_related[to_var] = {
                            'method': [],
                            'variable': []
                        }
                    
            variable_related = topu_to_find_related(variable_related)   # 拓扑排序
            for var in variable_list:
                if var not in variable_related:
                    continue
                for rela_method in variable_related[var]['method']:
                    method_list.append(rela_method)
            method_list = list(set(method_list))
            for method_signature in method_list:
                method.add_branch_related_called_method_name(method_signature)   # 与分支相关的调用方法
            # print(f'{method.name} variable_list {variable_list}')
            # print(f'{method.name} method_list {method_list}')


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