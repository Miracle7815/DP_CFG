import os

import chardet
from ..data.config import CONFIG , logger

def find_java_files(directory):
    java_files = []

    for root , dirs , files in os.walk(directory):
        for file in files:
            if file.endswith('.java'):
                file_path = os.path.join(root , file)
                java_files.append(file_path)

    return java_files

def get_packages(project_path , src_path):
    all_files = []
    all_packages_map = {}
    method_map = {}
    class_map = {}

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


# def analyze_project(project_name):
#     """
#         通过静态分析提取到项目中的代码调用关系, 以及现有测试程序对应方法的映射
#     """
#     # eg: loc = "/home/miracle/DP_CFG/project_under_test/Lang/Lang_1_buggy"  src = "src/main/java"
#     all_packages, method_map, class_map = get_packages(CONFIG['path_mappings'][project_name]['loc'], CONFIG['path_mappings'][project_name]['src'])
#     setup_all_packages(project_name, all_packages, method_map, class_map)
#     return all_packages, method_map, class_map

if __name__ == "__main__":
    project_path = "/home/miracle/DP_CFG/project_under_test/Lang/Lang_1_buggy"
    src_path = "src/main/java"
    get_packages(project_path , src_path)