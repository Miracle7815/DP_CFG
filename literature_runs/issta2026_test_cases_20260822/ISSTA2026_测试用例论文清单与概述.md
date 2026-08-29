# ISSTA 2026 测试用例相关论文清单与概述

更新日期：2026-08-22

范围：ISSTA 2026 主会 Research Papers 中，标题或摘要明确聚焦测试用例/测试套件生成、测试断言与预言、回归测试选择、测试优先级、测试演化与修复、测试有效性评价或测试数据集的论文。纯 fuzzing、泛化系统测试和仅把测试作为辅助工具的论文不纳入核心清单。

说明：ISSTA 2026 将于 2026 年 10 月 3–9 日举行，正式论文集计划刊载于 PACMSE 的 ISSTA 2026 专刊。当前清单以官方录用页为准；部分论文已有 arXiv 预印本，但本次未下载任何 PDF。

## 一、整体概览

共整理 26 篇核心论文：

- 测试用例/测试套件生成：10 篇。
- 断言、测试预言与生成质量评价：7 篇。
- 测试选择、优先级、演化与修复：8 篇。
- Flaky-test 数据集：1 篇。

最明显的研究趋势是：LLM 已从“直接把代码翻译成测试”转向与静态分析、变异测试、需求语义、检索、执行反馈和强化学习组合；评价指标也从单纯覆盖率扩展到真实缺陷检测、变异分数、可编译性、可执行性、测试语义和工业采纳率。

## 二、优先阅读建议

如果你的重点是“自动生成高质量测试用例”，建议先读：

1. **Generating Project-Specific Test Cases with Requirement Validation Intention**：需求意图 + 项目内测试复用，最贴近真实项目测试生成。
2. **Sakura**：面向自然语言复杂测试场景，多智能体生成跨类、跨方法测试。
3. **Context Matters**：总结工业落地中上下文、测试骨架和静态分析对生成可靠性的作用。
4. **Test vs Mutant**：测试智能体与变异体智能体对抗演化，强调真实缺陷检测能力。
5. **ConUT**：针对复杂 Java 分支，以静态分析提取的条件配置引导测试生成。

如果你的重点是“测试维护和回归测试”，建议先读：

1. **MuMuTestUp**：多智能体 + 变异分析的测试用例更新。
2. **Names Are All You Need**：Python 回归测试选择。
3. **Characterizing and Repairing Obsolete Android GUI Tests**：GUI 测试失效基准与自动修复。
4. **On the Evaluation of LLMs in Unit Test Evolution**：系统评测 LLM 的单元测试演化能力。

如果你的重点是“断言/预言与测试质量”，建议先读：

1. **RESTOR**：REST API 测试预言生成和工业部署。
2. **Towards More Realistic Assertion Generation**：多断言、插入位置未知的现实设置。
3. **Do Coverage and Mutation Scores ... Correlate With Their Effectiveness?**：重新审视覆盖率、变异分数和真实缺陷检测之间的关系。
4. **Evaluating and Mitigating the Misguidance Effect ...**：解释 buggy code 如何误导 LLM 生成验证错误行为的测试。

## 三、论文清单与简要概述

### A. 测试用例与测试套件生成（10 篇）

1. **AutoHIL: LLM-Based ECU Functional Test Generation through Domain Knowledge Augmentation**  
   作者：Sichen Gong, Qicai Chen, Bihuan Chen, Wenzhuo Zhang, Yukun Gao, Xin Peng。  
   概述：面向汽车 ECU 的 HIL 功能测试，将自然语言需求、AUTOSAR 源码/配置中的隐性领域知识和测试台 API 知识结合，生成可执行测试脚本。  
   入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/64/AutoHIL-LLM-Based-ECU-Functional-Test-Generation-through-Domain-Knowledge-Augmentati)

2. **Context Matters: Improving the Practical Reliability of LLM-Based Unit Test Generation (Experience Paper)**  
   作者：Junjie Chen, Ziqi Wang, Lin Yang, Chen Yang, Xiao Chu, Jianyi Zhou, Guangtai Liang, Qianxiang Wang, Dong Wang。  
   概述：CATGen 通过结构化项目上下文检索、确定性测试骨架和静态分析后处理，提高工业单元测试生成的可编译性、覆盖率和效率。  
   入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/151/Context-Matters-Improving-the-Practical-Reliability-of-LLM-Based-Unit-Test-Generatio)；[arXiv](https://arxiv.org/abs/2607.19682)

3. **ConUT: Condition-Aware Test Generation for Complex Java Code**  
   作者：RuiGuo Yu, Ruiqi Dong, Xi Xiao, Xiaogang Zhu, Shaohua Wang, Sheng Wen, Qingli。  
   概述：针对复杂 Java 控制流，通过反向依赖追踪提取影响分支条件的字段和调用，并生成配置模板指导 LLM 构造精确对象状态。  
   入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/169/ConUT-Condition-Aware-Test-Generation-for-Complex-Java-Code)

4. **From Greedy Steps to Global Optimization: Learning Sequential Test Suite Generation**  
   作者：Guoqing Wang, Chengran Yang, Xiaoxuan Zhou, Zeyu Sun, Bo Wang, David Lo, Dan Hao。  
   概述：把测试套件生成建模为序列决策问题，以强化学习训练 LLM 在每一步最大化新增测试的边际覆盖和缺陷检测收益。  
   入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/71/From-Greedy-Steps-to-Global-Optimization-Learning-Sequential-Test-Suite-Generation)

5. **From Natural Language to Executable Properties for Property-based Testing of Mobile Apps (Experience Paper)**  
   作者：Yiheng Xiong, Ting Su, Jingling Sun, Jue Wang, Qin Li, Geguang Pu, Zhendong Su。  
   概述：iPBT 将移动应用的自然语言属性描述转换为可执行的 property-based tests，结合多模态 UI 语义对齐和框架特定代码生成。  
   入口：[arXiv](https://arxiv.org/abs/2603.21263)；[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

6. **Generating Project-Specific Test Cases with Requirement Validation Intention**  
   作者：Binhang Qi, Yun Lin, Xinyi Weng, Yuhuan Huang, Chenyan Liu, Hailong Sun, Zhi Jin, Jin Song Dong。  
   概述：IntentionTest 根据测试目标、前置条件和预期结果检索项目内可复用测试，再由 LLM 按验证意图编辑，补充项目特定 API、mock 和断言。  
   入口：[arXiv](https://arxiv.org/abs/2507.20619)；[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

7. **LLMutantKiller: Using Large Language Models to Generate Tests that Kill Mutants**  
   作者：Farideh Khalili, Aidan Domondon, Harshit Garg, Frank Tip。  
   概述：使用 LLM 针对仍存活的变异体定向生成测试，把 mutation killing 作为提高测试有效性的目标。  
   入口：[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

8. **Sakura: An Approach for Generating Complex Tests from Natural Language Test Descriptions**  
   作者：Tyler Stennett, Rangeet Pan, Bridget McGinn, Alessandro Orso, Saurabh Sinha。  
   概述：从自然语言测试描述生成跨多个类和方法、包含复杂调用链与断言链的 Java 测试；使用定位、组合和监督三个智能体，并由静态分析与执行反馈约束。  
   入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/21/Sakura-An-Approach-for-Generating-Complex-Tests-from-Natural-Language-Test-Descripti)；[arXiv](https://arxiv.org/abs/2606.00530)

9. **Test vs Mutant: Adversarial LLM Agents for Robust Unit Test Generation**  
   作者：Pengyu Chang, Yixiong Fang, Silin Chen, Yuling Shi, Beijun Shen, Xiaodong Gu。  
   概述：AdverTest 让测试生成智能体和变异体生成智能体形成对抗循环，以覆盖率和变异分数为反馈，不断暴露并补足测试套件盲区。  
   入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/31/Test-vs-Mutant-Adversarial-LLM-Agents-for-Robust-Unit-Test-Generation)；[arXiv](https://arxiv.org/abs/2602.08146)

10. **Uncovering Business Logic Bugs via Semantics-Driven Unit Test Generation (Experience Paper)**  
    作者：Chen Yang, Junjie Chen。  
    概述：SeGa 从产品需求文档构建业务语义知识库，再针对代码方法生成带前置条件、触发动作、预期结果和语义约束的单元测试，以发现业务逻辑缺陷。  
    入口：[arXiv](https://arxiv.org/abs/2604.23509)；[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

### B. 断言、测试预言与生成质量评价（7 篇）

11. **Do Coverage and Mutation Scores of LLM-Generated Test Suites Correlate With Their Effectiveness? (Replicability Study)**  
    作者：Junda Zhao, Shurui Zhou, Eldan Cohen。  
    概述：检验覆盖率、变异分数和真实缺陷检测是否相关；结论强调相关性依赖测试场景，不能把代理指标无条件等同于测试有效性。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/2/Do-Coverage-and-Mutation-Scores-of-LLM-Generated-Test-Suites-Correlate-With-Their-Eff)；[arXiv](https://arxiv.org/abs/2607.22880)；DOI：10.1145/3832093

12. **Evaluating and Mitigating the Misguidance Effect of Buggy Code in LLM-Generated Unit Tests**  
    作者：Junda Zhao, Shurui Zhou, Eldan Cohen。  
    概述：研究 buggy code 如何诱导 LLM 生成验证错误行为的测试，并提出先生成规格说明、再基于规格生成测试的缓解方案。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/113/Evaluating-and-Mitigating-the-Misguidance-Effect-of-Buggy-Code-in-LLM-Generated-Unit-)；[arXiv](https://arxiv.org/abs/2607.22883)

13. **How Does Killing Surviving Mutants Help Detect Real Bugs with Assertion Generation? A Controlled Experiment**  
    作者：Hang Du, Vijay Krishna Palepu, James Jones。  
    概述：通过受控实验研究“针对存活变异体生成断言”是否真正提高真实缺陷检测，而不只提升变异分数。  
    入口：[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

14. **Industrial Practice of LLM-based Test Case Carving and Assertion Generation (Experience Paper)**  
    作者：Haozhen You, Zhen Dong, Jingjing Wang, Qiang Li, Xin Peng。  
    概述：NL2Test 从自然语言业务场景和真实流量中裁剪最小可重放 API 请求序列、恢复动态数据依赖并生成稳定断言；包含长期生产部署数据。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/8/Industrial-Practice-of-LLM-based-Test-Case-Carving-and-Assertion-Generation-Experien)；[arXiv](https://arxiv.org/abs/2607.24000)

15. **RESTOR: Automated Test Oracle Generation for RESTful APIs via Reinforcement Learning**  
    作者：Xun Zhou, Zhen Dong, Mingyu Ren, Qiang Li, JunJie Li, Sifan Wang, Xiaolong Yu, Chaofeng Sha, Xin Peng。  
    概述：从单个 REST API 请求—响应样本中识别稳定、语义有效的字段并生成可执行断言，使用强化学习优化预言质量，并报告生产部署。  
    入口：[arXiv](https://arxiv.org/abs/2607.23963)；[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

16. **STARS: Static Analysis-guided Assertion Synthesis Using Large Language Models**  
    作者：Jialun Cao, Haoyu Wang, Haoran Yan, Ming Wen, Michael Pradel。  
    概述：利用静态分析信息缩小断言候选范围并引导 LLM 合成测试断言，属于程序分析与生成模型结合的路线。  
    入口：[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

17. **Towards More Realistic Assertion Generation under Mixed-Assertion Scenario**  
    作者：Hongyan Li, Kunpeng E, Weifeng Sun, Quanjun Zhang, Meng Yan。  
    概述：指出现有研究常假设单断言和已知插入位置；提出面向单/多断言混合、插入位置未知场景的两阶段 DA-AG，先预测断言位置，再生成断言内容。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/123/Towards-More-Realistic-Assertion-Generation-under-Mixed-Assertion-Scenario)

### C. 测试选择、优先级、演化与修复（8 篇）

18. **CARE: Cascading Impact-Aware Compliance Test Suite Evolution under Regulatory Changes**  
    作者：Zhiyi Xue, Xiaohong Chen, Min Zhang。  
    概述：建立“法规—需求—场景—测试用例”四层级联关系，识别法规变化影响的测试并尽量复用不受影响的测试。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/204/CARE-Cascading-Impact-Aware-Compliance-Test-Suite-Evolution-under-Regulatory-Changes)

19. **Characterizing and Repairing Obsolete Android GUI Tests under UI Evolution**  
    作者：Shiwen Song, Yiheng Xiong, Wenbo Guo, Manqi Sun, Jiaolong Kong, Xiaofei Xie。  
    概述：构建 736 个过时 GUI 测试的基准，分析控件语义歧义和目标不可达问题，并以 GUIRevive 自动修复失效测试。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/209/Characterizing-and-Repairing-Obsolete-Android-GUI-Tests-under-UI-Evolution)

20. **Enhancing LLM-based Bug Reproduction via Code Entity Retrieval and Test Case Repair**  
    作者：Hao Ding, Yanjie Jiang, Yuxia Zhang, Hui Liu。  
    概述：LTER 从缺陷报告识别代码实体、检索精细上下文，生成缺陷复现测试，并根据编译诊断动态补充依赖与修复候选测试。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/156/Enhancing-LLM-based-Bug-Reproduction-via-Code-Entity-Retrieval-and-Test-Case-Repair)

21. **MuMuTestUp: Mutation-based Multi-Agent Test Case Update**  
    作者：Dawei Tian, Jiakun Liu, Yun Peng, Yichen Zhang, Jianlei Chi, Jun Sun, Xiaohong Su。  
    概述：通过变异分析、细粒度覆盖分析和语义检索等智能体协作，修复代码演化后不可编译、断言薄弱或覆盖不足的测试用例。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/126/MuMuTestUp-Mutation-based-Multi-Agent-Test-Case-Update)；[arXiv](https://arxiv.org/abs/2605.19265)

22. **Names Are All You Need: Effective and Safe Regression Test Selection for Python**  
    作者：You Wang, Michael Pradel, Zhongxin Liu。  
    概述：NameRTS 将 Python 程序表示为代码元素与名称的二部依赖图，通过可达性分析选择受代码变更影响的测试，兼顾跳过比例和安全性。  
    入口：[arXiv](https://arxiv.org/abs/2605.25356)；[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

23. **On the Evaluation of Large Language Models in Unit Test Evolution (Experience Paper)**  
    作者：Weichang Liu, Junwei Zhang, Yuqing Niu, Bo Zhou。  
    概述：在 530 个真实方法—测试协同演化实例上评估 12 个 LLM，比较提示设计、上下文学习方法和不同演化类型的影响。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/191/On-the-Evaluation-of-Large-Language-Models-in-Unit-Test-Evolution-Experience-Paper-)

24. **Test Case Prioritization for DNNs via Neural Collapse Instability**  
    作者：Chunyu Liu, Mingyuan Li, Yang Li, Wenmin Li, Fei Gao, Tengfei Tu, Su-Juan Qin。  
    概述：NCIP 利用 DNN 训练后期多个检查点的预测不稳定性，对测试输入排序，以在有限预算下更早暴露错误样本。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/92/Test-Case-Prioritization-for-DNNs-via-Neural-Collapse-Instability)；[arXiv](https://arxiv.org/abs/2607.20046)

25. **Test Case Selection for Deep Neural Networks: A Replication Study on LLMs for Code (Replicability Study)**  
    作者：Ali Asgari, Mitchell Olsthoorn, Annibale Panichella。  
    概述：在代码克隆、漏洞检测和技术债分类任务上评估 13 种测试用例选择策略，区分精度估计和早期故障发现两类目标。  
    入口：[官方录用页](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/145/Test-Case-Selection-for-Deep-Neural-Networks-A-Replication-Study-on-LLMs-for-Code-R)；[arXiv](https://arxiv.org/abs/2606.27601)

### D. 测试数据集（1 篇）

26. **A Dataset of Reproducible Flaky-Test Failures**  
    作者：Suzzana Rafi, Mahbub-Ul-Hoque Sumon, Md Erfan, Maruf Morshed Khan, August Shi, Wing Lam。  
    概述：ReproFlake 收录 1115 个可复现 flaky tests，提供可构建环境、失败复现脚本、修复应用脚本以及成功/失败执行日志。  
    入口：[arXiv](https://arxiv.org/abs/2605.21677)；[官方论文列表](https://conf.researchr.org/track/issta-2026/issta-2026-research-papers)

## 四、主题归纳

1. **LLM + 程序分析成为主流组合**：ConUT、STARS、Sakura、Context Matters 都借助静态分析或确定性程序处理约束生成过程。
2. **从覆盖率转向真实缺陷检测**：Test vs Mutant、LLMutantKiller、How Does Killing Surviving Mutants、Do Coverage and Mutation Scores 等论文都在追问“覆盖高是否真的更能发现 bug”。
3. **需求语义重新成为测试生成的核心输入**：IntentionTest、SeGa、AutoHIL、iPBT 和 CARE 都显式使用需求、业务规则或属性描述。
4. **测试维护受到明显关注**：MuMuTestUp、GUIRevive、NameRTS、CARE 和 Unit Test Evolution 覆盖更新、修复、选择和演化的完整链条。
5. **工业证据增多**：Context Matters、NL2Test、RESTOR、AutoHIL、SeGa 等包含企业数据、生产部署或工业系统评估。

## 五、边界说明

本清单没有纳入大量以 fuzzing、编译器测试、智能体红队测试、数据库测试为中心的 ISSTA 2026 论文，即使它们也会生成测试输入；原因是它们的研究对象主要是特定系统测试技术，而不是一般意义上的测试用例/测试套件构造、管理和评价。如需，我可以另行整理“ISSTA 2026 测试生成与 fuzzing 全景清单”。
