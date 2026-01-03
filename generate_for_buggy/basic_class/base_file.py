class File():
    def __init__(self , file_path , content , belong_package):
        self.file_path = file_path
        self.file_name = file_path.split('/')[-1]
        self.content = content
        self.classes = set()    # class in file
        self.methods = set()    # method in file
        self.import_map = {}
        self.belong_package = belong_package

        self.import_method_map = {}
        self.import_static_method = {}
        self.import_static_field = {}

        self.import_jdk_map = {}
        self.import_static_jdk_map = {}

        # cfg
        self.file_obj = None
        self.node_id_to_line_number = None
        self.line_number_to_node_id = None

    def add_method(self , method):
        self.methods.add(method)
    
    def add_class(self , new_class):
        self.classes.add(new_class)