class Package():
    def __init__(self , package_name , package_path=None):
        self.name = package_name
        self.methods = set()   # method in package
        self.classes = set()   # class in package
        self.files = set()     # file in package
        self.import_map = {}
        self.package_path = package_path if package_path else ''

    def add_method(self , method):
        self.methods.add(method)

    def add_class(self , new_class):
        self.classes.add(new_class)

    def add_file(self , file):
        self.files.add(file)
