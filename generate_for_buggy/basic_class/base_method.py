from collections import defaultdict
import copy

class Method():
    def __init__(self , name_no_package , name , belong_package , belong_class , parameters_list , parameters_modifiers_list , content , return_type , node):
        self.name_no_package = name_no_package
        self.name = name

        self.belong_package = belong_package
        self.belong_class = belong_class
        self.belong_file = None

        self.parameters_list = parameters_list
        self.parameters_modifiers_list = parameters_modifiers_list
        self.signature = ''
        self.parameters_string = ''

        self.content = content
        self.return_type = return_type
        self.node = node
        
        self.return_class = None

        self.variable_map = {}
        self.import_map = {}

        self.line_range = set()

        self.javadoc = None

        self.called_method_name = set()
        self.called_methods = set()
        self.callee_methods = set()
        self.called_chains = []
        self.callee_chains = []

        self.branch_related_called_methods_name = set()
        self.branch_related_called_methods = set()

        self.called_method_2_class = defaultdict()      # 如果method是子类中未实现的继承父类的method，method是父类的对象，class是子类的
        self.branch_related_called_methods_2_class = defaultdict()

        self.used_callee_chains = list()
        self.using_callee_chains = list()

        self.is_target = False
        self.is_static = False

        self.direct_programs = list()

        # test
        self.test_head = None
        self.new_programs = list()
        self.covered_lines = set()
        
        self.newly_covered_by_llm = set()
        self.line_number = set()
        
        self.covered_tests = set()

        # coverage info
        self.missed_lines = set()
        self.missed_brachese = set()

        # cfg
        # self.declaration_value = None
        self.cfg_info = None

    def get_package_name(self):
        return self.belong_package.name
    
    def add_covered_tests(self, test):
        self.covered_tests.add(test)
        
    def add_called_method_and_class(self, called_method, called_class):
        self.called_method_2_class[called_method] = called_class
    
    def add_branch_related_called_methods_and_class(self, called_method, called_class):
        self.branch_related_called_method_2_class[called_method] = called_class
        
    def get_called_class(self, called_method):
        return self.called_method_2_class.get(called_method)
    
    def get_branch_related_called_class(self, called_method):
        return self.branch_related_called_method_2_class.get(called_method)
    
    def add_call_method_name(self, method_name, arguments_list):
        self.called_method_name.add((method_name, tuple(arguments_list)))
        
    def add_called_method(self, method):
        self.called_methods.add(method)
        
    def add_callee_method(self, method):
        self.callee_methods.add(method)
        
    def add_called_chain(self, chain):
        self.called_chains.append(chain)
    
    def add_callee_chain(self, chain):
        self.callee_chains.append(chain)
    
    def add_branch_related_called_method_name(self, signature):
        self.branch_related_called_methods_name.add(signature)
        
    def add_branch_related_called_method(self, method):
        self.branch_related_called_methods.add(method)
        
    def set_target(self):
        self.is_target = True
        
    def add_direct_program(self, direct_program):
        self.direct_programs.append(direct_program)
    
    def add_new_program(self, new_program):
        self.new_programs.append(new_program)
    
    def add_variable_map(self, variable_map):
        self.variable_map = copy.deepcopy(variable_map)
            
    def get_covered_lines(self):
        return self.covered_lines
    
    def add_covered_lines(self, new_line):
        self.covered_lines = self.covered_lines.union(new_line)
        
    def add_covered_by_llm(self, new_line):
        self.newly_covered_by_llm = self.newly_covered_by_llm.union(new_line)
    
    def set_method_signature(self):
        help_parameters_list = []
        for parameter in self.parameters_list:
            
            class_name = parameter.split('.')[-1]
            if class_name == parameter:
                if class_name == 'byte':
                    class_name = 'Byte'
                if class_name == 'character':
                    class_name = 'Character'
                if class_name == 'double':
                    class_name = 'Double'
                if class_name == 'float':
                    class_name = 'Float'
                if class_name == 'int':
                    class_name = 'Integer'
                if class_name == 'long':
                    class_name = 'Long'
                if class_name == 'boolean':
                    class_name = 'Boolean'
                if class_name == 'short':
                    class_name = 'Short'
                help_parameters_list.append('java.lang#' + class_name)
            else:
                package_name = parameter.split('.' + class_name)[0]
                help_parameters_list.append(package_name + '#' + class_name)
        # parameters_string = ','.join(item for item in help_parameters_list)
        parameters_string = ','.join((modifier.strip() + " " if modifier != "no modifier" else "") + item for modifier , item in zip(self.parameters_modifiers_list , help_parameters_list))
        
        self.signature = f'{self.belong_package.name}#{self.belong_class.name_no_package}#{self.name_no_package}({parameters_string})'

    def add_javadoc(self, javadoc):
        self.javadoc = javadoc