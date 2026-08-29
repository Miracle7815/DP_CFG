# DP_CFG 测试生成流水线

本项目实现按公开契约生成场景、逐场景生成并隔离执行 JUnit 测试、三模型断言归因，以及基于真实覆盖率和动态 CFG 轨迹的补充场景。生成阶段不访问 fixed 版本；fixed 仅由独立离线评估入口使用。Memory 不在本版本范围内。

## 运行顺序

```text
目标方法精确解析
→ RequirementAgent 公开上下文收集与初始场景
→ ReviewAgent 失败关闭审查
→ TestAgent 逐场景生成、方法级执行与修复
→ JaCoCo + 临时副本动态分支轨迹
→ 私有符号路径与脱敏 CoverageGoal
→ 补充场景、Witness 绑定、精确目标边验证
→ 最终产物
→ 独立 OfflineEvaluator
```

断言失败只有在确认到达断言后才进入 `AttributionVoter`。三个不同模型先各自预测公开契约下的正确行为，再各自分类；三份完整票中至少两票一致才会自动判定。多数认为测试有问题时覆盖修改当前测试方法；多数认为源代码有问题时保留该失败测试。票数不足或无多数一律为 `ambiguous`。

## 安装与配置

建议使用 Python 3.10 或 3.11：

```powershell
python -m pip install -r requirements.txt
```

路径、重试次数、覆盖率目标和模型环境变量名位于 `main_config.json`。至少配置以下环境变量：

```text
DP_CFG_SELECTED_MODEL / DP_CFG_SELECTED_API_KEY / DP_CFG_SELECTED_BASE_URL
DP_CFG_REVIEW_MODEL / DP_CFG_REVIEW_API_KEY / DP_CFG_REVIEW_BASE_URL
DP_CFG_ATTRIBUTION_1_MODEL / DP_CFG_ATTRIBUTION_1_API_KEY / DP_CFG_ATTRIBUTION_1_BASE_URL
DP_CFG_ATTRIBUTION_2_MODEL / DP_CFG_ATTRIBUTION_2_API_KEY / DP_CFG_ATTRIBUTION_2_BASE_URL
DP_CFG_ATTRIBUTION_3_MODEL / DP_CFG_ATTRIBUTION_3_API_KEY / DP_CFG_ATTRIBUTION_3_BASE_URL
DP_CFG_PROJECTS
```

三个归因模型必须具有不同的模型配置，否则系统拒绝启动投票。API Key 不写入仓库或结果文件。

Maven 项目直接使用 JaCoCo Maven Plugin。没有标准 `pom.xml` 的 Defects4J 项目还需提供本地 JaCoCo agent 和 CLI：

```text
DP_CFG_JACOCO_AGENT_JAR
DP_CFG_JACOCO_CLI_JAR
```

这些路径也可写入 `main_config.json` 的 `coverage` 段。未配置时覆盖阶段明确返回 `coverage_unavailable`，不会伪造覆盖率。

## 执行

生成 buggy 测试：

```powershell
python main.py
```

生成结束后，单独执行 fixed 离线评估：

```powershell
python -m generate_for_buggy.offline_evaluator `
  --project Lang_1 `
  --generation-jsonl results/detailed_res_info/Lang/Lang_1.jsonl `
  --output results/offline/Lang_1.json
```

运行验证：

```powershell
python -m pytest -q
```

## 隐私边界

- Agent 只能获得签名、Javadoc、无方法体类骨架和公开构造 API。
- 原始 CFG statement、源码行号、私有名称、源码常量和 witness 具体值只存在于本地私有层。
- `CoverageGoal` 只含不透明分支 ID、参数槽、抽象关系与 `κ` 边界符号。
- 自定义对象由测试模型选择公开构造路线，WitnessBinder 只替换隐藏叶子槽并用动态轨迹验证目标边。
- 没有公开 Oracle 的目标只能生成临时 coverage probe；probe 不做断言归因，并会在最终 JUnit 输出前移除。
- 不使用反射或 `Unsafe`；不支持的约束和不可控状态显式停止。
- fixed 评估报告不会回流到场景、Prompt、归因或重试流程。
