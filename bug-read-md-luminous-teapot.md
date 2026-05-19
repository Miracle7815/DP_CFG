# 基于多智能体的 Bug-Detecting 测试用例生成 — 设计方案

## Context

目标：对 Defects4J 数据集中每个 bug 的**变更函数**（从 `data_info/*/buggy_fix_info.json` 的 `changed_functions` 提取），在 buggy 版本上生成能检测该 bug 的 JUnit 测试用例。

## 核心约束

- **整个生成和迭代仅在 buggy 版本上进行，fixed 版本完全屏蔽**
- fixed 版本仅用于最终结果评测（验证 buggy失败 + fixed通过），不参与任何迭代
- `data_info` 中的 `buggy_fix_info.json` 仅用于从数据集中**筛选被测函数**，对多智能体流水线**不可见**。ReviewerAgent 不能从中获取任何 bug 信息

---

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    项目级流水线                          │
│                                                         │
│  1. 静态分析 (analyze_project) → 构建代码知识库           │
│  2. 提取被测函数 (process_method_info)                   │
│     从 buggy_fix_info.json 的 changed_functions 筛选      │
│  3. 对每个被测函数:                                       │
│     RequirementAgent → TestAgent → 评测                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                    函数级流水线                          │
│                                                         │
│  RequirementAgent                    TestAgent           │
│  ┌──────────────┐                    ┌──────────────┐    │
│  │ Phase1: 工具 │                    │ Phase1: 生成  │    │
│  │ 调用收集上下文│                    │ 测试脚手架    │    │
│  └──────┬───────┘                    └──────┬───────┘    │
│         │                                   │            │
│  ┌──────▼───────┐                    ┌──────▼───────┐    │
│  │ Phase2: 生成  │                    │ 编译验证     │    │
│  │ 需求文档      │                    │ (最多2轮)    │    │
│  └──────┬───────┘                    └──────┬───────┘    │
│         │                                   │            │
│  ┌──────▼───────┐  ┌─────────────┐  ┌──────▼───────┐    │
│  │ Phase3: 与   │◄─┤ Reviewer    │  │ Phase2: 逐场  │    │
│  │ Reviewer交互 │  │ Agent审核   │  │ 景生成(单轮)  │    │
│  │ 迭代 refinement│ │             │  │ 调用         │    │
│  └──────────────┘  └─────────────┘  │ TestExecutor │    │
│                                     │ 编译+运行     │    │
│  ReviewerAgent 两个角色:              │              │    │
│  - 审核需求文档                       │ Reviewer诊断  │    │
│  - 诊断测试运行失败                   └──────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## Agent 1: RequirementAgent

**文件**: [generate_for_buggy/agents/agent_requirement.py](generate_for_buggy/agents/agent_requirement.py)

**职责**: 分析被测函数，收集代码上下文，生成测试需求文档，与 Reviewer 迭代提升质量。

### 三阶段流程

**Phase 1 — 上下文收集**（工具调用循环）:
- 扫描被测函数代码，识别依赖的类字段、常量、被调用方法
- 通过工具调用按需获取：`search_method_source`（方法源码）、`search_class_skeleton`（类骨架）
- 收集到的上下文存入 `ContextManager`，避免重复调用
- 终止条件：LLM 输出 `[ANALYSIS_COMPLETE]` 或达到最大调用次数（10次）

**Phase 2 — 需求生成**:
- 基于 Javadoc + 方法签名 + 收集的上下文，生成结构化需求文档
- 输出 5 个 section：
  1. Method Intent — 方法应该做什么
  2. Input/Output Specification — 参数语义 + 返回值预期
  3. Logic Rules and Branches — 从 Javadoc/签名推导的业务规则
  4. Boundary Analysis — null/空/极端值处理
  5. **Suspicious Logic** — 代码与 Javadoc 矛盾处、未定义行为、推测的行为（**这是 Reviewer 诊断测试失败时的唯一语义信号**）

**Phase 3 — Reviewer 迭代**:
- 将需求文档提交给 ReviewerAgent
- 收到修改意见后重新生成（最多 `MAX_UPDATE=5` 轮）
- 维护 `history_issue` 避免重复犯同样错误

### 现有代码需修复

- 工具调用后的上下文拼接逻辑（注释掉的大段代码 L386-488）需要清理和正确实现
- 需要把工具调用的结果正确累加到 `ContextManager` 中并在后续 prompt 中使用

### Section 5 (Suspicious Logic) 的关键作用

由于 ReviewerAgent 没有 bug diff 信息，Section 5 成为判别测试失败原因的**核心信号**。RequirementAgent 必须确保 Section 5 的质量：

- `[Contradiction]`：代码实现与 Javadoc 矛盾（高价值 bug 检测候选区域）
- `[Undocumented]`：Javadoc 未定义但代码有实现的行为（需要测试验证）
- `[Inferred]`：基于代码逻辑推测的行为（需要测试确认或推翻）

Reviewer 在诊断时会比对：测试的 AssertionError 是否与 Section 5 中某条预测相匹配。

---

## Agent 2: TestAgent

**文件**: [generate_for_buggy/agents/agent_test.py](generate_for_buggy/agents/agent_test.py)

**职责**: 根据需求文档，在 buggy 版本上生成 JUnit 测试用例。

### 内部工具：TestExecutor（非LLM模块）

**文件**: [generate_for_buggy/agents/test_executor.py](generate_for_buggy/agents/test_executor.py)

TestExecutor 是 TestAgent 调用的**确定性工具函数**，不是独立 Agent。职责：
- `compile_test(test_code, project_loc, test_class_sig)` → `(success, error_output)`
  - 写入测试文件到项目 test 目录，调用 Maven 编译
  - 解析编译器输出，提取错误行号和类型
- `run_test(test_class_sig, project_loc)` → `ExecutionResult`
  - 运行测试类，解析 JUnit XML/控制台输出
- 数据结构:
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
    compile_errors: list     # 编译错误详情
    method_results: list[TestMethodResult]
    raw_output: str
```

### 两阶段测试生成（最小化迭代）

**Phase 1 — 生成测试脚手架**（最多2轮迭代）:

输入：需求文档 + 被测方法信息 + 代码知识库

输出：完整的测试类框架，包括：
- 正确的 package 声明和 import（只引入项目中真实存在的类）
- 测试类名（`{ClassName}Test`）
- `@Before` / `@BeforeClass` 初始化方法（如果需要）
- 测试方法签名占位（根据需求文档中的测试场景列出方法名和注释，方法体为空或 `assertTrue(true)` 占位）
- `@After` / `@AfterClass` 清理方法（如果需要）

Prompt 核心要求：确保脚手架**能编译通过**。

验证流程：
```
生成脚手架 → TestExecutor 编译
  ├─ 通过 → 进入 Phase 2
  └─ 失败 → Reviewer 诊断编译错误 → TestAgent 修正 → 重新编译
             (最多1次修正，共2轮)
```

**Phase 2 — 逐场景生成测试方法（单轮，无迭代）**:

从需求文档中提取测试场景：

| 场景来源 | 对应的需求文档 Section | 测试目标 |
|---|---|---|
| 正常路径 | Section 3 (Logic Rules) | 验证方法按预期工作 |
| 边界条件 | Section 4 (Boundary Analysis) | null/空值/极端值处理 |
| 可疑行为 | Section 5 (Suspicious Logic) | **重点：触发 bug 的矛盾点** |

对每个场景，**单轮处理**（无回头修正）:
```
1. TestAgent 生成单个 @Test 方法的代码
2. 将方法注入到已编译通过的脚手架中
3. TestExecutor 编译
   ├─ 通过 → TestExecutor 运行
   │         ├─ ALL_PASSED → 记录为非 bug 检测用例
   │         ├─ ASSERTION_FAILURE → Reviewer 诊断（LLM）
   │         │   判定: likely_bug_detected or test_issue
   │         └─ RUNTIME_ERROR → Reviewer 诊断（LLM）
   │             判定: bug触发的异常 or 测试setup错误
   └─ 失败 → 跳过该场景，继续下一个
             (不回头修正，因为脚手架已验证过import正确性，
              单个@Test方法的编译失败率低，跳过更高效)
```

**为什么逐场景不迭代**：
- 脚手架已验证了 import/package/类结构的正确性
- 单个 `@Test` 方法体内只使用被测类的 public API，这些 API 已通过静态分析确认存在
- 编译失败通常是因为 import 遗漏，但脚手架已处理
- 跳过失败场景比花时间修正更高效——其他场景可能直接成功
- Reviewer 的 LLM 调用只在**运行阶段**使用（判别失败是否是 bug 检测），不浪费在编译修正上

**LLM 调用成本对比**：
- 旧方案（每场景最多5轮迭代）: `5N` 次调用（N=场景数）
- 新方案（脚手架2轮 + 逐场景单轮）: `2 + N` 次调用

---

## Agent 3: ReviewerAgent

**文件**: [generate_for_buggy/agents/agent_reviewer.py](generate_for_buggy/agents/agent_reviewer.py)

**职责**: 两个独立角色 — 审核需求文档 + 诊断测试运行失败。

### 角色 A: 审核需求文档

现有实现，保持基本逻辑不变。审查需求文档的：
- 实现泄露（是否混入了代码实现细节）
- 逻辑矛盾（需求内部是否自相矛盾）
- 覆盖缺失（Javadoc 中提到的行为是否遗漏）

### 角色 B: 诊断测试运行失败（新增）

**方法**: `review_test_failure(requirement_doc, test_code, execution_result)`

**输入上下文**（无 bug diff 信息，仅依赖以下三个信号源）:
1. **需求文档 Section 5 (Suspicious Logic)** — 核心语义信号，记录了代码与 Javadoc 的矛盾点和未定义行为
2. **测试代码** — 断言内容、测试 setup 方式
3. **ExecutionResult** — 异常类型、异常消息、栈跟踪、失败断言行号

**诊断流程（规则 + LLM 混合）**:

```
第一步：确定性分类（非LLM，基于 ExecutionResult）
├── 编译失败
│   └── 诊断: compilation_error
│       → 提取错误行号、错误类型、缺失的符号
│       → 直接返回修复建议（不调用LLM）
├── 所有测试通过
│   └── 诊断: all_passed (非 bug 检测)
├── AssertionError 且栈跟踪终点在测试代码内的断言处
│   └── 进入第二步 LLM 语义判别
│       (测试成功调用了被测方法，但方法返回值/行为与预期不符)
├── RuntimeException 且栈跟踪终点在测试 setup 代码内
│   └── 诊断: test_setup_error
│       → 直接返回修复建议（不调用LLM）
│       (NPE 在测试初始化、找不到构造器参数等)
└── RuntimeException 且栈跟踪终点指向被测方法内部
    └── 进入第二步 LLM 语义判别
        (被测方法内部抛出异常，可能是 bug 触发的异常路径)

第二步：LLM 语义判别（仅对需要判断的运行失败场景）

输入 Prompt 包含：
  - 被测方法的 Javadoc + 代码（已知可能含 bug）
  - 需求文档 Section 5 (Suspicious Logic) 的全部条目
  - 测试代码（断言的预期值 vs 实际值）
  - 失败的异常类型 + 消息 + 栈跟踪

LLM 判别逻辑：
  - 测试的断言失败点是否对应 Section 5 中的某条 [Contradiction] 或 [Undocumented] 预测？
    → 匹配 = likely_bug_detected
  - 栈跟踪是否深入被测方法内部（说明测试成功调用了被测代码）？
    → 是 = 更可能是真实行为异常
  - 异常消息是否与 Section 5 描述的可疑行为一致？
    → 一致 = likely_bug_detected
  - 失败是否是因为测试构造了不合理的输入或错误的 mock？
    → 是 = test_issue

输出 JSON:
{
    "diagnosis": "likely_bug_detected" | "test_issue" | "inconclusive",
    "confidence": 0.0-1.0,
    "reasoning": "判断依据",
    "matched_suspicious_logic": "Section 5 中匹配的条目（如有）",
    "accept_as_bug_detecting": true/false
}
```

**接受为 bug 检测候选的条件**:
- AssertionError + Section 5 中有匹配的 [Contradiction]/[Undocumented] 预测 → 高置信度接受
- AssertionError + 栈跟踪深入被测方法 + 异常消息与 Section 5 描述的可疑行为一致 → 接受
- RuntimeException（指向被测方法内部）+ Section 5 预测了异常路径 → 接受
- 无法与 Section 5 中的任何条目建立关联 → test_issue 或 inconclusive

**关键设计原则**：ReviewerAgent **不猜测** bug 在哪里，它只做一件事——判断测试失败是否与 Section 5 中 RequirementAgent 已经识别出的可疑行为相匹配。如果 Section 5 质量高（经过 Reviewer 角色 A 的审核迭代），诊断准确率就有保障。

---

## Memory 设计

| 层级 | Agent | 内容 | 写入条件 |
|---|---|---|---|
| 方法级 | RequirementAgent | 工具调用结果、Reviewer 历史 issue | 每次交互后 |
| 方法级 | TestAgent | 已生成的测试方法、执行结果、Reviewer 反馈 | 每次迭代后 |
| 项目级 | TestAgent | 成功编译的 import 模式 | 脚手架编译通过后 |
| 项目级 | ReviewerAgent | 已接受的 bug 检测模式 | Reviewer 接受后 |

正确性保证：长期记忆仅在确定性验证后写入

---

## 流水线：run_generate.py

**文件**: [generate_for_buggy/run_generate.py](generate_for_buggy/run_generate.py)

**`run_method()` 新流程**:

```
输入: target_method, project_name, 代码知识库

1. RequirementAgent.write_requirement_for_method(target_method)
   → 产出 requirement_document.txt (含5个section)

2. TestAgent.generate_scaffold(requirement_doc, target_method)
   → 产出 test_class_sig, scaffold_code

3. TestExecutor.compile_test(scaffold_code, buggy_loc, test_class_sig)
   if 编译失败 (最多重试1次):
       ReviewerAgent 诊断编译错误 → TestAgent.fix_scaffold()

4. 从需求文档提取测试场景列表
   for scenario in scenarios:
       a. TestAgent.generate_test_method(scenario, scaffold)
          → test_method_code
       b. 注入 test_method_code 到测试类
       c. TestExecutor.compile_test(更新后的测试类)
          if 编译失败:
              跳过该场景，continue
       d. TestExecutor.run_test(test_class_sig, buggy_loc)
          → ExecutionResult
       e. 根据结果分类:
          - ALL_PASSED: 记录为非 bug 检测
          - ASSERTION_FAILURE / RUNTIME_ERROR:
              ReviewerAgent.review_test_failure(
                  requirement_doc, test_code, execution_result)
              → 判定是否接受为 bug 检测候选

5. 输出完整测试类文件 + 元数据(JSON)
```

**新增导入**:
```python
from .agents.agent_test import TestAgent
from .agents.test_executor import TestExecutor
```

---

## 错误累积防止

1. 每阶段有硬上限（需求审核5轮、脚手架修正2轮、逐场景单轮无迭代）
2. 编译检查点：脚手架必须编译通过才能进入逐场景生成
3. 方法级隔离：每个被测函数独立处理
4. 逐场景跳过策略：编译失败直接跳过，不回头修正，避免单点卡死
5. 项目级记忆只提供参考模式，不携带状态
6. **Section 5 质量保障**：RequirementAgent 的 Section 5 必须经过 Reviewer 角色 A 的审核迭代，确保可疑行为预测的准确性，这是下游诊断的根基

## 验证方式

**开发阶段验证**:
1. 在 Lang_1（NumberUtils.createNumber bug）上跑通全流程
2. 检查生成的测试类是否能编译
3. 检查是否有测试方法在 buggy 上失败且被接受为 bug 检测候选
4. 离线在 fixed 版本上运行，验证"buggy失败+fixed通过"

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
| 重写 | `generate_for_buggy/agents/agent_test.py` |
| 增强 | `generate_for_buggy/agents/agent_reviewer.py` |
| 优化 | `generate_for_buggy/agents/agent_requirement.py` |
| 修改 | `generate_for_buggy/run_generate.py` |
