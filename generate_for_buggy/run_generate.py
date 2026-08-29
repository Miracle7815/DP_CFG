"""Top-level generation orchestrator. The fixed checkout is intentionally absent."""

from __future__ import annotations

import json
import os
import time
from typing import Iterable, Optional

from .agents.agent_requirement import RequirementAgent
from .agents.agent_reviewer import ReviewAgent
from .agents.agent_test import TestAgent, remove_test_method
from .config import CONFIG, logger
from .privacy import audit_checkpoint, audit_summary_since
from .utils.file_operation import create_directory
from .utils.preprocess_project import analyze_project
from .utils.process_project_info import get_callable_method, process_method_info


time_dict = {}


def _save_scenarios(project_name: str, scenario_data: dict) -> None:
    group = project_name.split("_")[0]
    directory = os.path.join(CONFIG["json_res_dir"], group, project_name)
    create_directory(directory)
    with open(os.path.join(directory, "test_scenarios.json"), "w", encoding="utf-8") as handle:
        json.dump(scenario_data, handle, ensure_ascii=False, indent=2)


def _approved_scenarios(scenario_data: dict, origin: Optional[str] = None) -> list[dict]:
    result = []
    for scenario in scenario_data.get("test_scenarios", []):
        if scenario.get("status") != "approved":
            continue
        if origin is not None and scenario.get("origin") != origin:
            continue
        result.append(scenario)
    return result


def run_method(target_method, project_name, all_packages, method_map, class_map, json_writer):
    prompt_audit_checkpoint = audit_checkpoint()

    def write_early_result(reason: str, scenario_file: dict, test_class_sig: str = "") -> None:
        json_writer.write(json.dumps({
            "method_signature": target_method.signature,
            "test_class_sig": test_class_sig,
            "test_class_code": "",
            "scenario_file": scenario_file,
            "scenario_results": {},
            "coverage_history": [],
            "bug_candidates": [],
            "unresolved_coverage_goals": [],
            "witness_references": [],
            "coverage_stop_reason": reason,
            "final_coverage": {"status": "no_final_tests"},
            "prompt_audit": audit_summary_since(prompt_audit_checkpoint),
        }, ensure_ascii=False) + "\n")
        json_writer.flush()
    group = project_name.split("_")[0]
    project_root = os.path.join(CONFIG["mappings"]["buggy_loc"], group, project_name + "_buggy")
    test_root = CONFIG["mappings"]["test"]
    src_root = CONFIG["mappings"]["src"]

    requirement_agent = RequirementAgent(project_name, all_packages, class_map, method_map)
    review_agent = ReviewAgent("scenario")
    review_agent.set_up(all_packages, class_map, method_map)
    test_agent = TestAgent(
        project_name=project_name,
        project_loc=project_root,
        test_loc=test_root,
        src_loc=src_root,
        method_map=method_map,
        class_map=class_map,
    )
    test_agent.set_reviewer_agent(review_agent)
    test_agent.target_method = target_method

    logger.info("========= Initial Scenario Generation =========")
    scenario_data = requirement_agent.write_scenarios_for_method(target_method)
    initial_scenarios = _approved_scenarios(scenario_data, "initial")
    if not initial_scenarios:
        logger.warning(f"No approved initial scenarios for {target_method.signature}")
        write_early_result("no_approved_initial_scenarios", scenario_data)
        return

    context = requirement_agent.context_manager.format_for_prompt()
    test_agent.set_public_context(context)
    test_agent.collect_constructor_info(target_method)
    approved_file = {
        "method_intent": scenario_data.get("method_intent", ""),
        "input_output_spec": scenario_data.get("input_output_spec", {}),
        "test_scenarios": initial_scenarios,
    }
    test_class_sig, scaffold = test_agent.generate_scaffold(approved_file, target_method.import_map)
    if not scaffold:
        logger.warning(f"Could not generate a compiling scaffold for {target_method.signature}")
        write_early_result("scaffold_compilation_failed", scenario_data, test_class_sig)
        return

    scenario_results: dict[str, dict] = {}
    accepted_methods: dict[str, str] = {}
    temporary_probe_methods: dict[str, str] = {}
    for scenario in initial_scenarios:
        logger.info(f"Generating isolated test for {scenario['id']}")
        result = test_agent.generate_test_for_scenario(scenario, scenario_data)
        scenario_results[scenario["id"]] = result
        if result["final_status"] in {"passed", "source_bug"}:
            accepted_methods[scenario["id"]] = result["method_name"]
        elif scenario.get("status") == "approved":
            scenario["status"] = "failed"
        _save_scenarios(project_name, scenario_data)

    coverage_history = []
    unresolved_goals = []
    stop_reason = "no_accepted_initial_tests"
    no_gain_rounds = 0
    previous_coverage_pct = None
    max_iterations = int(CONFIG["pipeline"]["coverage_max_iterations"])
    no_gain_limit = int(CONFIG["pipeline"]["coverage_no_gain_limit"])

    if accepted_methods:
        for coverage_iteration in range(max_iterations):
            logger.info(f"========= Coverage Iteration {coverage_iteration + 1} =========")
            coverage_info = test_agent.coverage_analyzer.collect_and_update(
                target_method,
                test_class_sig,
                scenario_methods=accepted_methods,
            )
            coverage_history.append(coverage_info)
            if coverage_info.get("status") != "ok":
                stop_reason = coverage_info.get("status", "coverage_error")
                break

            branch_pct = coverage_info["branch_coverage_pct"]
            line_pct = coverage_info["line_coverage_pct"]
            coverage_targets = CONFIG.get("coverage", {})
            if (
                line_pct >= float(coverage_targets.get("target_line_pct", 100.0))
                and branch_pct >= float(coverage_targets.get("target_branch_pct", 100.0))
            ):
                stop_reason = "coverage_target_reached"
                break
            current_coverage_pct = (line_pct, branch_pct)
            if (
                previous_coverage_pct is not None
                and line_pct <= previous_coverage_pct[0]
                and branch_pct <= previous_coverage_pct[1]
            ):
                no_gain_rounds += 1
            else:
                no_gain_rounds = 0
            previous_coverage_pct = current_coverage_pct
            if no_gain_rounds >= no_gain_limit:
                stop_reason = "coverage_no_gain_limit"
                break

            goal = coverage_info.get("coverage_goal")
            if goal is None:
                trace_status = coverage_info.get("branch_trace_status")
                stop_reason = "coverage_complete_or_no_frontier" if trace_status == "ok" else trace_status
                break

            goal_id = goal["goal_id"]
            witness = test_agent.coverage_analyzer.witness_binder.vault.get(goal_id)
            if goal.get("support_status") != "supported" or not witness.bindings:
                reason = witness.failure_reason or goal.get("support_status", "unsupported_constraint")
                coverage_info["goal_result"] = {
                    "goal_id": goal_id,
                    "succeeded": False,
                    "reason": reason,
                }
                unresolved_goals.append({"goal_id": goal_id, "reason": reason})
                test_agent.coverage_analyzer.abandon_goal(goal_id, reason)
                continue

            existing_ids = {item["id"] for item in scenario_data.get("test_scenarios", [])}
            scenario_data = requirement_agent.generate_supplement_scenarios(
                existing_scenarios=scenario_data,
                coverage_goal=goal,
                max_rounds=int(CONFIG["pipeline"]["scenario_review_retries"]),
            )
            new_scenarios = [
                item for item in _approved_scenarios(scenario_data, "coverage")
                if item["id"] not in existing_ids and item.get("coverage_goal_id") == goal_id
            ]
            _save_scenarios(project_name, scenario_data)
            if not new_scenarios:
                coverage_info["goal_result"] = {
                    "goal_id": goal_id,
                    "succeeded": False,
                    "reason": "no_reviewed_scenario",
                }
                unresolved_goals.append({"goal_id": goal_id, "reason": "no_reviewed_scenario"})
                test_agent.coverage_analyzer.abandon_goal(goal_id, "no_reviewed_scenario")
                continue

            test_agent.set_coverage_runtime(
                goals=test_agent.coverage_analyzer.public_goals,
                witness_binder=test_agent.coverage_analyzer.witness_binder,
                verifier=lambda requested_goal, method_name, candidate: test_agent.coverage_analyzer.verify_goal(
                    requested_goal,
                    test_class_sig,
                    method_name,
                    candidate,
                ),
            )
            goal_succeeded = False
            for scenario in new_scenarios:
                result = test_agent.generate_test_for_scenario(scenario, scenario_data)
                scenario_results[scenario["id"]] = result
                if result["final_status"] in {"passed", "source_bug"} and result.get("target_hit"):
                    accepted_methods[scenario["id"]] = result["method_name"]
                    goal_succeeded = True
                elif result["final_status"] == "coverage_probe" and result.get("target_hit"):
                    accepted_methods[scenario["id"]] = result["method_name"]
                    temporary_probe_methods[scenario["id"]] = result["method_name"]
                    goal_succeeded = True
                elif scenario.get("status") == "approved":
                    scenario["status"] = "failed"
            coverage_info["goal_result"] = {
                "goal_id": goal_id,
                "succeeded": goal_succeeded,
                "scenario_ids": [item["id"] for item in new_scenarios],
                "executions": {
                    item["id"]: {
                        "final_status": scenario_results[item["id"]].get("final_status"),
                        "target_hit": scenario_results[item["id"]].get("target_hit"),
                        "coverage_delta": scenario_results[item["id"]].get("coverage_delta"),
                    }
                    for item in new_scenarios
                },
            }
            _save_scenarios(project_name, scenario_data)

            if not goal_succeeded:
                unresolved_goals.append({"goal_id": goal_id, "reason": "test_or_target_invalid"})
                test_agent.coverage_analyzer.abandon_goal(goal_id, "test_or_target_invalid")
        else:
            stop_reason = "coverage_iteration_limit"

    for scenario_id, method_name in temporary_probe_methods.items():
        test_agent.test_class_code = remove_test_method(test_agent.test_class_code, method_name)
        accepted_methods.pop(scenario_id, None)
    test_agent.executor.write_test(test_agent.test_class_code, test_class_sig)
    final_coverage = test_agent.coverage_analyzer.collect_metrics_only(
        target_method,
        test_class_sig,
    ) if accepted_methods else {"status": "no_final_tests"}
    _save_scenarios(project_name, scenario_data)
    bug_candidates = [
        {
            "scenario_id": scenario_id,
            "method_name": result.get("method_name", ""),
            "attribution": result.get("attribution"),
        }
        for scenario_id, result in scenario_results.items()
        if result.get("final_status") == "source_bug"
    ]
    witness_references = []
    for goal_id in test_agent.coverage_analyzer.public_goals:
        try:
            reference = test_agent.coverage_analyzer.witness_binder.vault.public_reference(goal_id)
            reference["scenario_ids"] = [
                scenario["id"]
                for scenario in scenario_data.get("test_scenarios", [])
                if scenario.get("coverage_goal_id") == goal_id
            ]
            witness_references.append(reference)
        except KeyError:
            continue

    result_record = {
        "method_signature": target_method.signature,
        "test_class_sig": test_class_sig,
        "test_class_code": test_agent.test_class_code,
        "scenario_file": scenario_data,
        "scenario_results": scenario_results,
        "coverage_history": coverage_history,
        "bug_candidates": bug_candidates,
        "unresolved_coverage_goals": unresolved_goals,
        "witness_references": witness_references,
        "coverage_stop_reason": stop_reason,
        "final_coverage": final_coverage,
        "prompt_audit": audit_summary_since(prompt_audit_checkpoint),
    }
    json_writer.write(json.dumps(result_record, ensure_ascii=False) + "\n")
    json_writer.flush()


def run_project(project_name: str, json_res_dir: str, tmp_test_dir: Optional[str] = None):
    del tmp_test_dir
    group = project_name.split("_")[0]
    result_file = os.path.join(json_res_dir, group, project_name + ".jsonl")
    create_directory(os.path.dirname(result_file))
    time_dict[project_name] = {}

    logger.info(f"Begin static analysis for {project_name}")
    all_packages, method_map, class_map = analyze_project(project_name + "_buggy")
    class_method_map = process_method_info(project_name, CONFIG["mappings"]["src"])
    callable_methods = []
    for class_name, method_infos in class_method_map.items():
        for method_info in method_infos:
            callable = get_callable_method(all_packages, class_name, method_info)
            if callable is not None:
                callable_methods.append(callable)
            else:
                logger.warning(f"Could not resolve exact target {class_name}:{method_info}")

    with open(result_file, "w", encoding="utf-8") as json_writer:
        for index, target_method in enumerate(callable_methods):
            started = time.time()
            logger.info(
                f"Processing {target_method.signature}, {index + 1}/{len(callable_methods)}"
            )
            try:
                run_method(target_method, project_name, all_packages, method_map, class_map, json_writer)
            except Exception as exc:
                logger.error(
                    f"Generation failed for {target_method.signature}: {type(exc).__name__}"
                )
                json_writer.write(json.dumps({
                    "method_signature": target_method.signature,
                    "scenario_results": {},
                    "coverage_history": [],
                    "bug_candidates": [],
                    "unresolved_coverage_goals": [],
                    "witness_references": [],
                    "coverage_stop_reason": "generation_error",
                    "final_coverage": {"status": "generation_error"},
                    "generation_error_type": type(exc).__name__,
                }, ensure_ascii=False) + "\n")
                json_writer.flush()
            time_dict[project_name][target_method.signature] = time.time() - started


run_projcet = run_project


def _configured_projects() -> list[str]:
    from_environment = os.environ.get("DP_CFG_PROJECTS", "")
    if from_environment.strip():
        return [item.strip() for item in from_environment.split(",") if item.strip()]
    configured = CONFIG.get("projects", [])
    if configured:
        return list(configured)
    return []


def run(json_res_dir: str, tmp_test_dir: str, projects: Optional[Iterable[str]] = None):
    selected = list(projects) if projects is not None else _configured_projects()
    if not selected:
        raise RuntimeError("No projects configured. Set DP_CFG_PROJECTS or generate_for_buggy.projects.")
    for project_name in selected:
        run_project(project_name, json_res_dir, tmp_test_dir)


def generate_entry(projects: Optional[Iterable[str]] = None):
    create_directory(CONFIG["json_res_dir"])
    create_directory(CONFIG["tmp_test_dir"])
    logger.debug("Generation begins")
    run(CONFIG["json_res_dir"], CONFIG["tmp_test_dir"], projects)
    logger.debug("Generation completed")
