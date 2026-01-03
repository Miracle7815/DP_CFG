import os
from generate_for_buggy.cfg.src.comex.codeviews.CFG.CFG_driver import CFGDriver
from generate_for_buggy.cfg.src.comex.codeviews.combined_graph.combined_driver import CombinedDriver


project_path = "/home/miracle/DP_CFG/data/project_under_test/Lang/Lang_1_buggy"
src_path = "src/main/java"
file_path = os.path.join(project_path , src_path , "org/apache/commons/lang3/math/NumberUtils.java")

file_handle = open(file_path , 'r' , encoding='utf-8')
src_code = file_handle.read()
file_handle.close()


cfg_driver = CFGDriver('java' , src_code , {'statistics': file_path})
testable_methods = cfg_driver.testable_methods
class_obj = cfg_driver.file_obj["class_objects"][0]   # class_object             
# clz_obj = {"class_declaration": {"id": clz_id, "value": clz_node, "name": clz_name}, "fields": fields,
#                   "constructors": constructors, "methods_under_test": []}
class_dec = class_obj["class_declaration"]["value"]   # NODE
print(class_dec)

cfg_driver = CombinedDriver('java' , src_code)
cfg_obj = cfg_driver.file_obj
cfg_node_to_line = cfg_driver.node_id_to_line_number
cfg_line_to_node = cfg_driver.line_number_to_node_id

clz_obj = cfg_obj["class_objects"][0]
method_under_test = clz_obj["methods_under_test"]

method_dest = None
for method in method_under_test:
    if method["method_declaration"]["name"] == "createNumber":
        method_dest = method
        break

pass