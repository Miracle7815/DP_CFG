import json
import os
from collections import defaultdict
from pathlib import Path

def analyze_fixing_types(json_dir):
    """
    分析目录下所有JSON文件的fixing_type信息
    
    Args:
        json_dir: JSON文件所在目录路径
    """
    # 统计数据结构
    stats = {
        'total_files': 0,
        'no_add_and_delete':{
            'total_count': 0,
            'all_names': []
        },
        'add_classes': {
            'total_count': 0,
            'files_with_changes': 0,
            'all_names': []
        },
        'delete_classes': {
            'total_count': 0,
            'files_with_changes': 0,
            'all_names': []
        },
        'add_functions': {
            'total_count': 0,
            'files_with_changes': 0,
            'all_names': []
        },
        'delete_functions': {
            'total_count': 0,
            'files_with_changes': 0,
            'all_names': []
        }
    }
    
    # 按bug分类的详细信息
    details_by_bug = {}
    
    # 遍历所有JSON文件
    json_files = []
    with open('./data/defects4j_list.txt' , 'r' , encoding='utf-8') as f:
        lines = f.readlines()
        for line in lines:
            if line.strip() != "":
                proj_name = line.strip()
                group = proj_name.split('_')[0]
                file_path = os.path.join(json_dir , group , proj_name , "buggy_fix_info.json")
                json_files.append(file_path)

    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 检查是否包含fixing_type
            if 'fixing_type' not in data:
                continue
                
            stats['total_files'] += 1
            fixing_type = data['fixing_type']
            bug_name = data.get('bug_name', str(json_file))
            
            # 存储每个bug的详细信息
            details_by_bug[bug_name] = fixing_type
            
            # 统计各类型
            flag = True
            for change_type in ['add_classes', 'delete_classes', 'add_functions', 'delete_functions']:
                if change_type in fixing_type:
                    num = fixing_type[change_type].get('num', 0)
                    # names = fixing_type[change_type].get('qualified_names', [])
                    
                    stats[change_type]['total_count'] += num
                    if num > 0:
                        flag = False
                        stats[change_type]['files_with_changes'] += 1
                        stats[change_type]['all_names'].append(bug_name)
                    # stats[change_type]['all_names'].extend(names)
            
            if flag:
                stats['no_add_and_delete']['total_count'] += 1
                stats['no_add_and_delete']['all_names'].append(bug_name)

        except json.JSONDecodeError as e:
            print(f"JSON解析错误 {json_file}: {e}")
        except Exception as e:
            print(f"处理文件出错 {json_file}: {e}")
    
    return stats, details_by_bug

def print_summary(stats, details_by_bug):
    """打印统计摘要"""
    print("=" * 60)
    print("FIXING_TYPE 统计摘要")
    print("=" * 60)
    print(f"\n总共分析的JSON文件数: {stats['total_files']}")
    
    print("\n" + "-" * 40)
    print("各类型变更统计:")
    print("-" * 40)
    
    print(f"\n【no_add_and_delete】")
    print(f"  - 总数量: {stats['no_add_and_delete']['total_count']}")
    for name in stats['no_add_and_delete']['all_names']:
        print(name)
    # print(f"  - 涉及文件数: {data['files_with_changes']}")

    for change_type in ['add_classes', 'delete_classes', 'add_functions', 'delete_functions']:
        data = stats[change_type]
        print(f"\n【{change_type}】")
        print(f"  - 总数量: {data['total_count']}")
        print(f"  - 涉及文件数: {data['files_with_changes']}")
        for name in data['all_names']:
            print(name)
        # if data['all_names']:
        #     print(f"  - 所有名称:")
        #     for name in data['all_names']:
        #         print(f"      • {name}")
    
    # 打印有变更的bug详情
    # print("\n" + "=" * 60)
    # print("各Bug的fixing_type详情:")
    # print("=" * 60)
    
    # for bug_name, fixing_type in details_by_bug.items():
    #     has_changes = any(
    #         fixing_type.get(t, {}).get('num', 0) > 0 
    #         for t in ['add_classes', 'delete_classes', 'add_functions', 'delete_functions']
    #     )
    #     if has_changes:
    #         print(f"\n【{bug_name}】")
    #         for change_type in ['add_classes', 'delete_classes', 'add_functions', 'delete_functions']:
    #             if fixing_type.get(change_type, {}).get('num', 0) > 0:
    #                 print(f"  {change_type}: {fixing_type[change_type]['qualified_names']}")

def export_stats(stats):
    for change_type in ['no_add_and_delete' , 'add_classes', 'delete_classes', 'add_functions', 'delete_functions']:
        data = stats[change_type]
        if data['total_count'] > 0:
            with open(f'./analyse_result/{change_type}_result.txt' , 'w' , encoding='utf-8') as f:
                for name in data['all_names']:
                    f.write(name + '\n')

def export_to_csv(stats, details_by_bug, output_file='fixing_type_stats.csv'):
    """导出统计结果到CSV文件"""
    import csv
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # 写入表头
        writer.writerow(['bug_name', 'add_classes_num', 'delete_classes_num', 
                        'add_functions_num', 'delete_functions_num',
                        'add_classes_names', 'delete_classes_names',
                        'add_functions_names', 'delete_functions_names'])
        
        # 写入每个bug的数据
        for bug_name, fixing_type in details_by_bug.items():
            writer.writerow([
                bug_name,
                fixing_type.get('add_classes', {}).get('num', 0),
                fixing_type.get('delete_classes', {}).get('num', 0),
                fixing_type.get('add_functions', {}).get('num', 0),
                fixing_type.get('delete_functions', {}).get('num', 0),
                ';'.join(fixing_type.get('add_classes', {}).get('qualified_names', [])),
                ';'.join(fixing_type.get('delete_classes', {}).get('qualified_names', [])),
                ';'.join(fixing_type.get('add_functions', {}).get('qualified_names', [])),
                ';'.join(fixing_type.get('delete_functions', {}).get('qualified_names', []))
            ])
    
    print(f"\n统计结果已导出到: {output_file}")

def get_todo_project():
    todo_list = []
    with open('./analyse_result/no_add_and_delete_result.txt' , 'r' , encoding='utf-8') as f:
        lines = f.readlines()
        for line in lines:
            if line.strip() != '':
                todo_list.append(line.strip())
    
    # with open('./analyse_result/add_functions_result.txt' , 'r' , encoding='utf-8') as f:
    #     lines = f.readlines()
    #     for line in lines:
    #         if line.strip() != '':
    #             file_name = line.strip()
    #             group = file_name.split('_')[0]
    #             with open(os.path.join('./data_info' , group , file_name , 'buggy_fix_info.json')) as bug_file:
    #                 bug_info = json.load(bug_file)

    #             fix_changes = bug_info["fixing_changes"]
    #             for fix_change in fix_changes:
    #                 flag = False
    #                 for change_method in fix_change['changed_functions'][0]['qualified_names']:
    #                     if change_method in fix_change['changed_functions'][1]['qualified_names']:
    #                         flag = True
    #                 if flag is True:
    #                     todo_list.append(file_name)
    
    # with open()

if __name__ == '__main__':
    # 设置JSON文件目录路径
    json_directory = './data_info'  # 修改为你的JSON文件目录
    
    # 分析fixing_type
    stats, details = analyze_fixing_types(json_directory)
    
    # 打印摘要
    print_summary(stats, details)
    
    # 导出到CSV
    # export_to_csv(stats, details)

    export_stats(stats)

    # get_todo_project()