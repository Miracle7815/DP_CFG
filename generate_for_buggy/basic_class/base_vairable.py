class Variable:
    def __init__(self , name , type , belong_method , belong_class , node):
        self.name = name
        self.type = type
        self.belong_method = belong_method
        self.belong_class = belong_class
        self.node = node

        self.is_field = False
        self.is_static = False
        
        self.dfg_passed_by = set()
        self.dfg_passed_to = set()