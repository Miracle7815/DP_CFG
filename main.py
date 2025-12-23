from generate_for_buggy.utils.preprocess_project import get_packages

project_path = "/home/miracle/DP_CFG/data/project_under_test/Lang/Lang_1_buggy"
src_path = "src/main/java"
all_packages, method_map, class_map = get_packages(project_path , src_path)