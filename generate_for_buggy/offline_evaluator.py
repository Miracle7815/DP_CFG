"""Standalone fixed-version evaluator.

This module is deliberately not imported by the generation orchestrator. Its report is
written to a separate file and never feeds prompts, retries, scenarios, or witnesses.
"""

from __future__ import annotations

import argparse
import json
import os

from .agents.coverage_analyzer import CoverageAnalyzer
from .agents.test_executor import TestExecutor
from .config import CONFIG
from .utils.preprocess_project import get_packages


class OfflineEvaluator:
    def __init__(self, fixed_root: str | None = None):
        self.fixed_root = fixed_root or CONFIG["offline_evaluation"]["fixed_loc"]

    def evaluate_project(self, project_name: str, generation_jsonl: str, output_file: str) -> dict:
        group = project_name.split("_")[0]
        fixed_project = os.path.join(self.fixed_root, group, project_name + "_fixed")
        if not os.path.isdir(fixed_project):
            raise FileNotFoundError(f"Fixed checkout not found: {fixed_project}")

        all_packages, _, class_map = get_packages(fixed_project, CONFIG["mappings"]["src"])
        del all_packages
        methods_by_signature = {
            method.signature: method
            for class_obj in class_map.values()
            for method in class_obj.methods
        }
        evaluations = []
        with open(generation_jsonl, "r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]

        for generation in records:
            executor = TestExecutor(
                project_name + "_fixed",
                fixed_project,
                CONFIG["mappings"]["test"],
            )
            test_class_sig = generation["test_class_sig"]
            test_code = generation["test_class_code"]
            compile_ok, compile_output = executor.compile_test(test_code, test_class_sig)
            scenario_evaluations = []
            if compile_ok:
                for scenario_id, scenario_result in generation.get("scenario_results", {}).items():
                    if scenario_result.get("final_status") not in {"passed", "source_bug"}:
                        continue
                    execution = executor.run_test(test_class_sig, scenario_result.get("method_name"))
                    scenario_evaluations.append({
                        "scenario_id": scenario_id,
                        "buggy_status": scenario_result.get("final_status"),
                        "fixed_status": execution.status,
                        "fixed_passed": execution.status == "ALL_PASSED",
                        "bug_revealing_valid": (
                            scenario_result.get("final_status") == "source_bug"
                            and execution.status == "ALL_PASSED"
                        ),
                    })

            target_method = methods_by_signature.get(generation["method_signature"])
            fixed_coverage = {"status": "target_method_not_found"}
            if compile_ok and target_method is not None:
                analyzer = CoverageAnalyzer(
                    project_name + "_fixed",
                    fixed_project,
                    CONFIG["mappings"]["src"],
                    CONFIG["mappings"]["test"],
                )
                fixed_coverage = analyzer.collect_metrics_only(target_method, test_class_sig)

            buggy_history = generation.get("coverage_history", [])
            buggy_coverage = buggy_history[-1] if buggy_history else {}
            evaluations.append({
                "method_signature": generation["method_signature"],
                "compile_ok": compile_ok,
                "compile_diagnostic": "" if compile_ok else compile_output[-3000:],
                "scenarios": scenario_evaluations,
                "buggy_coverage": {
                    "line_coverage_pct": buggy_coverage.get("line_coverage_pct"),
                    "branch_coverage_pct": buggy_coverage.get("branch_coverage_pct"),
                },
                "fixed_coverage": fixed_coverage,
            })

        summary = self._summary(project_name, evaluations)
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        return summary

    @staticmethod
    def _summary(project_name: str, evaluations: list[dict]) -> dict:
        scenarios = [item for evaluation in evaluations for item in evaluation["scenarios"]]
        bug_candidates = [item for item in scenarios if item["buggy_status"] == "source_bug"]
        return {
            "project": project_name,
            "methods": evaluations,
            "accepted_test_count": len(scenarios),
            "fixed_pass_count": sum(item["fixed_passed"] for item in scenarios),
            "fixed_pass_rate": (
                sum(item["fixed_passed"] for item in scenarios) / len(scenarios)
                if scenarios else 0.0
            ),
            "bug_candidate_count": len(bug_candidates),
            "valid_bug_revealing_count": sum(item["bug_revealing_valid"] for item in bug_candidates),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate generated tests on an isolated fixed checkout")
    parser.add_argument("--project", required=True)
    parser.add_argument("--generation-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fixed-root")
    args = parser.parse_args()
    OfflineEvaluator(args.fixed_root).evaluate_project(
        args.project,
        args.generation_jsonl,
        args.output,
    )


if __name__ == "__main__":
    main()
