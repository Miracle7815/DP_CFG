from ..data.config import CONFIG
import os
import subprocess
from cfg.comex.codeviews.combined_graph.combined_driver import CombinedDriver

def generate_cfg_for_method(project_path , method):
    out_put_dir = os.path.join(project_path , "cfg_output" , method.name_no_package)
    # cmd = ["comex",
    #     "--lang", "java",
    #     "--code-file", out_put_dir,
    #     "--graphs", "cfg,dfg"
    # ]

    # result = subprocess.run(cmd , )

    CombinedDriver(
        src_language="java",
        src_code=method.content,
        output_file=os.path.join(out_put_dir , "output.json"),
        graph_format="json",
        codeviews={"DFG" : {"exists" : True} , "AST" : {"exists" : False} , "CFG" : {"exists" : True} }
    )