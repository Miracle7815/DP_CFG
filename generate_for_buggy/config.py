import os
from rich.logging import RichHandler
import sys
import json
import logging

_config_file = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), 'main_config.json')

with open(_config_file, 'r', encoding='utf-8') as f:
    CONFIG = json.loads(f.read())['generate_for_buggy']

code_base = CONFIG['code_base']

def init_logger(project_name="myproject"):
    '''
        create logger
    '''
    logger = logging.getLogger(project_name)
    logger.setLevel(logging.DEBUG)       # 设置记录器的最低日志等级为 DEBUG。意味着所有等级（DEBUG、INFO、WARNING、ERROR、CRITICAL）都能捕获
    logger.propagate = False   # 🚨 禁止把日志传给 root 多层日志环境中可能被打印多次

    # 清理旧 handler，避免重复打印  避免多次初始化
    logger.handlers.clear()

    handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,   # 关掉时间
        show_path=True,   # 关掉路径
        show_level=True    # 只保留彩色等级 + message
    )

    # RichHandler 自己控制格式，这里只留 message 消息本身，不打印其他信息
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    return logger

def log_and_print(msg , **kwargs):
    logger.info(msg)
    
logger = init_logger(project_name="generate_for_buggy")