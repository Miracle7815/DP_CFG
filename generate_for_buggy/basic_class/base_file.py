class File():
    def __init__(self , file_path , content , belong_package):
        self.file_path = file_path
        self.file_name = file_path.split('/')[-1]
        self.content = content
        self.classes = set()    # class in file
        self.methods = set()    # method in file
        self.import_map = {}
        self.belong_package = belong_package

    def add_method(self , method):
        self.methods.add(method)
    
    def add_class(self , new_class):
        self.classes.add(new_class)