import os

def create_directory_temp_test(root_dir , package_name):
    package_path = package_name.replace('.' , os.sep)
    path = os.path.join(root_dir , package_path)
    os.makedirs(path, exist_ok=True)
    return path

def write_to_file(file_path , content):
    with open(file_path , 'w' , encoding="utf-8") as f:
        f.write(content)
    f.close()

def remove_file(file_path):
    os.remove(file_path)

def create_directory(dir_path):
    os.makedirs(dir_path , exist_ok=True)