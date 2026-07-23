from generate_for_buggy.utils.preprocess_project import get_packages
from generate_for_buggy.run_generate import analyze_project , delete_existing_case_and_save , generate_entry
from generate_for_buggy.utils.process_project_info import process_method_info
from generate_for_buggy.models.OpenAILLM import OpenaiModel
from generate_for_buggy.models.model import set_model , set_review_model
from generate_for_buggy.agents.agent_reviewer import ReviewAgent
# from generate_for_buggy.run_generate import generate_entry
import os
import json

# data_list_path = os.path.join(os.path.dirname(__file__) , 'analyse_result' , 'no_add_and_delete_result.txt')

# data_list = []

# with open(data_list_path , 'r' , encoding='utf-8') as f:
#     contents = f.readlines()
#     for line in contents:
#         project_name = line.strip()

#         if project_name == "Lang_1":
#             data_list.append(project_name)
        
        # data_list.append(project_name)

# project_path = "/home/miracle/DP_CFG/data/project_under_test/Lang/Lang_1_buggy"
# src_path = "src/main/java"
# test_path = "src/test/java"
# # all_packages, method_map, class_map = get_packages(project_path , src_path)

# all_packages, method_map, class_map = analyze_project("Lang_1" + "_buggy")

# for package in all_packages:
#     for file in package.files:
#         if file.file_name == 'NumberUtils.java':
#             for method in file.methods:
#                 print(f"{method.name} javadoc: ")
#                 if method.javadoc is not None:
#                     print(method.javadoc)
#                 else:
#                     print("No javadoc")

# pass
# tmp_test_dir = "/home/miracle/DP_CFG/data/tmp_test"
# delete_existing_case_and_save("Lang_1" + "_buggy", tmp_test_dir)

# project_name = "Lang_1"
# info_json = process_method_info(project_name , "src/main/java")

# model_name = "Qwen/Qwen2.5-Coder-7B-Instruct"
model_name = "Qwen/Qwen2.5-Coder-7B-Instruct"
BASE_URL = "https://api.siliconflow.cn/v1"
API_KEY = "sk-vbraavnwvagkmnhrshcelqaytpinotzfssrwupvkdtsnfptc"


selected_model = OpenaiModel(model_name)
set_model(selected_model , API_KEY , BASE_URL)

review_model_name = "gpt-4o-mini"
BASE_URL = "https://api.openai-proxy.org/v1"
API_KEY = "sk-YXeIf5Hzq452SluTP77QPGWOeWHq7GFMqH4C4kwr9uFZhbhv"

review_model = OpenaiModel(review_model_name)
set_review_model(review_model , API_KEY , BASE_URL)

generate_entry()