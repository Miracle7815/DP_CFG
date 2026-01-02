import os

data_list_path = os.path.join(os.path.dirname(__file__) , 'data' , 'defects4j_list.txt')
new_data_path = os.path.join(os.path.dirname(__file__) , 'data' , 'defects4j_new_list.txt')

proj_list = []

with open(data_list_path , 'r' , encoding='utf-8') as f:
    content = f.readlines()
    for line in content:
        parts = line.strip().split('_')
        proj_name = '_'.join(parts[:-1])
        proj_list.append(proj_name)

with open(new_data_path , 'w' , encoding='utf-8') as f:
    content = '\n'.join(proj_name for proj_name in proj_list)
    f.write(content)