import copy

class Class():
    def __init__(self , name , belong_package , name_no_package , content , node):
        self.name = name # full name
        self.name_no_package = name_no_package # name without package
        
        self.belong_package = belong_package
        self.belong_file = None

        self.methods = set()
        self.constructor = set()
        self.content = content
        self.node = node
        self.father_class = None
        self.father_class_name = None
        self.son_classes = set()
        self.son_classes_name = set()
        self.variable_map = {}  # 全局变量
        self.import_map = {}

        self.fields = {}
    
    def add_constructor(self , constructor):
        self.constructor.add(constructor)

    def add_method(self , method):
        self.methods.add(method)

    def add_father_class(self , father_class):
        self.father_class = father_class
    
    def add_father_class_name(self , father_class_name):
        self.father_class_name = father_class_name
    
    def add_variable_map(self , variable_map):
        self.variable_map = copy.deepcopy(variable_map)