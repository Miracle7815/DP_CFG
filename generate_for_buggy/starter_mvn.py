import time
import os
import json
import traceback
from .data.config import CONFIG , logger , code_base

time_dict = {}

def run_projcet(project_name, json_res_dir, tmp_test_dir):
    json_res_file = os.path.join(json_res_dir, project_name + '.jsonl')
    json_writer = open(json_res_file, "w",)
    
    time_dict[project_name] = {}  # 记录时间
    
    ## 防止出现之前的结果没有重新写回的情况
    # logger.debug(f"Before static analysis, recover existing case in projcet {project_name}")
    # recovery_existing_case(project_name, tmp_test_dir)
    
    # 通过静态分析提取到项目中的代码调用关系, 以及现有测试程序对应方法的映射
    logger.debug(f"Begin static analysis project for {project_name}")
    # all_packages, method_map, class_map = analyze_project(project_name)
    logger.debug(f"Finish static analysis project for {project_name}")
    
    # # 在项目中把已有的test都删掉，节省编译时间
    # logger.debug(f"Begin deleting existing case in projcet {project_name}")
    # delete_existing_case_and_save(project_name, tmp_test_dir)
    # logger.debug(f"Finish deleting existing case in projcet {project_name}")
    
    # all_callable_methods = []
    # if debugging_mode:
    #     all_packages = [i for i in all_packages if i.name == 'org.llm']
    
    # collect_type = CONFIG['path_mappings'][project_name]['test_scale']   # collect_type = method
    # logger.debug(f"Collecting target methods.")
    # try:
    #     for package_index, single_package in enumerate(all_packages):
    #         package_name = single_package.name
    #         # logger.debug(f"Processing package {package_name}, {package_index + 1} / {len(all_packages)}")

    #         collected_methods = run_package(project_name, single_package, collect_type)
    #         all_callable_methods.extend(collected_methods)
    #     pass

    #     logger.debug(f"Collected {len(all_callable_methods)} target methods.")
    #     for target_index, single_target in enumerate(all_callable_methods):
    #         logger.debug(f"Processing target: {single_target.signature}, {target_index + 1} / {len(all_callable_methods)}")
            
    #         run_time = time.time()
    #         run_method(single_target, project_name, all_packages, method_map, class_map, json_writer, debugging_mode)
    #         run_time = time.time() - run_time
    #         logger.debug(f"Time elapsed: {run_time}")
            
    #         time_dict[project_name][package_name][single_target.signature] = run_time
    #         logger.debug(f"Generation for target: {single_target.signature}, {target_index + 1} / {len(all_callable_methods)} finished!\n\n")

    # except Exception as e:
    #     print('Exception:', e)
    #     traceback.print_exc()
    # finally:
    #     # 把所有的测试程序写回原项目
    #     logger.debug(f"Begin recovery existing case in projcet {project_name}")
    #     recovery_existing_case(project_name, tmp_test_dir)
    #     logger.debug(f"Finish recovery existing case in projcet {project_name}")
    #     pass

def run(json_res_dir , tmp_test_dir):
    todo_projects = [project for project in list(CONFIG['path_mappings'].keys())]
    
    for project_index , project_name in enumerate(todo_projects):
        logger.debug(f"Begin processing project {project_name}")
        
        run_projcet(project_name, json_res_dir, tmp_test_dir)
        
        # with open(done_proj_file, 'a+') as f:
        #     f.write(project_name + '\n')
        logger.debug(f"Finished project {project_name}\n\n")
        logger.debug(f"Collect generated suite and overall coverage\n\n")


def record_time(time_dict, date):
    total_time = 0
    total_method = 0
    
    for project, package_dict in time_dict.items():
        for package, method_dict in package_dict.items():
            for method, time in method_dict.items():
                total_time += time
                total_method += 1
    
    if total_method == 0:
        logger.debug(f"No method processed, return")
        return
    logger.debug(f"Total time elapsed: {total_time}")
    logger.debug(f"Total method processed: {total_method}")
    logger.debug(f"Average time elapsed: {total_time / total_method}")
    
    time_dict['total_time'] = total_time
    time_dict['total_method'] = total_method
    time_dict['average_time'] = total_time / total_method
    
    time_dir = os.path.join(code_base, 'data', 'time')
    if not os.path.exists(time_dir):
        os.makedirs(time_dir)
    time_file = os.path.join(code_base, 'data', 'time', date + '.json')
    with open(time_file, 'w') as f:
        json.dump(time_dict, f)

def generate_entry():
    # 获取当前时间的时间戳
    timestamp = time.time()
    # 将时间戳转换为可读的时间字符串（例如：2023-04-01 12:34:56）
    readable_time = time.strftime('%Y-%m-%d_%H:%M:%S', time.localtime(timestamp))
    
    # 用于标记区分每一次运行
    date = CONFIG['expr_identifier']
    # date = 'test_926'
    # 本项目路径
    code_base = CONFIG['code_base'] # code_base = "/data/WiseUT/coverage_module"
     
    # # 已经跑完的项目
    # done_proj_file = os.path.join(code_base, CONFIG['done_project'])
    
    # 存储所有的大模型生成后的结果
    json_res_dir = os.path.join(code_base, CONFIG['json_res_dir'], date)   # json_res_dir = "/data/WiseUT/coverage_module/data/detailed_res_info"
    os.makedirs(json_res_dir, exist_ok=True)
    
    # 存储所有现有的test case的路径
    tmp_test_dir = os.path.join(code_base, CONFIG['tmp_test_dir'], date)   # tmp_test_dir = "/data/WiseUT/coverage_module/data/tmp_test"
    os.makedirs(tmp_test_dir, exist_ok=True)
    
    logger.debug("Generation begins!")    # logger.debug是让logger输出一条debug等级的日志消息
    run(json_res_dir, tmp_test_dir, debugging_mode=False)
    logger.debug("Generation completed!")
    
    record_time(time_dict, date)

    