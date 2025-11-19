from ..data.config import CONFIG
import os
import subprocess
from comex.codeviews.combined_graph.combined_driver import CombinedDriver

def get_cfg_dfg_comex(content , output_file , collapsed="" , blacklisted="" , last_def=False , last_use=False):
    codeviews = {
        "AST": {
            "exists": False,
            "collapsed": collapsed,
            "minimized": bool(blacklisted),
            "blacklisted": blacklisted.split(",")
        },
        "DFG": {
            "exists": True,
            "collapsed": collapsed,
            "minimized": False,
            "statements": True,
            "last_def": last_def,
            "last_use": last_use
        },
        "CFG": {
            "exists": True,
        }
    }
    CombinedDriver(
        src_language="java",
        src_code=content,
        output_file=output_file,
        graph_format="json",
        codeviews=codeviews
    )
    
def generate_cfg_for_file(project_path , file):
    out_put_dir = os.path.join(project_path , "cfg_output" , file.file_name.split('.')[0])
    os.makedirs(out_put_dir , exist_ok=True)
    get_cfg_dfg_comex(file.content , os.path.join(out_put_dir , "output.json"))
