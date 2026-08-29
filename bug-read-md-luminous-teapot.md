# 基于公开契约、三模型归因与符号覆盖反馈的测试生成系统

## 1. 目标与硬约束

系统针对 Defects4J 变更方法，在 buggy 版本上生成 JUnit 测试，并同时提高覆盖率与 bug 暴露能力。

硬约束如下：

- 生成、修复、归因和覆盖补充阶段只访问 buggy 版本。
- fixed 版本只允许独立 `OfflineEvaluator` 在生成完全结束后访问。
- RequirementAgent 与 TestAgent 看不到目标方法体、源码行号、原始 CFG statement、私有实现和 fixed 信息。
- 场景与 CoverageGoal 不保存具体 Java 输入值；具体 witness 只存在于本地私有层和最终可执行测试中。
- 断言归因必须有三个不同模型的三份完整结果，并采用两阶段独立投票。
- 未知错误、超时、非零退出和缺少测试执行证据绝不判定为通过。
- 不使用反射或 `Unsafe` 构造不可由公开 API 到达的状态。
- 本版本没有 Memory 模块，也不保存跨任务记忆。

## 2. 总体流水线

```text
目标方法精确解析
→ RequirementAgent 收集公开上下文并生成初始场景
→ ReviewAgent 失败关闭审查
→ TestAgent 先生成测试类骨架，再逐场景生成、执行和修复测试方法
→ 形成初始有效测试集
→ JaCoCo 收集方法覆盖率，临时插桩副本收集动态分支轨迹
→ 选择最小覆盖前沿
→ 私有 SymbolicPathBuilder 构造路径公式
→ CoverageSanitizer 生成无源码、无具体值的 CoverageGoal
→ RequirementAgent 生成一个补充场景并重新审查
→ TestAgent 选择公开构造路线，WitnessBinder 本地绑定符号槽
→ 动态轨迹精确验证目标边，同时确认覆盖增量
→ 循环补充
→ 输出测试、场景、执行、归因、覆盖和 Prompt 隐私审计
→ 独立 OfflineEvaluator 使用 fixed 评估
```

## 3. 组件职责

### RequirementAgent

- 通过工具获取签名、Javadoc、无方法体类骨架、公开字段声明、公开方法契约和调用方法签名。
- 生成统一 Schema 的初始场景。
- 只根据脱敏 CoverageGoal 生成覆盖补充场景。
- 被 ReviewAgent 拒绝后修改完整场景文件，最多三轮。

### ReviewAgent

- 审查 Schema、文档依据、Oracle、可测试性、冲突、重复和隐私。
- 解析失败、字段缺失或 Oracle 缺失时失败关闭；唯一例外是明确标记为 `coverage_probe_only` 的临时覆盖探针。
- 不再承担断言归因；旧的单模型归因入口对断言固定返回 `ambiguous`。

### TestAgent

- 查询公开构造器、工厂、Builder、setter、实现类以及项目测试框架摘要。
- 先生成可编译测试类骨架，再逐场景添加一个测试方法。
- 每次只执行当前方法；修复时覆盖当前候选，不积累冲突版本。
- 编译、准备和调用失败根据结构化诊断最多修复三轮。
- 拒绝反射、`Unsafe`、无 Oracle 和占位断言；临时覆盖探针必须无断言，并在最终输出前移除。
- 断言失败委托 AttributionVoter；多数测试问题时修改当前测试，多数源码问题时保留失败测试。

### TestExecutor

- 有标准 `pom.xml` 时使用 Maven，否则使用 Defects4J。
- Maven 使用 `Class#method`，Defects4J 使用 `Class::method` 做方法级隔离。
- 分类为通过、编译失败、断言失败、运行时错误、超时或未知错误。
- 退出码为零仍必须有构建输出或新 Surefire XML 证明当前测试确实运行。

### AttributionVoter

只处理已经到达断言的断言失败。

第一阶段，每个模型独立看到：

- 方法签名；
- 公开 Javadoc 与无源码上下文；
- 场景 ID、类别和输入抽象。

第一阶段隐藏当前 Oracle、断言和实际结果。第二阶段每个模型只看到自己的预测，再获得批准场景、断言和标准化结果，输出以下一种：

- `source_bug`
- `test_oracle_error`
- `test_implementation_error`
- `ambiguous`

三份完整票中至少两票为 `source_bug` 才保留为 bug-revealing 测试；两票属于两类测试问题的并集时修复测试；其他情况均为 `ambiguous`。

### CoverageAnalyzer 与 BranchTraceCollector

- 从 JaCoCo XML 按包、类、方法名、JVM descriptor 和方法起始位置精确定位重载方法。
- 保存真实指令、行和分支计数，不再推断“前 N 行已覆盖”。
- 临时复制 buggy 项目，只在副本中给条件节点包裹无语义变化的分支记录器。
- 每个测试方法独立收集分支 ID、方向和顺序；正式测试仍在原始 buggy 副本执行。
- 目标边命中需要动态轨迹出现精确节点与方向，并同时观察到 JaCoCo 分支增量。

### SymbolicPathBuilder 与 CoverageSanitizer

- 原始 CFG、条件表达式、局部 def-use、源码常量和路径公式始终停留在私有层。
- 使用方法内定义回溯把简单局部变量还原到 `ARG_n`。
- 支持数值线性比较、简单加减偏移、boolean、null、字符串/数组/集合长度、参数相等关系以及 `and/or/not`。
- 对动态已覆盖前缀保持原方向，只翻转目标分支。
- 混合复杂逻辑、反射、native、I/O、复杂别名、不可控状态和无法解析的调用显式返回不支持，不交给 LLM 猜测。
- 公开 CoverageGoal 将常量替换为 `κ`，将分支和对象属性替换为稳定不透明 ID。

### WitnessBinder

- 在私有层联合求解同一参数的多条约束，矛盾时不产生部分 witness。
- 支持数值、boolean、字符串、数组和常见集合长度，以及参数相等/不等关系。
- 自定义对象的隐藏叶子值由 Binder 绑定；TestAgent 根据公开契约选择构造器、工厂、Builder、setter 或嵌套公开访问路线。
- 支持公开 setter、公开字段、构造参数、静态工厂、Builder、嵌套对象叶子以及共享引用配方。
- 接口或抽象类只有一个公开可用实现时才能自动选择；否则停止。
- 生成的对象配方只保存在私有 WitnessRecord，公开输出仅保留 witness ID、目标 ID 和验证状态。

### Prompt 隐私边界

每次模型调用前执行统一隐私校验：

- 拒绝原始 CFG 字段、源码字段、源码位置、fixed 路径和 witness 值字段。
- 若 Prompt 含目标方法体或足够长的目标源码片段，立即终止该调用。
- 不把完整 Prompt 或模型回复写入普通日志；输出只保存 Prompt 哈希、阶段和隐私检查结果。

## 4. 数据契约

### 统一场景 Schema

每个场景必须包含：

- `id`
- `origin`：`initial` 或 `coverage`
- `category`
- `description`
- `input_constraints`
- `expected_behavior_constraints`
- `oracle_basis`
- `priority`
- `status`
- `coverage_goal_id`：仅覆盖场景需要

旧字段 `type`、`input`、`expected_behavior` 被确定性拒绝。约束字段若含数字或引号包围的示例值也会被拒绝。

### 公开 CoverageGoal

仅允许：

- 不透明目标 ID；
- 种子场景 ID；
- 不透明分支序列和方向；
- 目标边；
- `ARG_n` 参数槽；
- 长度、大小、空值、相等关系和不透明对象特征；
- `κ` 边界符号；
- 文档条款 ID；
- 约束支持状态。

禁止具体数字、字符串、枚举值、源码表达式、变量名、源码位置、return、私有方法、实际输出和 witness 内容。

### 场景执行记录

每个场景记录：

- 场景 ID 和测试方法名；
- 编译状态与执行状态；
- 失败阶段和标准化错误类型；
- 重试次数；
- 三模型预测、分类与投票；
- CoverageGoal 命中状态和覆盖增量；
- 最终状态：`passed`、`source_bug`、`test_invalid`、`unconstructable_input`、`uncontrollable_object_state` 或 `ambiguous`。

## 5. 覆盖前沿算法

1. 初始场景全部得到最终状态后才启动覆盖补充。
2. 对动态轨迹已经到达的分支，选择执行顺序中第一个尚未覆盖的出口。
3. 若候选区域从未到达，先最大化真实轨迹与 CFG 候选路径的共同前缀，再选择该前缀之后第一条进入未覆盖区域的边。
4. 不枚举并比较“未覆盖行最多”的完整路径。
5. 内部公式是已覆盖前缀约束与目标分支期望方向的合取。
6. RequirementAgent 每轮只得到一个 CoverageGoal，并只生成与该目标关联的新场景。
7. 测试通过或断言失败都必须先精确证明目标边命中；未命中时先修复构造路线，不做源码缺陷归因。
8. 没有公开 Oracle 时只允许生成 `coverage_probe_only` 临时方法，用于推进动态前缀；它不参与归因，并在最终输出前移除，随后重新计算最终覆盖率。

覆盖循环在以下任一条件发生时停止：

- 达到配置的行与分支覆盖目标；
- 没有可支持前沿；
- 连续两轮无覆盖增长；
- 达到十轮上限。

## 6. 构建与覆盖适配

- Maven：使用 Surefire 方法选择器和 JaCoCo Maven Plugin。
- Defects4J：使用 `defects4j compile/test`；覆盖阶段通过 `JAVA_TOOL_OPTIONS` 加载外部 JaCoCo agent，再用 JaCoCo CLI 生成 XML。
- Defects4J JaCoCo 需要配置 `DP_CFG_JACOCO_AGENT_JAR` 和 `DP_CFG_JACOCO_CLI_JAR`。缺失时返回 `coverage_unavailable`，不回退到伪覆盖率或陈旧报告。
- Gradle 不在首期范围。

## 7. 输出与离线评估

生成输出包含：

- 完整统一场景文件；
- 最终 JUnit 测试类；
- 每场景执行记录；
- 三模型独立预测、分类与投票；
- 每轮覆盖率、CoverageGoal、目标命中和增量；
- 未解决目标及原因；
- witness 与场景/目标的公开引用；
- Prompt 隐私审计哈希。

`OfflineEvaluator` 是单独模块和命令入口。它在 fixed checkout 中编译并逐方法运行最终接受的测试，报告 fixed 通过率、bug-revealing 有效性和 fixed 覆盖率。该报告不会被导入生成编排器，也不会回流到任何 Agent。

## 8. 默认重试与验收

- 场景审查：三轮。
- 测试修复：三轮。
- 自定义对象构造路线：两轮。
- 覆盖补充：十轮。
- 连续无增长停止：两轮。

自动化测试覆盖 Schema、模型独立性、2/3 投票、执行失败关闭、JaCoCo 重载定位、覆盖前沿、布尔逻辑与 def-use、脱敏、数组/集合、自定义 setter/Builder/嵌套对象/共享引用，以及测试问题修改与源码问题保留流程。
