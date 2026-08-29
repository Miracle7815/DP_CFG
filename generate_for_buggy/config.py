import json
import logging
import os
from copy import deepcopy

from rich.logging import RichHandler


REPOSITORY_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONFIG_FILE = os.environ.get(
    "DP_CFG_CONFIG",
    os.path.join(REPOSITORY_ROOT, "main_config.json"),
)


def _absolute_path(path: str) -> str:
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(REPOSITORY_ROOT, path))


def load_config(config_file: str = _CONFIG_FILE) -> dict:
    with open(config_file, "r", encoding="utf-8") as config_handle:
        config = deepcopy(json.load(config_handle)["generate_for_buggy"])

    for key in ("code_base", "project_info_path", "data_info_path", "json_res_dir", "tmp_test_dir"):
        if key in config:
            config[key] = _absolute_path(config[key])

    mappings = config.setdefault("mappings", {})
    if "buggy_loc" in mappings:
        mappings["buggy_loc"] = _absolute_path(mappings["buggy_loc"])

    offline = config.setdefault("offline_evaluation", {})
    if "fixed_loc" in offline:
        offline["fixed_loc"] = _absolute_path(offline["fixed_loc"])

    coverage = config.setdefault("coverage", {})
    for key in ("jacoco_agent_jar", "jacoco_cli_jar"):
        if coverage.get(key):
            coverage[key] = _absolute_path(coverage[key])

    env_paths = {
        "DP_CFG_BUGGY_ROOT": (mappings, "buggy_loc"),
        "DP_CFG_RESULTS_ROOT": (config, "json_res_dir"),
        "DP_CFG_TMP_TEST_ROOT": (config, "tmp_test_dir"),
        "DP_CFG_DATA_INFO_ROOT": (config, "data_info_path"),
        "DP_CFG_FIXED_ROOT": (offline, "fixed_loc"),
        "DP_CFG_JACOCO_AGENT_JAR": (coverage, "jacoco_agent_jar"),
        "DP_CFG_JACOCO_CLI_JAR": (coverage, "jacoco_cli_jar"),
    }
    for env_name, (section, key) in env_paths.items():
        value = os.environ.get(env_name)
        if value:
            section[key] = _absolute_path(value)

    return config


CONFIG = load_config()
code_base = CONFIG["code_base"]


def init_logger(project_name: str = "generate_for_buggy") -> logging.Logger:
    logger = logging.getLogger(project_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=True,
        show_level=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


logger = init_logger()
