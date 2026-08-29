"""Temporary-copy branch instrumentation used only for coverage frontier discovery."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Optional

from .test_executor import TestExecutor


TRACE_PACKAGE = "dp.cfg.runtime"
TRACE_CLASS = f"{TRACE_PACKAGE}.CoverageTrace"


@dataclass(frozen=True)
class TraceStep:
    node_id: Any
    outcome: bool


@dataclass
class TraceCollection:
    traces: dict[str, list[TraceStep]]
    status: str
    diagnostic: str = ""


class BranchTraceInstrumenter:
    BRANCH_NODE_TYPES = {
        "if_statement",
        "while_statement",
        "do_statement",
        "for_statement",
        "ternary_expression",
    }

    def instrument(self, target_method) -> tuple[str, dict[str, Any]]:
        source = target_method.belong_file.content
        source_bytes = source.encode("utf-8")
        replacements = []
        branch_to_node = {}
        line_mapping = target_method.belong_file.line_number_to_node_id or {}

        def visit(node):
            if node.type in self.BRANCH_NODE_TYPES:
                condition = node.child_by_field_name("condition")
                if condition is not None:
                    line = condition.start_point[0] + 1
                    mapping = line_mapping.get(line)
                    node_id = mapping[1] if mapping else f"line-{line}"
                    branch_id = f"T{len(branch_to_node)}"
                    branch_to_node[branch_id] = node_id
                    original = source_bytes[condition.start_byte:condition.end_byte].decode("utf-8")
                    inner = original.strip()
                    if inner.startswith("(") and inner.endswith(")"):
                        inner = inner[1:-1]
                        replacement = f"({TRACE_CLASS}.record(\"{branch_id}\", ({inner})))"
                    else:
                        replacement = f"{TRACE_CLASS}.record(\"{branch_id}\", ({inner}))"
                    replacements.append((condition.start_byte, condition.end_byte, replacement.encode("utf-8")))
            for child in node.named_children:
                visit(child)

        visit(target_method.node)
        for start, end, replacement in sorted(replacements, reverse=True):
            source_bytes = source_bytes[:start] + replacement + source_bytes[end:]
        return source_bytes.decode("utf-8"), branch_to_node

    @staticmethod
    def helper_source() -> str:
        return """package dp.cfg.runtime;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;

public final class CoverageTrace {
    private CoverageTrace() {}

    public static synchronized boolean record(String branchId, boolean outcome) {
        String configured = System.getProperty("dp.cfg.trace", "target/dp-cfg/branch-trace.tsv");
        File output = new File(configured);
        File parent = output.getParentFile();
        if (parent != null) {
            parent.mkdirs();
        }
        try {
            FileWriter writer = new FileWriter(output, true);
            writer.write(branchId + "\\t" + (outcome ? "T" : "F") + "\\n");
            writer.close();
        } catch (IOException ignored) {
            // Tracing must never change target behavior.
        }
        return outcome;
    }
}
"""


class BranchTraceCollector:
    def __init__(self, project_name: str, project_loc: str, src_loc: str, test_loc: str):
        self.project_name = project_name
        self.project_loc = os.path.abspath(project_loc)
        self.src_loc = src_loc
        self.test_loc = test_loc
        self.instrumenter = BranchTraceInstrumenter()

    def collect_many(self, target_method, test_class_sig: str,
                     scenario_methods: dict[str, str]) -> TraceCollection:
        if not scenario_methods:
            return TraceCollection({}, "no_test_methods")
        try:
            relative_source = os.path.relpath(target_method.belong_file.file_path, self.project_loc)
            if relative_source.startswith(".."):
                return TraceCollection({}, "source_outside_project")
            instrumented_source, branch_map = self.instrumenter.instrument(target_method)
        except Exception as exc:
            return TraceCollection({}, "instrumentation_error", str(exc))

        with tempfile.TemporaryDirectory(prefix="dp_cfg_trace_") as temp_root:
            clone = os.path.join(temp_root, "project")
            try:
                shutil.copytree(
                    self.project_loc,
                    clone,
                    ignore=shutil.ignore_patterns("target", ".git", ".gradle", "build"),
                )
                source_file = os.path.join(clone, relative_source)
                os.makedirs(os.path.dirname(source_file), exist_ok=True)
                with open(source_file, "w", encoding="utf-8") as handle:
                    handle.write(instrumented_source)

                helper_path = os.path.join(clone, self.src_loc, "dp", "cfg", "runtime", "CoverageTrace.java")
                os.makedirs(os.path.dirname(helper_path), exist_ok=True)
                with open(helper_path, "w", encoding="utf-8") as handle:
                    handle.write(self.instrumenter.helper_source())

                executor = TestExecutor(self.project_name, clone, self.test_loc)
                compiled = executor.adapter.compile()
                if compiled.returncode != 0:
                    return TraceCollection({}, "instrumented_compile_error", compiled.output[-3000:])

                traces = {}
                trace_path = os.path.join(clone, "target", "dp-cfg", "branch-trace.tsv")
                for scenario_id, method_name in scenario_methods.items():
                    if os.path.exists(trace_path):
                        os.remove(trace_path)
                    executor.run_test(test_class_sig, method_name)
                    traces[scenario_id] = self._read_trace(trace_path, branch_map)
                return TraceCollection(traces, "ok")
            except (OSError, shutil.Error) as exc:
                return TraceCollection({}, "trace_collection_error", str(exc))

    @staticmethod
    def _read_trace(trace_path: str, branch_map: dict[str, Any]) -> list[TraceStep]:
        if not os.path.isfile(trace_path):
            return []
        result = []
        with open(trace_path, "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 2 or parts[0] not in branch_map or parts[1] not in {"T", "F"}:
                    continue
                result.append(TraceStep(branch_map[parts[0]], parts[1] == "T"))
        return result

