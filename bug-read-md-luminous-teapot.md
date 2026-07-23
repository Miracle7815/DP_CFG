# 基于多智能体的 Bug-Detecting 测试用例生成 — 设计方案

## Context

目标：对 Defects4J 数据集中每个 bug 的**变更函数**（从 `data_info/*/buggy_fix_info.json` 的 `changed_functions` 提取），在 buggy 版本上生成能检测该 bug 的 JUnit 测试用例，同时追求**高覆盖率**和**bug 发现能力**的双重目标。

## 核心约束

- **整个生成和迭代仅在 buggy 版本上进行，fixed 版本完全屏蔽**
- fixed 版本仅用于最终结果评测（验证 buggy失败 + fixed通过），不参与任何迭代
- `data_info` 中的 `buggy_fix_info.json` 仅用于从数据集中**筛选被测函数**，对多智能体流水线**不可见**
- **RequirementAgent 不提供被测代码（源代码）**，只基于 javadoc、方法签名、工具收集的上下文生成测试场景
- **所有 Prompt 均为英文**

---

## 整体架构：双层闭环

```
┌──────────────────────────────────────────────────────────────┐
│                       外层闭环（覆盖率驱动）                     │
│                                                              │
│  ┌────────────────┐      ┌────────────────┐                   │
│  │ RequirementAgent│─────▶│  TestAgent     │                   │
│  │ 生成测试场景     │      │ 生成测试+执行   │                   │
│  └───────▲────────┘      └───────┬────────┘                   │
│          │                       │                            │
│          │  未覆盖的行/分支       │  覆盖率报告                 │
│          │  补充新场景            │  已通过的测试               │
│          │                       ▼                            │
│          │               ┌────────────────┐                   │
│          └───────────────│ CoverageAnalyzer│                   │
│                          │  分析未覆盖区域   │                   │
│                          └────────────────┘                   │
│                                                              │
│  ┌────────────────┐                                          │
│  │ ReviewerAgent   │ ◀── 评审测试场景（全面性、准确性）          │
│  │ (场景审核)      │      与 RequirementAgent 迭代              │
│  └────────────────┘                                          │
│                                                              │
│  ┌────────────────┐      ┌─────────────────┐                  │
│  │ ReviewerAgent   │─────▶│  Speculation    │                  │
│  │ (失败诊断)      │      │  Sub-Agent      │                  │
│  │                 │◀─────│  (结果推测)      │                  │
│  └────────────────┘      └─────────────────┘                  │
└──────────────────────────────────────────────────────────────┘
```

### 双层迭代

```
内层迭代（场景审核）:
  RequirementAgent 生成场景 → Reviewer 审核 → 不通过则修正 → 重新审核
  （最多 MAX_SCENARIO_REVIEW=5 轮）

外层闭环（覆盖率驱动）:
  初始场景 → TestAgent 生成测试 → 覆盖率分析 → 未覆盖区域 → RequirementAgent 补充场景
  （最多 MAX_COVERAGE_ITERATION=3 轮）
```

---

## Agent 1: RequirementAgent

**文件**: [generate_for_buggy/agents/agent_requirement.py](generate_for_buggy/agents/agent_requirement.py)

**职责**: 收集代码上下文，生成测试场景文件，根据 Reviewer 反馈和覆盖率反馈迭代补充。

**执行策略**: **ReAct (Reasoning + Acting)** — 工具调用循环 + 推理交替进行，直到上下文收集完整后生成最终场景。

### 工作阶段

**Phase 1 — 上下文收集**（ReAct 循环）:

- 输入：**不包含被测代码**，只有方法签名 + javadoc + import 列表
- LLM 在每次迭代中：
  1. **Reasoning**: 分析已知信息，思考还需要什么上下文才能完整理解方法的行为
  2. **Action**: 选择并调用工具获取缺失的上下文
  3. **Observation**: 查看工具返回结果，判断是否还需要更多信息
- 可用工具：
  - `search_class_skeleton(class_name)`: 类的骨架（字段声明 + 方法签名，不含方法体）
  - `search_method_source(class_name, method_name)`: 被调用方法的源码（仅用于理解调用语义）
  - `search_field_definition(class_name, field_name)`: 字段定义
  - `search_use_examples(class_name, method_name)`: 方法在项目中的使用示例
- 收集到的上下文存入 `ContextManager`
- 终止条件：LLM 输出 `[ANALYSIS_COMPLETE]` 或达到最大调用次数（10次）

**Phase 2 — 初始测试场景生成**:

- 输入：javadoc + 方法签名 + 收集的上下文（**不含被测方法源码**）
- 输出：结构化的测试场景文件（JSON 格式）

场景 JSON 结构：
```json
{
  "method_intent": "High-level description of what the method SHOULD do",
  "input_output_spec": {
    "parameters": [{"name": "...", "type": "...", "constraints": "..."}],
    "return_value": {"type": "...", "expected_behavior": "..."}
  },
  "test_scenarios": [
    {
      "id": "S1",
      "type": "normal_path",
      "description": "Normal input produces expected output",
      "input": "Valid hex string '0xFF'",
      "expected_behavior": "Returns the corresponding integer 255",
      "rationale": "Javadoc states: 'Converts string to number'",
      "priority": "high"
    },
    {
      "id": "S2",
      "type": "boundary",
      "description": "Null input handling",
      "input": "parameter = null",
      "expected_behavior": "Returns null or throws IllegalArgumentException",
      "rationale": "Javadoc mentions 'throws IAE if input is null'",
      "priority": "high"
    },
    {
      "id": "S3",
      "type": "suspicious",
      "description": "Behavior with leading zeros",
      "input": "String with leading zeros like '007'",
      "expected_behavior": "[Not Specified in Javadoc] Likely parsed as octal or decimal",
      "rationale": "Javadoc does not specify handling of leading zeros",
      "priority": "medium"
    }
  ]
}
```

场景类型：
- `normal_path`: javadoc 明确描述的正常行为路径
- `boundary`: 边界条件（null、空值、极值）
- `edge_case`: 特殊输入组合或异常路径
- `suspicious`: javadoc 未定义但可能存在问题的行为（**bug 检测关键**）

**Phase 3 — Reviewer 迭代**（场景审核）:
- 将测试场景文件提交给 ReviewerAgent（角色A：场景审核）
- 收到修改意见后重新生成场景（最多 `MAX_SCENARIO_REVIEW=5` 轮）
- 维护 `history_issue` 避免重复同样错误

**Phase 4 — 覆盖率驱动的补充**（外层闭环触发）:
- 接收 Coverage Analyzer 反馈的未覆盖行/分支
- 基于未覆盖区域的上下文生成新的测试场景
- 新场景同样经过 Reviewer 审核

### Prompt 设计

**ReAct 循环 Prompt**:

```
SYSTEM: You are an experienced software test engineer specialized in test scenario analysis. Your task is to analyze a Java method and gather the necessary code context for generating test scenarios.

You will be given:
- Method signature (name, parameters, return type)
- Javadoc documentation
- Import statements

IMPORTANT: You do NOT have access to the method's source code. You must gather context using the available tools.

Available tools:
- search_class_skeleton(class_name): Get class structure (fields + method signatures)
- search_method_source(class_name, method_name): Get source code of a called method
- search_field_definition(class_name, field_name): Get field definition
- search_use_examples(class_name, method_name): Find usage examples

Process:
1. Analyze the method signature and javadoc
2. Identify what context you need (called methods, field types, etc.)
3. Call tools to gather information
4. When you have sufficient context, output [ANALYSIS_COMPLETE]
```

**场景生成 Prompt**:

```
SYSTEM: You are an expert test engineer specializing in test scenario generation. Your task is to generate comprehensive test scenarios for a Java method based on its documentation and code context.

You will be given:
- Method signature
- Javadoc documentation
- Collected context from tool calls (class skeletons, called methods, field definitions)

IMPORTANT: You do NOT have access to the method's source code. Generate scenarios based ONLY on javadoc, method signature, and collected context.

Output a JSON object with:
1. method_intent: High-level summary of what the method should do
2. input_output_spec: Parameter semantics and return value expectations
3. test_scenarios: Array of test scenarios

Each scenario must have:
- id: Unique identifier (S1, S2, ...)
- type: One of "normal_path", "boundary", "edge_case", "suspicious"
- description: What this scenario tests
- input: Concrete input description
- expected_behavior: Expected method behavior/return value
- rationale: Why this scenario is needed (which javadoc rule it covers)
- priority: "high", "medium", or "low"

Scenario types:
- normal_path: Normal behavior explicitly described in javadoc
- boundary: Edge cases (null, empty, extreme values)
- edge_case: Unusual input combinations
- suspicious: Undocumented behaviors that may indicate bugs (mark as [Not Specified] or [Inferred])

Rules:
- Scenarios for normal_path and boundary MUST be derived ONLY from javadoc and method signature
- suspicious scenarios may include inferred behaviors but MUST be clearly marked
- Every scenario must have a specific, testable expected behavior
```

**覆盖率补充场景 Prompt**:

```
SYSTEM: You are generating additional test scenarios to improve code coverage.

You will be given:
- Original test scenarios file
- Uncovered code regions (line numbers, branch conditions, called methods)
- Method signature and javadoc

Task: Generate NEW test scenarios that would exercise the uncovered code regions.

For each uncovered region:
1. Analyze what input conditions might reach that code path
2. Generate a new scenario with type "edge_case" or "suspicious"
3. Clearly mark the rationale as coverage-driven

Output only new scenarios to be appended to the existing scenario file.
```

### 现有代码需修复

- 工具调用后的上下文拼接逻辑（注释掉的大段代码 L386-488）需要清理和正确实现
- Prompt 改为英文，去除对被测方法源码的引用，改为测试场景生成

---

## Agent 2: ReviewerAgent

**文件**: [generate_for_buggy/agents/agent_reviewer.py](generate_for_buggy/agents/agent_reviewer.py)

**职责**: 两个独立角色 — 审核测试场景 + 诊断测试运行失败。

**执行策略**: **结构化输出 + 子 Agent 调用** — 场景审核使用结构化 JSON 输出；测试失败诊断时调用 Speculation Sub-Agent 进行结果推测。

### 角色 A: 审核测试场景

**方法**: `review_test_scenarios(scenario_file, method_signature, javadoc, collected_context)`

**Prompt**:

```
SYSTEM: You are an expert Software Quality Assurance Lead. Your task is to audit a set of test scenarios generated for a Java method.

You will be given:
- Method signature
- Javadoc documentation
- Collected code context
- Test scenarios file

Audit criteria:
1. COMPLETENESS: Do the scenarios cover ALL behaviors described in the javadoc? Are any obvious cases missing?
2. ACCURACY: Are the expected behaviors consistent with the javadoc and method signature? Are any scenarios over-speculating?
3. CONSISTENCY: Are there contradictions between scenarios? (Same input with different expected behaviors)
4. TESTABILITY: Can each scenario be translated into a concrete test case? Are inputs and expected outputs specific enough?

IMPORTANT: Do NOT suggest changes to the Javadoc or source code. Only evaluate the test scenarios.

Output JSON:
{
    "result": "Yes" or "No",
    "issues": [
        {
            "scenario_id": "S2" or null,
            "error_type": "missing_coverage" | "inaccurate_expectation" | "contradiction" | "not_testable",
            "error_analysis": "Specific description of the issue"
        }
    ],
    "suggestions": "Recommendations for additional scenarios"
}

Rules:
- If scenarios are adequate, output "Yes" with empty issues array. Do NOT nitpick on style.
- If rejecting, provide SPECIFIC issues with scenario IDs and actionable feedback.
```

### 角色 B: 诊断测试运行失败

**方法**: `review_test_failure(scenario, test_code, execution_result)`

**输入上下文**（全部来自 buggy 版本，无 bug diff 信息，**不含被测代码**）:
1. **测试场景** — 该测试对应的场景描述（输入、预期行为、类型、rationale）
2. **测试代码** — 断言内容、测试 setup 方式
3. **ExecutionResult** — 异常类型、异常消息、栈跟踪、失败断言行号
4. **被测方法的 javadoc**（不含源码）

**诊断流程**:

```
第一步：确定性分类（非LLM，基于 ExecutionResult）
├── 编译失败
│   └── 诊断: compilation_error
│       → 提取错误行号、错误类型、缺失的符号
│       → 直接返回修复建议（不调用LLM）
├── 所有测试通过
│   └── 诊断: all_passed (该场景验证通过)
├── AssertionError 且栈跟踪终点在测试代码内的断言处
│   └── 进入第二步：调用 Speculation Sub-Agent 进行结果推测
├── RuntimeException 且栈跟踪终点在测试 setup 代码内
│   └── 诊断: test_setup_error
│       → 直接返回修复建议（不调用LLM）
└── RuntimeException 且栈跟踪终点指向被测方法内部
    └── 进入第二步：调用 Speculation Sub-Agent 进行结果推测

第二步：Speculation Sub-Agent 推测 + 三方比对

ReviewerAgent 调用 Speculation Sub-Agent，输入：
  - 测试场景的完整描述（输入 + 预期行为 + 场景类型）
  - 被测方法的 javadoc（**不含源码**）
  - 收集的代码上下文（被调用方法的骨架/源码、字段定义）
  - 测试代码中的断言（预期值）
  - 实际执行结果（异常类型、消息、失败断言行号）

Sub-Agent 任务：
  基于 javadoc 和上下文，独立推测该方法在此输入下应该产生什么行为。
  不与测试的预期比对，而是独立推理。

Sub-Agent 输出:
  - speculation_result: "speculated_behavior" — 推测的方法应该产生的行为
  - reasoning: 推理过程
  - confidence: 推测的置信度

第三步：三方比对（ReviewerAgent 做最终判断）

ReviewerAgent 比较三个结果：
  A = 场景预期行为（scenario.expected_behavior）
  B = Sub-Agent 推测结果（speculation_result）
  C = 实际执行结果（execution_result）

判定规则:
  (1) 如果 B == C (Sub-Agent 推测 = 测试实际结果):
      → ACCEPT as bug_detected
      (独立推测确认了代码行为异常，接受该测试用例)

  (2) 如果 B == A (Sub-Agent 推测 = 场景预期):
      → REJECT, provide fix_instructions for test case
      (独立推测确认场景预期正确，说明测试用例实现有误)

  (3) 如果 B 与 A 和 C 都不一致:
      → REGENERATE: 返回给 TestAgent 重新生成测试
      (推测结果与两者都不符，说明场景本身可能有问题，需要重新设计)

最终输出 JSON:
{
    "diagnosis": "bug_detected" | "test_issue" | "needs_regeneration",
    "confidence": 0.0-1.0,
    "reasoning": "Three-way comparison result and justification",
    "speculation_match": "scenario" | "execution" | "neither",
    "fix_instructions": "Specific changes needed (when test_issue)",
    "accept_as_bug_detecting": true/false
}
```

### Prompt 设计

**Speculation Sub-Agent Prompt**:

```
SYSTEM: You are an independent code behavior analyst. Your task is to PREDICT what a Java method SHOULD return or do for a given input, based ONLY on documentation and context — NOT on the method's source code.

You will be given:
- A test scenario describing the input and the expected behavior from the test author's perspective
- The method's Javadoc (documentation comments)
- Code context: called methods' source code, class skeletons, field definitions
- The test code showing what assertion was made
- The actual execution result (exception type, message, failed assertion line)

IMPORTANT:
- Do NOT look at the target method's source code. It may contain bugs.
- Make an INDEPENDENT prediction of what the method should do.
- Do NOT try to agree with either the test's expectation or the actual result.
- Base your reasoning SOLELY on the Javadoc and the context of called methods.

Task:
1. Read the Javadoc and understand what the method is supposed to do
2. Consider the called methods' behavior (you have their source code)
3. Predict what the correct behavior should be for the given input
4. Compare your prediction with both the test's expectation and the actual result

Output JSON:
{
    "speculated_behavior": "What you believe the method SHOULD return/do",
    "reasoning": "Step-by-step reasoning based on Javadoc and context",
    "confidence": 0.0-1.0,
    "matches_test_expectation": true/false,
    "matches_actual_result": true/false
}
```

**Reviewer 最终判定 Prompt**:

```
SYSTEM: You are making the final decision on a test case that failed at an assertion.

You have three pieces of information:
1. The test SCENARIO's expected behavior: {scenario_expected}
2. An independent SPECULATION of what the method should do: {speculation_result}
3. The ACTUAL execution result: {actual_result}

Decision rules:
- If speculation MATCHES actual result → ACCEPT as bug detected. The independent analysis confirms the code behaves incorrectly.
- If speculation MATCHES scenario expectation → REJECT as test issue. The scenario is correct but the test implementation is wrong. Provide specific fix instructions.
- If speculation matches NEITHER → REGENERATE. The scenario may be flawed or the situation is unclear. Request a new test design.

Output JSON:
{
    "diagnosis": "bug_detected" | "test_issue" | "needs_regeneration",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed justification for the decision",
    "speculation_match": "execution" | "scenario" | "neither",
    "fix_instructions": "Specific changes to make (only for test_issue)",
    "accept_as_bug_detecting": true/false
}
```

---

## Agent 3: TestAgent

**文件**: [generate_for_buggy/agents/agent_test.py](generate_for_buggy/agents/agent_test.py)

**职责**: 根据测试场景文件，在 buggy 版本上生成 JUnit 测试用例，逐场景迭代修复，最终输出完整测试类。

**执行策略**: **分阶段结构化生成** — 脚手架生成、逐场景方法生成、修复生成各自使用独立的 Prompt 模板。

### 内部工具：TestExecutor（非LLM模块）

**文件**: [generate_for_buggy/agents/test_executor.py](generate_for_buggy/agents/test_executor.py)

TestExecutor 是 TestAgent 调用的**确定性工具函数**，不是独立 Agent。职责：
- `compile_test(test_code, project_loc, test_class_sig)` → `(success, error_output)`
  - 写入测试文件到项目 test 目录，调用 Maven 编译
  - 解析编译器输出，提取错误行号和类型
- `run_test(test_class_sig, project_loc)` → `ExecutionResult`
  - 运行测试类，解析 JUnit XML/控制台输出
- `get_coverage(test_class_sig, project_loc, src_loc)` → `CoverageReport`
  - 运行测试并收集覆盖率信息

### 数据结构

```python
@dataclass
class TestMethodResult:
    method_name: str
    passed: bool
    exception_type: str | None
    exception_message: str | None
    stack_trace: str | None

@dataclass
class ExecutionResult:
    status: str              # COMPILATION_ERROR / ASSERTION_FAILURE / RUNTIME_ERROR / ALL_PASSED
    compile_errors: list
    method_results: list[TestMethodResult]
    raw_output: str

@dataclass
class CoverageReport:
    covered_lines: set[int]
    uncovered_lines: list[int]
    covered_branches: set[str]
    uncovered_branches: list[str]
    line_coverage_pct: float
    branch_coverage_pct: float
```

### 三阶段测试生成

**Phase 1 — 生成测试脚手架**:

输入：测试场景文件 + 被测方法信息 + 代码知识库

输出：完整的测试类框架

**Prompt**:

```
SYSTEM: You are an expert Java test engineer. Your task is to generate a JUnit test class SKELETON that compiles successfully.

You will be given:
- Test scenarios file
- Target method signature
- Target class name
- Available imports from the project

Generate a Java test class with:
1. Correct package declaration (same package as the target class)
2. Only imports that exist in the project (use the provided import map)
3. Test class name: {ClassName}Test
4. @Before method for common test setup (if needed)
5. Empty @Test method stubs for each scenario (method name + comment describing the scenario, body is empty or assertTrue(true))
6. @After method (if cleanup is needed)

IMPORTANT:
- The skeleton MUST compile. Do not use classes or methods that don't exist.
- Do NOT implement the test logic yet — just create the structure.
- Use JUnit 4 annotations (@Test, @Before, @After).

Output the complete Java code enclosed in ```java ... ```
```

验证流程：生成脚手架 → TestExecutor 编译 → 通过则进入 Phase 2，失败则修正后重试（最多2轮）。

**Phase 2 — 逐场景生成测试用例**（带迭代修复）:

对每个场景:

**Step a — 生成测试方法 Prompt**:

```
SYSTEM: You are an expert Java test engineer. Generate a single JUnit @Test method for a specific test scenario.

You will be given:
- The test scenario (id, type, description, input, expected_behavior)
- The target method signature
- The test class so far (with other test methods)

Generate ONE @Test method that:
1. Constructs the input as described in the scenario
2. Calls the target method with the constructed input
3. Asserts the expected behavior using Assert.assertEquals, Assert.assertTrue, etc.
4. Has a clear method name describing what is tested (e.g., testValidHexInput_returnsCorrectInteger)

Rules:
- Use only classes and methods available in the test class's imports
- Handle necessary setup (object instantiation, etc.)
- The method must be self-contained and independent

Output only the @Test method code enclosed in ```java ... ```
```

**Step b — 注入 + 编译 + 运行 迭代**:

```
for scenario in test_scenarios:
    a. TestAgent 生成单个 @Test 方法
    b. 注入到测试类
    c. MAX_METHOD_ROUNDS = 3 迭代:
        i.   TestExecutor 编译
        ii.  if 编译失败:
                 提取编译错误 → TestAgent.fix_test_method(compilation_errors) → 重试
        iii. if 编译通过:
                 TestExecutor 运行 → ExecutionResult
                 if ALL_PASSED:
                     标记场景为 passed，break
                 if ASSERTION_FAILURE:
                     进入 ReviewerAgent 诊断流程（含 Speculation Sub-Agent）
                     → bug_detected: 接受
                     → test_issue: TestAgent.fix_test_method(fix_instructions) → 重试
                     → needs_regeneration: TestAgent.regenerate_test_method() → 重试
                 if RUNTIME_ERROR:
                     进入 ReviewerAgent 诊断流程
                     → 同上述判定
        iv.  if 超过 MAX_METHOD_ROUNDS:
                 标记场景为 failed
```

**Step c — 修复方法 Prompt**（仅在 test_issue 时使用）:

```
SYSTEM: Your test method failed to compile or run. Fix it based on the feedback.

Previous method code:
```java
{previous_method_code}
```

Feedback:
{fix_instructions}

Generate the corrected @Test method. Output only the method code.
```

**Step d — 重新生成 Prompt**（仅在 needs_regeneration 时使用）:

```
SYSTEM: Your test method design needs to be rethought. The previous approach did not correctly test the scenario.

Scenario: {scenario_description}
Previous method: {previous_method_code}
Reviewer feedback: {feedback}

Generate a completely new @Test method with a different approach to test this scenario.
```

---

## Coverage Analyzer

**文件**: [generate_for_buggy/agents/coverage_analyzer.py](generate_for_buggy/agents/coverage_analyzer.py)

**方法**:
- `collect_coverage(test_class_sig, project_loc, src_loc)` → `CoverageReport`
  - 使用 JaCoCo 或其他 Java 覆盖率工具
  - 编译项目（带覆盖率 agent）→ 运行测试 → 解析覆盖率报告
- `get_uncovered_context(uncovered_lines, class_map, method_map)` → `list[dict]`
  - 对每个未覆盖的行，查找其所在方法、分支条件、被调用方法
  - 为 RequirementAgent 提供结构化上下文

---

## 完整流水线：run_generate.py

**文件**: [generate_for_buggy/run_generate.py](generate_for_buggy/run_generate.py)

**`run_method()` 新流程**:

```
输入: target_method, project_name, 代码知识库

=== 初始场景生成 ===
1. RequirementAgent 上下文收集（ReAct 工具调用循环）
2. RequirementAgent 生成初始测试场景文件 → test_scenarios.json
3. ReviewerAgent 审核测试场景（角色A）
   if 不通过:
       RequirementAgent 修正场景 → 重新审核 (最多 5 轮)

=== 测试生成 ===
4. TestAgent 生成测试脚手架 → test_class_sig, scaffold_code
5. TestExecutor.compile_test(scaffold_code)
   if 编译失败 (最多重试1次):
       TestAgent 修正脚手架

6. 逐场景生成测试用例:
   for scenario in test_scenarios:
       a. TestAgent.generate_test_method(scenario)
       b. 注入到测试类
       c. MAX_METHOD_ROUNDS=3 迭代:
           - 编译 → 运行 → 分类处理
           - ASSERTION_FAILURE: ReviewerAgent 诊断
               → 调用 Speculation Sub-Agent 推测
               → 三方比对判定:
                 * Sub-Agent 推测 == 实际结果 → accept as bug_detected
                 * Sub-Agent 推测 == 场景预期 → test_issue, 修正测试方法
                 * 都不匹配 → needs_regeneration, 重新设计测试
       d. 记录场景结果 (passed / bug_detected / test_issue / failed)

=== 外层闭环 ===
7. CoverageReport = TestExecutor.get_coverage(test_class_sig, buggy_loc, src_loc)

8. if 覆盖率不达标 and 外层迭代次数 < MAX_COVERAGE_ITERATION (3轮):
       a. CoverageAnalyzer 提取未覆盖区域的上下文
       b. RequirementAgent 基于未覆盖区域生成补充场景
       c. ReviewerAgent 审核补充场景
       d. 回到步骤 6，继续逐场景生成
       e. 外层迭代次数 +1

9. 输出完整测试类文件 + 测试场景文件 + 元数据(JSON)
   记录: 每个场景的状态、bug 检测候选列表、最终覆盖率
```

**新增导入**:
```python
from .agents.agent_test import TestAgent
from .agents.test_executor import TestExecutor
from .agents.coverage_analyzer import CoverageAnalyzer
```

---

## Memory 设计

| 层级 | Agent | 内容 | 写入条件 |
|---|---|---|---|
| 方法级 | RequirementAgent | 工具调用结果、Reviewer 历史审核意见 | 每次交互后 |
| 方法级 | TestAgent | 已生成的测试方法、执行结果、Reviewer 诊断反馈 | 每次迭代后 |
| 方法级 | 闭环共享 | 已通过的测试场景列表、未覆盖区域历史 | 每轮外层迭代后 |
| 项目级 | TestAgent | 成功编译的 import 模式 | 脚手架编译通过后 |
| 项目级 | ReviewerAgent | 已接受的 bug 检测模式 | Reviewer 接受后 |

正确性保证：长期记忆仅在确定性验证后写入

---

## 错误累积防止

1. 每阶段有硬上限（场景审核5轮、脚手架修正2轮、逐场景3轮、外层闭环3轮）
2. 编译检查点：脚手架必须编译通过才能进入逐场景生成
3. 方法级隔离：每个被测函数独立处理
4. 逐场景跳过策略：超过 3 轮修正仍失败的场景直接跳过
5. 项目级记忆只提供参考模式，不携带状态
6. **场景质量保障**：初始场景和补充场景都必须经过 Reviewer 审核

## 验证方式

**开发阶段验证**:
1. 在 Lang_1（NumberUtils.createNumber bug）上跑通全流程
2. 检查生成的测试类是否能编译
3. 检查是否有测试方法被接受为 bug 检测候选
4. 检查最终覆盖率是否达到预期（行覆盖率 > 80%）
5. 离线在 fixed 版本上运行，验证"buggy失败+fixed通过"

**最终评测**（不在迭代中）:
```bash
# 生成（仅 buggy 版本）
python main.py

# 离线评测：对接受的 bug 检测候选，在 fixed 版本上运行
# 统计: buggy失败数, fixed通过数, 两者都失败数, 两者都通过数
```

## 关键文件清单

| 操作 | 文件路径 |
|---|---|
| 新建 | `generate_for_buggy/agents/test_executor.py` |
| 新建 | `generate_for_buggy/agents/coverage_analyzer.py` |
| 重写 | `generate_for_buggy/agents/agent_test.py` |
| 增强 | `generate_for_buggy/agents/agent_reviewer.py` |
| 优化 | `generate_for_buggy/agents/agent_requirement.py` |
| 修改 | `generate_for_buggy/run_generate.py` |
