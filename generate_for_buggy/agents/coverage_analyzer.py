"""
CoverageAnalyzer - Collects JaCoCo coverage, updates target_method.missed_lines
and missed_braches, uses cfg_info.paths + line_number_to_node_id mapping to
find the best uncovered path for generating new test scenarios.
"""
import os
import subprocess
import xml.etree.ElementTree as ET

from ..config import logger


class CoverageAnalyzer:
    """Runs JaCoCo coverage and extracts per-method coverage using cfg_info.paths."""

    def __init__(self, project_name: str, project_loc: str, src_loc: str):
        self.project_name = project_name
        self.project_loc = project_loc
        self.src_loc = src_loc

    def collect_and_update(self, target_method, test_class_sig: str) -> dict:
        """Run JaCoCo tests, parse coverage, update target_method fields,
        and find the best path for generating new test scenarios.

        Returns:
            {
                "line_coverage_pct": float,
                "branch_coverage_pct": float,
                "missed_lines": [int, ...],
                "best_path": {
                    "path_nodes": [...],          # nodes in the best path
                    "branch_conditions": [...],   # {"line": int, "statement": str, "conditional": str}
                },
            }
        """
        # Step 1: run JaCoCo and parse coverage (line + branch counters)
        line_covered, line_missed, branch_covered, branch_missed = \
            self._run_jacoco(test_class_sig, target_method)

        # Step 2: compute covered/missed lines using node_id_to_line_number mapping
        covered_lines, missed_lines_set = self._map_coverage_to_lines(
            target_method, line_covered, line_missed
        )

        target_method.missed_lines = missed_lines_set

        # Step 3: use cfg_info.paths to find the path covering the most missed lines
        best_path_info = self._find_best_path(target_method, missed_lines_set)

        # update missed branches from best path's branch conditions
        missed_branches_set = set()
        if best_path_info:
            for bc in best_path_info["branch_conditions"]:
                missed_branches_set.add((bc["line"], bc["conditional"]))
        target_method.missed_braches = missed_branches_set

        # Step 4: compute coverage percentages
        total_exec = len(covered_lines) + len(missed_lines_set)
        line_pct = (len(covered_lines) / max(total_exec, 1)) * 100
        total_branch = branch_covered + branch_missed
        branch_pct = (branch_covered / max(total_branch, 1)) * 100

        return {
            "line_coverage_pct": round(line_pct, 2),
            "branch_coverage_pct": round(branch_pct, 2),
            "missed_lines": sorted(missed_lines_set),
            "best_path": best_path_info,
        }

    # ── JaCoCo ───────────────────────────────────────────────────────

    def _run_jacoco(self, test_class_sig: str, target_method) -> tuple[int, int, int, int]:
        """Run Maven + JaCoCo. Returns (line_covered, line_missed, branch_covered, branch_missed)."""
        pom = os.path.join(self.project_loc, "pom.xml")
        if not os.path.exists(pom):
            total_lines = len(target_method.line_range)
            return 0, total_lines, 0, 0

        cmd = [
            "mvn", "test", "-q",
            f"-Dtest={test_class_sig}",
            "-Djacoco.outputDir=target/jacoco-report"
        ]
        try:
            subprocess.run(
                cmd, cwd=self.project_loc,
                capture_output=True, text=True, timeout=180
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            total_lines = len(target_method.line_range)
            return 0, total_lines, 0, 0

        report_file = os.path.join(self.project_loc, "target", "jacoco-report", "jacoco.xml")
        if not os.path.exists(report_file):
            total_lines = len(target_method.line_range)
            return 0, total_lines, 0, 0

        return self._parse_jacoco_for_method(report_file, target_method)

    def _parse_jacoco_for_method(self, jacoco_xml: str, target_method) -> tuple[int, int, int, int]:
        """Parse JaCoCo XML LINE and BRANCH counters for the target method's class.
        Returns (line_covered, line_missed, branch_covered, branch_missed).
        """
        try:
            tree = ET.parse(jacoco_xml)
            root = tree.getroot()

            target_class_short = target_method.belong_class.name_no_package
            target_pkg = target_method.belong_package.name.replace(".", "/")

            line_covered = 0
            line_missed = 0
            branch_covered = 0
            branch_missed = 0

            for pkg in root.findall("package"):
                pkg_name = pkg.get("name", "")
                if target_pkg and pkg_name != target_pkg:
                    continue
                for cls in pkg.findall("class"):
                    cls_name = cls.get("name", "")
                    if cls_name != target_class_short:
                        continue
                    for counter in cls.findall("counter"):
                        ctype = counter.get("type")
                        if ctype == "LINE":
                            line_covered = int(counter.get("covered", 0))
                            line_missed = int(counter.get("missed", 0))
                        elif ctype == "BRANCH":
                            branch_covered = int(counter.get("covered", 0))
                            branch_missed = int(counter.get("missed", 0))

            return line_covered, line_missed, branch_covered, branch_missed
        except Exception as e:
            logger.debug(f"Failed to parse JaCoCo XML: {e}")
            total_lines = len(target_method.line_range)
            return 0, total_lines, 0, 0

    # ── Map JaCoCo counts to specific lines via CFG mapping ──────────

    def _map_coverage_to_lines(self, target_method, covered_count: int,
                                missed_count: int) -> tuple[set, set]:
        """Use belong_file's node_id_to_line_number to map JaCoCo coverage counts
        to specific line numbers.

        node_id_to_line_number: {node_id: [line_number, ...]}
        """
        belong_file = target_method.belong_file
        if belong_file is None or belong_file.node_id_to_line_number is None:
            return set(), target_method.line_range

        node_id_to_lines = belong_file.node_id_to_line_number

        # collect all line numbers that are part of the method's CFG
        cfg_lines = set()
        for node_id, line_numbers in node_id_to_lines.items():
            for ln in line_numbers:
                if ln in target_method.line_range:
                    cfg_lines.add(ln)

        if not cfg_lines:
            return set(), target_method.line_range

        # executable lines within CFG
        executable_lines = self._filter_executable_lines(target_method, cfg_lines)
        executable_sorted = sorted(executable_lines)

        # Map: first `covered_count` executable lines are covered, rest are missed.
        covered_lines = set(executable_sorted[:covered_count])
        missed_lines_set = executable_lines - covered_lines

        return covered_lines, missed_lines_set

    def _filter_executable_lines(self, target_method, cfg_lines: set) -> set:
        """Filter to only executable lines (non-blank, non-comment, non-brace)."""
        belong_file = target_method.belong_file
        src_file = None
        if belong_file is not None:
            src_file = belong_file.file_path

        if src_file is None:
            src_file = os.path.join(self.project_loc, self.src_loc,
                                    target_method.belong_class.name.replace(".", os.sep) + ".java")

        if not os.path.exists(src_file):
            return cfg_lines

        with open(src_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        executable = set()
        for line_num in cfg_lines:
            idx = line_num - 1
            if idx < 0 or idx >= len(lines):
                continue
            stripped = lines[idx].strip()
            if (stripped and
                not stripped.startswith("//") and
                not stripped.startswith("/*") and
                not stripped.startswith("*") and
                stripped not in ("{", "}")):
                executable.add(line_num)

        return executable

    # ── Best path from cfg_info (line coverage only) ─────────────────

    def _find_best_path(self, target_method, missed_lines_set: set) -> dict | None:
        """Search all paths in cfg_info to find the one that covers the most missed lines.

        Uses belong_file's node_id_to_line_number for precise node-to-line mapping.
        Only considers line coverage (not branch coverage) when evaluating paths.

        Each path structure:
            {
                "path": [
                    {"id": node_id, "statement": "...", "conditional": None},
                    {"id": node_id, "statement": "...", "conditional": "pos_next"},
                    {"id": node_id, "statement": "...", "conditional": "neg_next"},
                ],
                "true_branches": [...],
                "method_calls_within_class": [...],
                "method_calls_outside_class": [...],
            }
        """
        if target_method.cfg_info is None:
            return None

        belong_file = target_method.belong_file
        if belong_file is None:
            return None

        node_id_to_lines = belong_file.node_id_to_line_number
        if node_id_to_lines is None:
            return None

        cfg = target_method.cfg_info
        paths = cfg.get("paths", [])
        if not paths:
            return None

        best_path_entry = None
        best_missed_count = 0
        best_branch_conditions = []
        best_path_nodes = []

        for path_entry in paths:
            path_nodes = path_entry.get("path", [])
            path_missed_lines = set()
            branch_conditions = []

            for node in path_nodes:
                node_id = node.get("id")
                stmt = node.get("statement", "")
                conditional = node.get("conditional")

                # map node_id to line numbers
                line_numbers = node_id_to_lines.get(node_id, [])
                for ln in line_numbers:
                    if ln in missed_lines_set:
                        path_missed_lines.add(ln)

                # collect branch conditions (for scenario generation context)
                if conditional is not None and stmt and stmt.strip():
                    node_lines = node_id_to_lines.get(node_id, [])
                    line_num = node_lines[0] if node_lines else 0
                    branch_conditions.append({
                        "line": line_num,
                        "statement": stmt.strip(),
                        "conditional": conditional,
                    })

            if len(path_missed_lines) > best_missed_count:
                best_missed_count = len(path_missed_lines)
                best_path_entry = path_entry
                best_branch_conditions = branch_conditions
                best_path_nodes = path_nodes

        if best_path_entry is None:
            return None

        return {
            "path_nodes": best_path_nodes,
            "branch_conditions": best_branch_conditions,
            "true_branches": best_path_entry.get("true_branches", []),
            "method_calls_within_class": best_path_entry.get("method_calls_within_class", []),
            "method_calls_outside_class": best_path_entry.get("method_calls_outside_class", []),
        }
