from generate_for_buggy.utils.preprocess_project import get_packages
# from generate_for_buggy.run_generate import generate_entry
import os
import json

# data_list_path = os.path.join(os.path.dirname(__file__) , 'data' , 'defects4j_list.txt')

# data_list = []

# with open(data_list_path , 'r' , encoding='utf-8') as f:
#     contents = f.readlines()
#     for line in contents:
#         project_name = line.strip()

#         if project_name == "Lang_1":
#             data_list.append(project_name)
        
        # data_list.append(project_name)

project_path = "/home/miracle/DP_CFG/data/project_under_test/Lang/Lang_1_buggy"
src_path = "src/main/java"
all_packages, method_map, class_map = get_packages(project_path , src_path)

for package in all_packages:
    for file in package.files:
        if file.file_name == 'NumberUtils.java':
            for method in file.methods:
                print(f"{method.name} javadoc: ")
                if method.javadoc is not None:
                    print(method.javadoc)
                else:
                    print("No javadoc")
                
pass