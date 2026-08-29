from types import SimpleNamespace

from generate_for_buggy.agents.branch_trace import TraceCollection, TraceStep
from generate_for_buggy.agents.coverage_analyzer import CoverageAnalyzer, _GoalBaseline
from generate_for_buggy.agents.symbolic_coverage import WitnessBinder
from generate_for_buggy.contracts import PrivatePathFormula, WitnessRecord


def method():
    return SimpleNamespace(
        belong_package=SimpleNamespace(name="demo"),
        belong_class=SimpleNamespace(name="demo.Calc"),
        name_no_package="f",
        parameters_list=["int"],
        return_type="int",
        line_range={10, 11},
        signature="demo#Calc#f(java.lang#Integer)",
        import_map={},
    )


def test_jacoco_parser_selects_exact_overload_and_method_lines(tmp_path):
    report = tmp_path / "jacoco.xml"
    report.write_text(
        """<report name="fixture">
        <package name="demo">
          <class name="demo/Calc" sourcefilename="Calc.java">
            <method name="f" desc="(Ljava/lang/String;)I" line="20">
              <counter type="LINE" missed="9" covered="0"/>
            </method>
            <method name="f" desc="(I)I" line="10">
              <counter type="LINE" missed="1" covered="1"/>
              <counter type="BRANCH" missed="1" covered="1"/>
            </method>
          </class>
          <sourcefile name="Calc.java">
            <line nr="10" mi="0" ci="3" mb="1" cb="1"/>
            <line nr="11" mi="2" ci="0" mb="0" cb="0"/>
            <line nr="20" mi="9" ci="0" mb="0" cb="0"/>
          </sourcefile>
        </package>
        </report>""",
        encoding="utf-8",
    )
    analyzer = object.__new__(CoverageAnalyzer)
    parsed = analyzer._parse_jacoco_for_method(str(report), method())
    assert parsed["status"] == "ok"
    assert parsed["counters"]["LINE"] == {"covered": 1, "missed": 1}
    assert set(parsed["lines"]) == {10, 11}


def test_descriptor_handles_arrays_and_java_lang_types():
    target = method()
    target.parameters_list = ["String", "int[]"]
    target.return_type = "boolean"
    assert CoverageAnalyzer._method_descriptor(target) == "(Ljava/lang/String;[I)Z"


def test_empty_counter_is_complete_not_zero_percent():
    assert CoverageAnalyzer._percentage(0, 0) == 100.0


def verifier(trace_step):
    analyzer = object.__new__(CoverageAnalyzer)
    formula = PrivatePathFormula(
        goal_id="CG_x",
        target_node_id=8,
        target_outcome=True,
        path_node_ids=(8,),
        path_outcomes=(True,),
        predicates=(),
    )
    analyzer.private_formulas = {"CG_x": formula}
    analyzer.goal_baselines = {
        "CG_x": _GoalBaseline((10,), 0, 1, (8, True)),
    }
    analyzer.attempted_targets = set()
    analyzer._target_method = method()
    analyzer.trace_collector = SimpleNamespace(
        collect_many=lambda *args, **kwargs: TraceCollection(
            {"CG_x": [trace_step]}, "ok"
        )
    )
    analyzer.witness_binder = WitnessBinder()
    analyzer.witness_binder.vault.put(WitnessRecord("W_x", "CG_x", {"ARG_0": 1}))
    analyzer._collect_jacoco = lambda *args, **kwargs: {
        "status": "ok",
        "lines": {10: {"covered_branches": 1, "missed_branches": 0}},
    }
    return analyzer


def test_coverage_counter_delta_without_exact_dynamic_edge_is_not_a_hit():
    analyzer = verifier(TraceStep(8, False))
    hit, detail = analyzer.verify_goal("CG_x", "demo.CalcGeneratedTest", "check")
    assert hit is False
    assert detail["exact_edge_hit"] is False


def test_exact_dynamic_edge_plus_coverage_delta_is_a_hit():
    analyzer = verifier(TraceStep(8, True))
    hit, detail = analyzer.verify_goal("CG_x", "demo.CalcGeneratedTest", "check")
    assert hit is True
    assert detail["exact_edge_hit"] is True


def test_frontier_selection_uses_first_reached_uncovered_exit():
    analyzer = object.__new__(CoverageAnalyzer)
    selected = {}

    class Builder:
        def build(self, target_method, path_nodes, target_index, target_outcome, seed):
            selected["node"] = path_nodes[target_index]["id"]
            return PrivatePathFormula(
                goal_id="CG_frontier",
                target_node_id=path_nodes[target_index]["id"],
                target_outcome=target_outcome,
                path_node_ids=(path_nodes[target_index]["id"],),
                path_outcomes=(target_outcome,),
                predicates=(),
                seed_scenario_id=seed,
            )

    class Sanitizer:
        def sanitize(self, target_method, formula):
            return SimpleNamespace(
                goal_id=formula.goal_id,
                to_public_dict=lambda: {"goal_id": formula.goal_id},
            )

    analyzer.symbolic_builder = Builder()
    analyzer.sanitizer = Sanitizer()
    analyzer.witness_binder = SimpleNamespace(register=lambda *args, **kwargs: None)
    analyzer.private_formulas = {}
    analyzer.public_goals = {}
    analyzer.goal_baselines = {}
    analyzer.attempted_targets = set()

    target = SimpleNamespace(
        signature="demo.Calc#f(int)",
        parameters_list=["int"],
        belong_class=SimpleNamespace(name="demo.Calc"),
        cfg_info={"paths": [{"path": [
            {"id": 1, "conditional": True, "statement": "if (x > 1)"},
            {"id": 2, "conditional": True, "statement": "if (x > 2)"},
        ]}]},
        belong_file=SimpleNamespace(node_id_to_line_number={1: [10], 2: [11]}),
    )
    coverage = {"lines": {
        10: {"covered_instructions": 1, "missed_branches": 1, "covered_branches": 1},
        11: {"covered_instructions": 1, "missed_branches": 1, "covered_branches": 1},
    }}
    traces = {"S1": [TraceStep(1, False), TraceStep(2, False)]}
    analyzer._build_next_goal(target, coverage, traces, None)
    assert selected["node"] == 1
