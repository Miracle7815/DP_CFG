"""Method-precise JaCoCo coverage and source-free coverage goal production."""

from __future__ import annotations

import glob
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from ..config import CONFIG, logger
from .branch_trace import BranchTraceCollector
from .symbolic_coverage import CoverageSanitizer, SymbolicPathBuilder, WitnessBinder


@dataclass
class _GoalBaseline:
    target_lines: tuple[int, ...]
    covered_branches: int
    missed_branches: int
    target_key: tuple


class CoverageAnalyzer:
    def __init__(self, project_name: str, project_loc: str, src_loc: str, test_loc: Optional[str] = None):
        self.project_name = project_name
        self.project_loc = os.path.abspath(project_loc)
        self.src_loc = src_loc
        self.symbolic_builder = SymbolicPathBuilder()
        self.sanitizer = CoverageSanitizer()
        self.witness_binder = WitnessBinder()
        self.trace_collector = BranchTraceCollector(
            project_name,
            self.project_loc,
            src_loc,
            test_loc or CONFIG["mappings"]["test"],
        )
        self.private_formulas = {}
        self.public_goals = {}
        self.goal_baselines: dict[str, _GoalBaseline] = {}
        self.attempted_targets: set[tuple] = set()
        self._last_coverage = None
        self._jacoco_diagnostic = ""

    def collect_and_update(
        self,
        target_method,
        test_class_sig: str,
        seed_scenario_id: Optional[str] = None,
        scenario_methods: Optional[dict[str, str]] = None,
    ) -> dict:
        coverage = self._collect_jacoco(target_method, test_class_sig)
        if coverage["status"] != "ok":
            return {
                "status": coverage["status"],
                "diagnostic": coverage.get("diagnostic", ""),
                "line_coverage_pct": 0.0,
                "branch_coverage_pct": 0.0,
                "covered_lines": [],
                "missed_lines": [],
                "coverage_goal": None,
            }

        self._last_coverage = coverage
        covered_lines = {
            line for line, counts in coverage["lines"].items() if counts["covered_instructions"] > 0
        }
        missed_lines = {
            line for line, counts in coverage["lines"].items() if counts["missed_instructions"] > 0
        }
        target_method.covered_lines = covered_lines
        target_method.missed_lines = missed_lines

        line_counter = coverage["counters"].get("LINE", {"covered": 0, "missed": 0})
        branch_counter = coverage["counters"].get("BRANCH", {"covered": 0, "missed": 0})
        line_total = line_counter["covered"] + line_counter["missed"]
        branch_total = branch_counter["covered"] + branch_counter["missed"]

        trace_collection = self.trace_collector.collect_many(
            target_method,
            test_class_sig,
            scenario_methods or {},
        )
        goal = None
        if trace_collection.status == "ok":
            goal = self._build_next_goal(
                target_method,
                coverage,
                trace_collection.traces,
                seed_scenario_id,
            )
        return {
            "status": "ok",
            "line_coverage_pct": self._percentage(line_counter["covered"], line_total),
            "branch_coverage_pct": self._percentage(branch_counter["covered"], branch_total),
            "covered_lines": sorted(covered_lines),
            "missed_lines": sorted(missed_lines),
            "coverage_goal": goal,
            "branch_trace_status": trace_collection.status,
            "branch_trace_diagnostic": trace_collection.diagnostic,
        }

    def collect_metrics_only(self, target_method, test_class_sig: str) -> dict:
        coverage = self._collect_jacoco(target_method, test_class_sig)
        if coverage.get("status") != "ok":
            return coverage
        line_counter = coverage["counters"].get("LINE", {"covered": 0, "missed": 0})
        branch_counter = coverage["counters"].get("BRANCH", {"covered": 0, "missed": 0})
        line_total = line_counter["covered"] + line_counter["missed"]
        branch_total = branch_counter["covered"] + branch_counter["missed"]
        return {
            "status": "ok",
            "line_coverage_pct": self._percentage(line_counter["covered"], line_total),
            "branch_coverage_pct": self._percentage(branch_counter["covered"], branch_total),
        }

    @staticmethod
    def _percentage(covered: int, total: int) -> float:
        return 100.0 if total == 0 else round(100.0 * covered / total, 2)

    def _build_next_goal(self, target_method, coverage: dict, traces: dict,
                         seed_scenario_id: Optional[str]) -> Optional[dict]:
        cfg = target_method.cfg_info or {}
        paths = cfg.get("paths", [])
        node_to_lines = getattr(target_method.belong_file, "node_id_to_line_number", None) or {}
        candidates = []

        for path_entry in paths:
            path_nodes = path_entry.get("path", [])
            covered_prefix = 0
            prefix_open = True
            for index, node in enumerate(path_nodes):
                node_lines = node_to_lines.get(node.get("id"), [])
                line_counts = [coverage["lines"].get(line) for line in node_lines]
                node_covered = any(item and item["covered_instructions"] > 0 for item in line_counts)
                if prefix_open and node_covered:
                    covered_prefix += 1
                else:
                    prefix_open = False

                if node.get("conditional") is None:
                    continue
                branch_lines = [
                    line for line in node_lines
                    if coverage["lines"].get(line, {}).get("missed_branches", 0) > 0
                ]
                if not branch_lines:
                    continue
                target_outcome = bool(node["conditional"])
                key = (node.get("id"), target_outcome)
                if key in self.attempted_targets:
                    continue
                observed_for_target = {
                    step.outcome
                    for trace in traces.values()
                    for step in trace
                    if step.node_id == node.get("id")
                }
                if target_outcome in observed_for_target:
                    continue
                reached_frontier = bool(observed_for_target)
                best_seed = seed_scenario_id
                best_trace_prefix = 0
                for scenario_id, trace in traces.items():
                    prefix = self._trace_prefix_length(path_nodes, index, trace)
                    if prefix > best_trace_prefix:
                        best_trace_prefix = prefix
                        best_seed = scenario_id
                    if any(step.node_id == node.get("id") for step in trace):
                        best_seed = scenario_id
                        best_trace_prefix = index
                        break
                # A reached branch is an actionable frontier: prefer its earliest
                # uncovered exit. For an unreached region, first maximize the real
                # dynamic prefix and then choose the first CFG edge beyond it.
                frontier_order = index if reached_frontier else -best_trace_prefix
                candidates.append((
                    0 if reached_frontier else 1,
                    frontier_order,
                    index,
                    -covered_prefix,
                    str(node.get("id")),
                    path_nodes,
                    index,
                    target_outcome,
                    tuple(branch_lines),
                    key,
                    best_seed,
                ))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[:5])
        _, _, _, _, _, path_nodes, target_index, target_outcome, target_lines, target_key, best_seed = candidates[0]
        formula = self.symbolic_builder.build(
            target_method,
            path_nodes,
            target_index,
            target_outcome,
            best_seed,
        )
        goal = self.sanitizer.sanitize(target_method, formula)
        public_goal = goal.to_public_dict()
        self.private_formulas[goal.goal_id] = formula
        self.public_goals[goal.goal_id] = public_goal
        self.witness_binder.register(
            formula,
            target_method.parameters_list,
            receiver_type=target_method.belong_class.name,
        )

        covered_branches = sum(coverage["lines"][line]["covered_branches"] for line in target_lines)
        missed_branches = sum(coverage["lines"][line]["missed_branches"] for line in target_lines)
        self.goal_baselines[goal.goal_id] = _GoalBaseline(
            target_lines=target_lines,
            covered_branches=covered_branches,
            missed_branches=missed_branches,
            target_key=target_key,
        )
        return public_goal

    @staticmethod
    def _trace_prefix_length(path_nodes: list[dict], target_index: int, trace: list) -> int:
        decisions = [
            (node.get("id"), bool(node.get("conditional")))
            for node in path_nodes[:target_index]
            if node.get("conditional") is not None
        ]
        if not decisions or not trace:
            return 0
        matched = 0
        trace_index = 0
        for node_id, outcome in decisions:
            while trace_index < len(trace):
                step = trace[trace_index]
                trace_index += 1
                if step.node_id == node_id and step.outcome == outcome:
                    matched += 1
                    break
            else:
                break
        return matched

    def verify_goal(self, goal_id: str, test_class_sig: str, method_name: str, _test_code: str = "") -> tuple[bool, dict]:
        if goal_id not in self.goal_baselines:
            return False, {"status": "unknown_coverage_goal"}
        formula = self.private_formulas[goal_id]
        target_method = getattr(formula, "_target_method", None)
        if target_method is None:
            # The owning method is recovered from the last collection; callers use one analyzer per method.
            target_method = getattr(self, "_target_method", None)
        if target_method is None:
            return False, {"status": "missing_target_method"}

        trace_collection = self.trace_collector.collect_many(
            target_method,
            test_class_sig,
            {goal_id: method_name},
        )
        if trace_collection.status != "ok":
            return False, {
                "status": "target_trace_unavailable",
                "trace_status": trace_collection.status,
                "diagnostic": trace_collection.diagnostic,
            }
        target_trace = trace_collection.traces.get(goal_id, [])
        exact_edge_hit = any(
            step.node_id == formula.target_node_id and step.outcome == formula.target_outcome
            for step in target_trace
        )

        # Re-run the generated class so the branch counters remain comparable with the
        # baseline union. Maven is told to ignore assertion failures for coverage only.
        coverage = self._collect_jacoco(target_method, test_class_sig, None)
        if coverage["status"] != "ok":
            return False, {"status": coverage["status"], "diagnostic": coverage.get("diagnostic", "")}

        baseline = self.goal_baselines[goal_id]
        after_covered = sum(
            coverage["lines"].get(line, {}).get("covered_branches", 0)
            for line in baseline.target_lines
        )
        after_missed = sum(
            coverage["lines"].get(line, {}).get("missed_branches", 0)
            for line in baseline.target_lines
        )
        coverage_progress = (
            after_covered > baseline.covered_branches
            or after_missed < baseline.missed_branches
        )
        target_hit = exact_edge_hit and coverage_progress
        self.witness_binder.mark_verified(goal_id, target_hit)
        self.attempted_targets.add(baseline.target_key)
        return target_hit, {
            "status": "target_hit" if target_hit else "target_miss",
            "exact_edge_hit": exact_edge_hit,
            "covered_branch_delta": after_covered - baseline.covered_branches,
            "missed_branch_delta": after_missed - baseline.missed_branches,
        }

    def abandon_goal(self, goal_id: str, reason: str) -> None:
        baseline = self.goal_baselines.get(goal_id)
        if baseline is not None:
            self.attempted_targets.add(baseline.target_key)
        try:
            record = self.witness_binder.vault.get(goal_id)
            record.failure_reason = reason
        except KeyError:
            pass

    def _collect_jacoco(
        self,
        target_method,
        test_class_sig: str,
        method_name: Optional[str] = None,
    ) -> dict:
        self._target_method = target_method
        report = self._run_jacoco(test_class_sig, method_name)
        if report is None:
            return {
                "status": "coverage_unavailable",
                "diagnostic": self._jacoco_diagnostic or "JaCoCo report was not produced",
            }
        return self._parse_jacoco_for_method(report, target_method)

    def _run_jacoco(self, test_class_sig: str, method_name: Optional[str]) -> Optional[str]:
        self._jacoco_diagnostic = ""
        started_at = time.time()
        pom = os.path.join(self.project_loc, "pom.xml")
        selector = test_class_sig if not method_name else f"{test_class_sig}#{method_name}"
        if os.path.isfile(pom):
            command = [
                "mvn",
                "-q",
                f"-Dtest={selector}",
                "-Dmaven.test.failure.ignore=true",
                "org.jacoco:jacoco-maven-plugin:0.8.12:prepare-agent",
                "test",
                "org.jacoco:jacoco-maven-plugin:0.8.12:report",
            ]
            try:
                subprocess.run(
                    command,
                    cwd=self.project_loc,
                    capture_output=True,
                    text=True,
                    timeout=int(CONFIG["pipeline"]["coverage_timeout_seconds"]),
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                self._jacoco_diagnostic = str(exc)
                logger.warning(f"JaCoCo collection failed: {exc}")
        else:
            return self._run_defects4j_jacoco(test_class_sig, method_name)

        candidates = [
            os.path.join(self.project_loc, "target", "site", "jacoco", "jacoco.xml"),
            os.path.join(self.project_loc, "target", "jacoco-report", "jacoco.xml"),
        ]
        candidates.extend(glob.glob(os.path.join(self.project_loc, "**", "jacoco.xml"), recursive=True))
        existing = [
            path for path in candidates
            if os.path.isfile(path) and os.path.getmtime(path) + 1 >= started_at
        ]
        return max(existing, key=os.path.getmtime) if existing else None

    def _run_defects4j_jacoco(
        self,
        test_class_sig: str,
        method_name: Optional[str],
    ) -> Optional[str]:
        agent_jar = CONFIG.get("coverage", {}).get("jacoco_agent_jar", "")
        cli_jar = CONFIG.get("coverage", {}).get("jacoco_cli_jar", "")
        if not agent_jar or not cli_jar:
            self._jacoco_diagnostic = (
                "Defects4J JaCoCo requires DP_CFG_JACOCO_AGENT_JAR and "
                "DP_CFG_JACOCO_CLI_JAR"
            )
            return None
        if not os.path.isfile(agent_jar) or not os.path.isfile(cli_jar):
            self._jacoco_diagnostic = "Configured JaCoCo agent or CLI jar does not exist"
            return None

        timeout = int(CONFIG["pipeline"]["coverage_timeout_seconds"])

        def run(command, env=None):
            return subprocess.run(
                command,
                cwd=self.project_loc,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )

        try:
            compile_result = run(["defects4j", "compile"])
            if compile_result.returncode != 0:
                self._jacoco_diagnostic = compile_result.stdout + compile_result.stderr
                return None

            exported = {}
            for property_name in ("dir.bin.classes", "dir.src.classes"):
                result = run(["defects4j", "export", "-p", property_name])
                if result.returncode != 0 or not result.stdout.strip():
                    self._jacoco_diagnostic = f"Could not export Defects4J property {property_name}"
                    return None
                exported[property_name] = result.stdout.strip().splitlines()[-1]

            output_dir = os.path.join(self.project_loc, "target", "dp-cfg")
            os.makedirs(output_dir, exist_ok=True)
            execution_data = os.path.join(output_dir, "jacoco.exec")
            report = os.path.join(output_dir, "jacoco.xml")
            for stale in (execution_data, report):
                if os.path.isfile(stale):
                    os.remove(stale)

            selector = test_class_sig if not method_name else f"{test_class_sig}::{method_name}"
            test_environment = os.environ.copy()
            agent_option = f"-javaagent:{agent_jar}=destfile={execution_data},append=false"
            existing_options = test_environment.get("JAVA_TOOL_OPTIONS", "").strip()
            test_environment["JAVA_TOOL_OPTIONS"] = " ".join(
                item for item in (existing_options, agent_option) if item
            )
            # A bug-revealing assertion is allowed to return non-zero. The execution
            # data, not the build status, determines whether coverage is available.
            run(["defects4j", "test", "-t", selector], env=test_environment)
            if not os.path.isfile(execution_data):
                self._jacoco_diagnostic = "Defects4J test produced no JaCoCo execution data"
                return None

            classes = os.path.join(self.project_loc, exported["dir.bin.classes"])
            sources = os.path.join(self.project_loc, exported["dir.src.classes"])
            report_result = run([
                "java", "-jar", cli_jar, "report", execution_data,
                "--classfiles", classes,
                "--sourcefiles", sources,
                "--xml", report,
            ])
            if report_result.returncode != 0 or not os.path.isfile(report):
                self._jacoco_diagnostic = report_result.stdout + report_result.stderr
                return None
            return report
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            self._jacoco_diagnostic = str(exc)
            return None

    def _parse_jacoco_for_method(self, jacoco_xml: str, target_method) -> dict:
        try:
            root = ET.parse(jacoco_xml).getroot()
        except (ET.ParseError, OSError) as exc:
            return {"status": "coverage_parse_error", "diagnostic": str(exc)}

        package_name = target_method.belong_package.name.replace(".", "/")
        class_name = target_method.belong_class.name.replace(".", "/")
        target_package = next((item for item in root.findall("package") if item.get("name") == package_name), None)
        if target_package is None:
            return {"status": "target_package_not_found", "diagnostic": package_name}
        target_class = next((item for item in target_package.findall("class") if item.get("name") == class_name), None)
        if target_class is None:
            return {"status": "target_class_not_found", "diagnostic": class_name}

        start_line = min(target_method.line_range) if target_method.line_range else None
        methods = [item for item in target_class.findall("method") if item.get("name") == target_method.name_no_package]
        descriptor = self._method_descriptor(target_method)
        descriptor_matches = [item for item in methods if item.get("desc") == descriptor]
        if descriptor_matches:
            methods = descriptor_matches
        if start_line is not None:
            exact = [item for item in methods if int(item.get("line", -1)) == start_line]
            if exact:
                methods = exact
            elif methods:
                methods.sort(key=lambda item: abs(int(item.get("line", start_line)) - start_line))
                methods = methods[:1]
        if len(methods) != 1:
            return {
                "status": "target_method_not_found" if not methods else "target_method_ambiguous",
                "diagnostic": target_method.signature,
            }
        method_element = methods[0]

        counters = {}
        for counter in method_element.findall("counter"):
            counters[counter.get("type", "")] = {
                "covered": int(counter.get("covered", 0)),
                "missed": int(counter.get("missed", 0)),
            }

        source_name = target_class.get("sourcefilename")
        source_file = next(
            (item for item in target_package.findall("sourcefile") if item.get("name") == source_name),
            None,
        )
        lines = {}
        if source_file is not None:
            for line in source_file.findall("line"):
                number = int(line.get("nr", 0))
                if target_method.line_range and number not in target_method.line_range:
                    continue
                lines[number] = {
                    "missed_instructions": int(line.get("mi", 0)),
                    "covered_instructions": int(line.get("ci", 0)),
                    "missed_branches": int(line.get("mb", 0)),
                    "covered_branches": int(line.get("cb", 0)),
                }
        return {"status": "ok", "counters": counters, "lines": lines}

    @staticmethod
    def _method_descriptor(target_method) -> str:
        parameters = "".join(
            CoverageAnalyzer._type_descriptor(item, target_method)
            for item in target_method.parameters_list
        )
        return f"({parameters}){CoverageAnalyzer._type_descriptor(target_method.return_type, target_method)}"

    @staticmethod
    def _type_descriptor(type_name: str, target_method) -> str:
        type_name = type_name.strip().replace("...", "[]")
        type_name = type_name.split("<", 1)[0].strip()
        dimensions = 0
        while type_name.endswith("[]"):
            dimensions += 1
            type_name = type_name[:-2]
        primitives = {
            "void": "V", "boolean": "Z", "byte": "B", "char": "C", "short": "S",
            "int": "I", "long": "J", "float": "F", "double": "D",
        }
        if type_name in primitives:
            descriptor = primitives[type_name]
        else:
            java_lang = {
                "String", "Boolean", "Byte", "Character", "Short", "Integer", "Long", "Float", "Double",
            }
            if "." not in type_name:
                type_name = target_method.import_map.get(type_name, type_name)
                if "." not in type_name and type_name in java_lang:
                    type_name = "java.lang." + type_name
                elif "." not in type_name:
                    type_name = target_method.belong_package.name + "." + type_name
            descriptor = "L" + type_name.replace(".", "/") + ";"
        return "[" * dimensions + descriptor
