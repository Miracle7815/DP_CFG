import time
import os
import json
import traceback
from .config import CONFIG, logger
from .utils.preprocess_project import analyze_project, delete_existing_case_and_save
from .utils.process_project_info import process_method_info, get_callable_method
from .utils.file_operation import create_directory
from .agents.agent_requirement import RequirementAgent
from .agents.agent_reviewer import ReviewAgent
from .agents.agent_test import TestAgent

time_dict = {}

# Pipeline config
MAX_SCENARIO_REVIEW = 5        # Inner loop: scenario review rounds
MAX_COVERAGE_ITERATION = 3     # Outer loop: coverage-driven supplementation rounds


def run_method(target_method, project_name, all_packages, method_map, class_map, json_writer):
    """Full pipeline for a single target method:
    1. RequirementAgent: context collection -> scenario generation -> reviewer iteration
    2. TestAgent: scaffold generation -> per-scenario test generation with iteration
    3. CoverageAnalyzer: coverage analysis -> supplement scenarios -> repeat
    """
    group = project_name.split('_')[0]
    project_root = os.path.join(CONFIG['mappings']['buggy_loc'], group, project_name + "_buggy")
    test_root_dir = CONFIG['mappings']['test']
    src_root_dir = CONFIG['mappings']['src']

    package_name = target_method.get_package_name()

    # ── Initialize agents ──────────────────────────────────────────────
    requirement_agent = RequirementAgent(project_name, all_packages, class_map, method_map)
    review_agent = ReviewAgent('scenario')
    review_agent.set_up(all_packages, class_map, method_map)

    test_agent = TestAgent(
        project_name=project_name,
        project_loc=project_root,
        test_loc=test_root_dir,
        src_loc=src_root_dir,
        method_map=method_map,
        class_map=class_map,
    )
    test_agent.set_reviewer_agent(review_agent)
    test_agent.target_method = target_method

    # ── Step 1: Scenario Generation (Phases 1-3) ───────────────────────
    logger.info(f"========= Scenario Generation =========")
    scenario_data = requirement_agent.write_scenarios_for_method(target_method)

    if not scenario_data.get('test_scenarios'):
        logger.warning(f"No test scenarios generated for {target_method.signature}")
        return

    logger.info(f"Generated {len(scenario_data['test_scenarios'])} initial scenarios")

    # ── Step 2: Test Generation ────────────────────────────────────────
    javadoc = target_method.javadoc if target_method.javadoc is not None else ""
    context_str = requirement_agent.context_manager.format_for_prompt()
    collected_context = [context_str] if context_str else []

    def run_test_generation(scenarios, context_without_source):
        """Generate test scaffold + per-scenario tests.

        Returns (test_class_sig, test_class_code, scenario_results).
        """
        # Generate scaffold
        test_class_sig, scaffold_code = test_agent.generate_scaffold(
            scenarios, target_method.import_map
        )
        if not scaffold_code:
            logger.warning("Failed to generate test scaffold")
            return None, None, {}

        # Generate per-scenario test methods
        scenario_results = {}
        for scenario in scenarios.get('test_scenarios', []):
            logger.info(f"Generating test for scenario {scenario['id']}")
            result = test_agent.generate_test_for_scenario(scenario, scenarios)
            scenario_results[scenario['id']] = result
            logger.info(f"Scenario {scenario['id']} result: {result['status']}")

        return test_class_sig, test_agent.test_class_code, scenario_results

    # ── Outer Loop: Coverage-Driven Supplementation ────────────────────
    coverage_iteration = 0
    all_scenario_results = {}

    for coverage_iteration in range(MAX_COVERAGE_ITERATION):
        logger.info(f"========= Coverage Iteration {coverage_iteration + 1} =========")

        test_class_sig, test_class_code, scenario_results = run_test_generation(
            scenario_data, collected_context
        )

        if test_class_sig is None:
            logger.warning("Test generation failed, stopping coverage loop")
            break

        all_scenario_results.update(scenario_results)

        # Analyze coverage
        logger.info(f"========= Coverage Analysis =========")
        coverage_info = test_agent.coverage_analyzer.collect_and_update(
            target_method, test_class_sig
        )

        logger.info(f"Line coverage: {coverage_info['line_coverage_pct']}%")
        logger.info(f"Branch coverage: {coverage_info['branch_coverage_pct']}%")
        logger.info(f"Missed lines: {coverage_info['missed_lines']}")

        best_path = coverage_info.get('best_path')
        if best_path is None:
            logger.info("No uncovered paths found, coverage loop complete")
            break

        branch_conditions = best_path.get('branch_conditions', [])
        if not branch_conditions:
            logger.info("No uncovered branch conditions, coverage loop complete")
            break

        logger.info(f"Uncovered branch conditions: {len(branch_conditions)}")

        # Supplement scenarios
        logger.info(f"========= Scenario Supplementation =========")
        scenario_data = requirement_agent.generate_supplement_scenarios(
            existing_scenarios=scenario_data,
            branch_conditions=branch_conditions,
        )

        new_scenario_count = len(scenario_data.get('test_scenarios', []))
        logger.info(f"Total scenarios after supplementation: {new_scenario_count}")

    # ── Save Results ─────────────────────────────────────────────────
    group = project_name.split('_')[0]
    dir_path = os.path.join(CONFIG['json_res_dir'], group, project_name)
    create_directory(dir_path)

    # Collect bug-detecting candidates
    bug_candidates = []
    for sid, result in all_scenario_results.items():
        if result.get('status') == 'bug_detected' or result.get('reviewer_diagnosis', {}).get('accept_as_bug_detecting'):
            bug_candidates.append({
                "scenario_id": sid,
                "method_name": result.get('method_name', ''),
                "diagnosis": result.get('reviewer_diagnosis', {}),
            })

    result_record = {
        "method_signature": target_method.signature,
        "test_class_sig": test_class_sig,
        "test_class_code": test_agent.test_class_code,
        "scenario_file": scenario_data,
        "scenario_results": all_scenario_results,
        "coverage_info": {
            "line_coverage_pct": coverage_info.get('line_coverage_pct', 0),
            "branch_coverage_pct": coverage_info.get('branch_coverage_pct', 0),
            "missed_lines": coverage_info.get('missed_lines', []),
        },
        "bug_candidates": bug_candidates,
        "coverage_iterations": coverage_iteration + 1,
    }

    if json_writer:
        json_writer.write(json.dumps(result_record, ensure_ascii=False) + "\n")
        json_writer.flush()

    logger.info(f"Results saved for {target_method.signature}")
    logger.info(f"Bug-detecting candidates: {len(bug_candidates)}")
    logger.info(f"Coverage iterations: {coverage_iteration + 1}")


def run_projcet(project_name, json_res_dir, tmp_test_dir=None):
    group = project_name.split('_')[0]
    json_res_file = os.path.join(json_res_dir, group, project_name + '.jsonl')
    create_directory(os.path.join(json_res_dir, group))

    json_writer = open(json_res_file, "w", encoding='utf-8')

    time_dict[project_name] = {}

    # static analysis
    logger.info(f"Begin static analysis project for {project_name}")
    all_packages, method_map, class_map = analyze_project(project_name + "_buggy")
    logger.info(f"Finish static analysis project for {project_name}")

    # delete exist test
    logger.info(f"Begin deleting existing case in projcet {project_name}")
    delete_existing_case_and_save(project_name + "_buggy", tmp_test_dir)
    logger.info(f"Finish deleting existing case in projcet {project_name}")

    callable_methods = []

    logger.info(f"Collecting target methods...")

    try:
        class_method_map = process_method_info(project_name, CONFIG['mappings']['src'])

        for class_name, method_infos in class_method_map.items():
            for method_info in method_infos:
                callable_method = get_callable_method(all_packages, class_name, method_info)
                if callable_method is None:
                    continue
                callable_methods.append(callable_method)

        logger.info(f"Collect {len(callable_methods)} target methods.")

        for index, target_method in enumerate(callable_methods):
            logger.info(f"Processing target method: {target_method.signature}, {index + 1} / {len(callable_methods)}")

            run_time = time.time()
            run_method(target_method, project_name, all_packages, method_map, class_map, json_writer)
            run_time = time.time() - run_time
            logger.debug(f"Time elapsed: {run_time}")

            time_dict[project_name][target_method.signature] = run_time
            logger.debug(f"Generation for target: {target_method.signature}, {index + 1} / {len(callable_methods)} finished!\n\n")

    except Exception as e:
        print('Exception:', e)
        traceback.print_exc()


def run(json_res_dir, tmp_test_dir):
    todo_list = []
    data_list_path = os.path.join(os.path.dirname(__file__), '..', 'analyse_result', 'no_add_and_delete_result.txt')

    with open(data_list_path, 'r', encoding='utf-8') as f:
        contents = f.readlines()
        for line in contents:
            project_name = line.strip()
            if project_name == "Lang_1":
                todo_list.append(project_name)

    for project_index, project_name in enumerate(todo_list):
        logger.info(f"Begin processing project {project_name}")
        run_projcet(project_name, json_res_dir, tmp_test_dir)
        logger.info(f"Finished project {project_name}\n\n")
        logger.info(f"Collect generated suite and overall coverage\n\n")


def generate_entry():
    timestamp = time.time()
    readable_time = time.strftime('%Y-%m-%d_%H:%M:%S', time.localtime(timestamp))

    code_base = CONFIG['code_base']

    json_res_dir = CONFIG['json_res_dir']
    os.makedirs(json_res_dir, exist_ok=True)

    tmp_test_dir = CONFIG['tmp_test_dir']
    os.makedirs(tmp_test_dir, exist_ok=True)

    logger.debug("Generation begins!")
    run(json_res_dir, tmp_test_dir)
    logger.debug("Generation completed!")
