# LangSmith Eval：第一阶段硬指标 + 第二阶段 LLM Judge

这套评估体系解决一个简单问题：修改模型、提示词、Agent 或交通规则以后，如何知道旅行规划能力有没有退化。

第一阶段先守住可以明确判定对错的底线；第二阶段在硬指标全部通过后，用 LLM Judge 评估难以用规则表达的体验质量。仓库中的 JSONL 是数据集事实来源；LangSmith 用于保存数据集、运行实验和比较不同版本。

## 1. 已交付内容

| 套件 | 数量 | 主要验证内容 | 默认是否联网 |
|---|---:|---|---|
| `change_routing` | 12 | 多轮追问应该重跑或复用哪些研究节点 | 否 |
| `route_policy` | 12 | 1 km、10 km、600 m、换乘次数和不可用降级边界 | 否 |
| `initial_plan` | 5 | 首轮计划契约、需求覆盖、证据来源、住宿约束、路线和未知值 | 否 |
| `revision` | 4 | 预算、夜景、酒店、目的地修订及未变字段保留 | 否 |
| 合计 | **33** | 第一阶段 P0 核心能力；v1 的 32 条仍可复现 | 否 |

目录说明：

```text
evals/
  cases/                  4 个版本化 JSONL 数据集
  fixtures/               离线冻结合成证据
  loader.py               数据加载和重复 ID 检查
  targets.py              默认离线目标
  live_target.py          真实 LLM + 冻结工具证据目标
  evaluators.py           确定性评分器
  judges.py               结构化 LLM Judge 与软质量量表
  gates.py                发布门禁
  run.py                  本地运行入口
  sync_datasets.py        同步到 LangSmith
  run_langsmith.py        创建 LangSmith 实验
  run_judge.py            给已有实验追加 Judge 评分
```

## 2. 新手先这样运行

在项目根目录执行：

```bash
.venv/bin/python -m evals.run
```

正常结果应为 `33/33` 通过，并且所有硬指标都是 `100%`。这个命令只使用本地代码和冻结夹具，不调用 LLM、高德或 LangSmith，适合日常开发和 CI。

也可以只运行某一组：

```bash
.venv/bin/python -m evals.run --suite components
.venv/bin/python -m evals.run --suite smoke
.venv/bin/python -m evals.run --suite revision
```

机器读取结果时使用：

```bash
.venv/bin/python -m evals.run --json
```

只要任一用例或门禁失败，进程退出码就是非零，CI 会自然失败。

## 3. 分数是什么意思

| 指标 | 判定标准 |
|---|---|
| `run_completed` | 目标成功返回结果 |
| `change_routing_exact` | 应重跑/复用的三个研究节点完全正确 |
| `route_policy_exact` | 每段推荐方式和确定性交通规则一致 |
| `plan_contract_valid` | 结果满足前端消费的完整 Pydantic 契约 |
| `request_coverage` | 城市、日期、天数和预算覆盖用户要求 |
| `attraction_unique` | 同一趟旅行不跨天重复景点 |
| `research_provenance` | 景点和酒店均来自冻结研究证据 |
| `accommodation_constraint_handled` | 有匹配住宿时选对类型；无匹配候选时明确标记未解决约束 |
| `route_coverage` | 每个日程地点都有对应相邻路线 |
| `unsupported_facts_null` | 没有证据的价格、费用和不可用路线数值保持 `null` |
| `tool_discipline` | live 实验中必需工具、参数形状和选择性研究节点正确 |
| `revision_intent_satisfied` | 修订确实满足预算、夜景、换酒店或换城市要求 |
| `unchanged_field_retention` | 不受影响的字段与上一版完全一致 |

第一阶段全部是硬指标，门禁均为 `100%`。这是刻意的：结构、安全和业务规则不应该靠平均分掩盖单条失败。

## 4. 三种运行层次不要混淆

1. **本地离线回归**：`evals.run`。验证数据集、评分器和确定性业务逻辑，是每次提交都应执行的基础门禁。
2. **LangSmith fixture 实验**：把离线结果上传到 LangSmith。用于确认数据同步、评分显示和实验对比链路可用，不代表模型质量。
3. **LangSmith live 实验**：调用真实 LLM，但地图、天气和路线由同一版冻结证据提供。这样不同模型/提示词之间的差异不会被实时外部数据变化干扰。

冻结证据是为评估构造的合成数据，不是实时高德快照。生产流程仍然使用真实高德结果。

## 5. 第一次同步到 LangSmith

只有你明确允许数据发送时才执行。旅行要求、模型输入输出和工具摘要会离开本地环境。

先在当前终端设置 LangSmith 配置，不要把真实 Key 写进文档或提交到 Git：

```bash
export RUN_LANGSMITH_EVALS=1
export LANGSMITH_API_KEY="你的 Key"
export LANGSMITH_PROJECT="travel-planning-evals"
```

同步 4 个数据集：

```bash
.venv/bin/python -m evals.sync_datasets
```

会创建或更新：

- `travel-change-routing-v1`
- `travel-route-policy-v1`
- `travel-initial-plan-v2`
- `travel-revision-v1`

每条样例使用稳定 UUID，重复同步是 upsert，不会因为多运行一次而复制数据。新增或修改用例时应创建 `v2` 文件和数据集名称，保留 `v1` 以便历史实验可复现。

## 6. 在 LangSmith 运行实验

先用 fixture 验证远端链路：

```bash
.venv/bin/python -m evals.run_langsmith --target fixture
```

再运行真实模型。为了避免一次消耗过多额度，建议先执行 9 条端到端用例：

```bash
export LLM_API_KEY="你的模型 Key"
export LLM_MODEL_ID="你的模型 ID"
export LLM_BASE_URL="你的 OpenAI 兼容地址"
export LANGSMITH_TRACING=true

.venv/bin/python -m evals.run_langsmith \
  --target live \
  --suite initial_plan \
  --suite revision \
  --max-concurrency 2
```

live 目标不会调用高德网络接口，只会调用真实 LLM。LLM 配置只从当前进程环境读取，不会主动加载项目 `.env`。

模型输出有随机性。准备做模型或提示词选型时，可以运行 3 次：

```bash
.venv/bin/python -m evals.run_langsmith \
  --target live \
  --suite initial_plan \
  --suite revision \
  --repetitions 3
```

比较实验时至少记录：Git 提交、模型 ID、提示词版本、数据集版本、重复次数和所有失败案例。不要只看总平均分。

## 7. 日常工作流

推荐顺序：

```text
改代码/提示词
  → 本地运行 33 条
  → 修复所有硬门禁
  → 有明确发送许可时运行 LangSmith live 9 条
  → 在 LangSmith 对比基线实验
  → 人工复核失败案例
  → 决定是否发布
```

回归测试入口：

```bash
.venv/bin/python -m unittest tests.test_evals -v
```

## 8. 第二阶段：LLM Judge 如何工作

第二阶段采用“硬门禁在前、软质量在后”的串联方式：

```text
Agent 输出
  → 第一阶段确定性评分
  → 任一硬指标失败：记录 judge_eligible=0，不调用 Judge
  → 硬指标全部通过：调用 LLM Judge
  → 把分项分数、理由和证据写回 LangSmith
```

这样可以避免 Judge 用一个较高的主观分数掩盖结构错误、来源错误、路线错误或虚构数据。

### 8.1 Judge 评分维度

| LangSmith 指标 | Judge 原始分 | 判断内容 |
|---|---:|---|
| `judge_request_alignment` | 1–5 | 目的地、日期、偏好、预算、住宿和追加要求是否真正得到满足 |
| `judge_itinerary_quality` | 1–5 | 景点组合、顺序、时间段、节奏和折返是否合理 |
| `judge_practical_feasibility` | 1–5 | 结合天气、路线、交通时间和已知开放信息，计划是否可执行 |
| `judge_presentation_quality` | 1–5 | 摘要、日程、提醒和限制说明是否清楚、可行动 |
| `judge_revision_quality` | 1–5 | 仅修订套件使用；是否精准满足新意图并保留未受影响内容 |
| `judge_degradation_quality` | 1–5 | 仅路线故障用例使用；是否透明降级、保留可执行信息且不伪造数值 |
| `judge_overall` | 0–1 | 按套件权重汇总后的总分 |

LangSmith 中的分项分数使用 `(原始分 - 1) / 4` 归一化：3 分对应 `0.50`，4 分对应 `0.75`，5 分对应 `1.00`。每个反馈的 comment 仍保留原始 `x/5`、简短理由和最多三条计划证据。

首轮规划的权重为：需求匹配 30%、行程安排 30%、现实可执行性 25%、表达质量 15%。修订规划的权重为：需求匹配 20%、行程安排 20%、现实可执行性 15%、表达质量 10%、修订质量 35%。

### 8.2 当前阈值是预警，不是发布门禁

第二阶段刚引入时采用 report-only：

- `judge_overall < 0.70` 时预警；
- 任一分项 `< 0.50` 时预警；
- 第一阶段所有硬指标仍要求 `100%`；
- Judge 不能让硬指标失败的结果通过发布。

先由人工复核一批高分、临界分和低分案例，确认 Judge 与人的判断稳定一致，再决定是否将软阈值升级为发布门禁。

## 9. 配置 Judge 模型

Judge 优先读取以下独立配置：

```dotenv
EVAL_JUDGE_API_KEY="..."
EVAL_JUDGE_MODEL_ID="..."
EVAL_JUDGE_BASE_URL="https://..."
EVAL_JUDGE_REPETITIONS="1"
```

如果某项留空，会逐项复用 `LLM_API_KEY`、`LLM_MODEL_ID` 和 `LLM_BASE_URL`。这让现有 `.env` 可以直接运行，但正式做模型选型时，建议 Judge 与被测 Agent 使用不同模型，降低“同一模型自评”的偏差。

代码不会自动读取 `.env`。需要复用项目已有配置时，显式通过 `python-dotenv` 注入；不要打印或提交真实 Key。

## 10. 新实验直接启用 Judge

只运行 9 条端到端用例，并同时写入硬指标和 Judge 指标：

```bash
export RUN_LANGSMITH_EVALS=1

.venv/bin/python -m dotenv run --override -- \
  .venv/bin/python -m evals.run_langsmith \
  --target live \
  --suite initial_plan \
  --suite revision \
  --judge \
  --judge-repetitions 3 \
  --max-concurrency 2
```

这会同时调用被测 Agent 和 Judge。`change_routing`、`route_policy` 是确定性组件套件，不进行主观评分。

## 11. 给已有实验追加 Judge，不重跑 Agent

第一阶段已经生成 live 实验时，优先复用现有输出，避免再次消耗 Agent 调用额度：

```bash
export RUN_LANGSMITH_EVALS=1

.venv/bin/python -m dotenv run --override -- \
  .venv/bin/python -m evals.run_judge \
  --experiment "travel-initial_plan-live-已有实验名称" \
  --suite initial_plan \
  --max-concurrency 2
```

修订实验把 `--suite` 改为 `revision`。该命令只读取已有实验的 inputs、outputs 和 reference outputs，运行 Judge 并把反馈追加回原实验，不会再次执行旅行规划 Agent。原实验中硬指标失败的行只记录 `judge_eligible=0`，不会产生主观质量分。

同一实验已有 `judge_*` 反馈时，命令默认停止，防止重复评分被 LangSmith 混合求平均。只有在明确需要升级 Judge 提示词或替换失准评分时，才使用 `--replace-existing`；它只删除该实验已有的 `judge_*` 反馈，不影响 Agent 输出和第一阶段指标。

## 12. 校准与日常使用建议

1. 先对现有 9 条端到端结果追加评分，人工复核 Judge 理由是否引用了真实计划内容。
2. 收集线上失败样例时先脱敏，再增加到新版本数据集，不覆盖 v1。
3. 模型或提示词对比时固定 Judge 模型和 `travel-plan-judge-v3` 提示词版本。
4. 对发布候选至少查看总分、分项最低分和硬指标失败行，不只看平均分。
5. Judge 空响应或输出结构异常时会自动要求模型修正一次；连续两次无效则让该 evaluator 明确失败，不能静默给默认分。设置 `--judge-repetitions 3` 后，首评分低于 `0.70` 的样例会再评两次，各维度取中位数，避免单次随机波动。

Judge 提示词明确把用户输入和候选计划视为不可信待评数据，忽略其中可能存在的提示注入，并只要求简短理由，不要求或保存模型思维链。它也明确说明冻结合成证据是评估世界的权威事实，避免 Judge 用外部地理常识误判示意线路、重复的合成距离或故意注入的服务故障。运行 Judge 会把旅行要求、候选计划和上一版计划（修订用例）发送给模型服务与 LangSmith，因此仍必须有明确的数据发送许可。
