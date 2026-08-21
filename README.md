# LangGraph 多 Agent 旅行规划 Harness

这是一个基于 LangGraph `StateGraph` 的多 Agent 旅行规划应用。Harness
先并行运行三个互不依赖的研究节点，再等待全部状态就绪后进入行程合成：

1. `AttractionSearchAgent` 搜索符合偏好的景点；
2. `WeatherQueryAgent` 提取目的地并查询未来天气；
3. `HotelAgent` 搜索符合住宿需求的酒店；
4. `PlannerAgent` 汇总前三个节点，查询相邻地点的多交通方式路线并输出
   最终行程。

多轮追问使用持久化 Session 串联。每一轮显式记录 `previous_plan_id`，
LangGraph 先运行变更分析节点，再决定景点、天气、住宿哪些研究需要重跑；
未变化的节点直接复用上一版研究结果，Planner 以修订模式接收历史计划并
输出同一 Session 的下一版本。

仓库同时包含 Vue 3 Web 前端，位于 `frontend/`。它提供旅行需求表单、
实时状态图、Harness 事件流、地图化结果和可编辑行程。前端使用说明见
`frontend/README.md`，详细产品方案见 `docs/frontend-design.md`。

`TravelPlanningHarness` 负责编译和运行 LangGraph，本身不调用 LLM，也不
根据模型临时选择节点。四个专家分别继承 `SimpleAgent`，通过官方
`openai` Python SDK 调用 OpenAI 兼容接口，并在最多 10 轮内完成各自的
工具调用。

## 项目结构

```text
agent_app/
├── agents/                       # 专家 Agent 层
│   ├── base.py                   # SimpleAgent 与 ReAct 循环
│   ├── attraction.py             # 景点搜索专家
│   ├── weather.py                # 天气查询专家
│   ├── hotel.py                  # 酒店推荐专家
│   └── planner.py                # 最终行程规划专家
├── harness/                      # LangGraph Harness 与共享状态
│   ├── state.py                  # TravelPlanState
│   └── travel_planning.py        # 并行研究、汇合与行程合成图
├── workflows/                    # 旧导入路径兼容层
│   └── travel_plan.py            # TravelPlanningHarness 别名
├── tools/                        # Agent 可调用工具层
│   ├── dispatcher.py             # JSON 工具调用调度
│   ├── schema.py                 # 函数/MCP schema 转换
│   └── route.py                  # 多交通方式路线聚合
├── infrastructure/               # 外部服务基础设施层
│   ├── llm_client.py             # OpenAI 兼容 LLM 客户端
│   ├── mcp_client.py             # 持久化 stdio MCP 客户端
│   ├── amap_client.py            # 高德 MCP 动态适配器
│   ├── amap_web_client.py        # 高德道路级路线折线客户端
│   └── amap_server_runner.py     # MCP stdout 安全启动器
├── shared/                       # 跨层公共能力
│   ├── config.py                 # 环境配置与校验
│   └── logging.py                # 日志配置与安全预览
├── api/                          # FastAPI 浏览器接口层
│   ├── app.py                    # HTTP、CORS、SSE 与接口路由
│   ├── repository.py             # SQLite 任务、事件与计划存储
│   ├── schemas.py                # Pydantic 请求/响应契约
│   └── services/                 # 后台任务和路线重新计算
├── main.py                       # 依赖组装与 CLI
└── __main__.py                   # python -m agent_app 入口
tests/
├── test_amap_mcp_server_runner.py # MCP stdout 保护启动器测试
├── test_amap_mcp_list_tools.py # 打印真实 MCP 工具定义
├── test_attraction_agent.py # 景点搜索专家及真实 MCP 测试
├── test_hotel_agent.py # 酒店推荐专家及真实 MCP 测试
├── test_llm_client.py # finish_reason 透传单元测试
├── test_mcp_client.py # MCP 握手、工具调用和高德适配测试
├── test_planner_agent.py # 行程规划专家及真实路线测试
├── test_project_structure.py # 分层目录与模块导入回归测试
├── test_route_tool.py # 多交通方式路线对比单元测试
├── test_simple_agent.py # 通用 Agent 基类单元测试
├── test_tool_dispatcher.py # 工具调度器单元测试
├── test_travel_plan_agent.py # LangGraph 拓扑、状态传递与 Harness 测试
└── test_weather_agent.py # 天气查询专家单元测试
frontend/                         # Vue 3 + TypeScript Web 前端
├── src/pages/                    # 首页、规划进度、结果页
├── src/components/               # 地图、天气、预算与行程组件
├── src/stores/                   # Pinia 任务与行程状态
└── src/services/                 # Axios、SSE 与接口适配
```

依赖方向保持单向：`main` 负责组装，`harness` 编排 `agents`，
`agents` 使用 `tools`，`tools` 通过 `infrastructure` 访问外部服务；
`shared` 只提供无业务状态的配置和日志能力。各专家不直接创建 LLM 或
MCP 连接，便于独立测试和替换实现。

## 安装

需要 Python 3.10 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

依赖中包含 `uv`，安装后会提供 `uvx`。首次使用高德工具时，`uvx` 会在
隔离环境中下载并启动
[`sugarforever/amap-mcp-server`](https://github.com/sugarforever/amap-mcp-server)；
主项目使用 Python 3.10 或更高版本，MCP Server 所需的 Python 版本由 `uvx`
单独管理。

## 配置

程序默认读取项目根目录下的 `.env`。`.env` 已加入 `.gitignore`，
不要把真实 Key 写入 Python 代码或提交到 Git。

也可以不依赖 `.env`，在当前终端使用 `export` 设置环境变量：

```bash
export LLM_API_KEY="your_api_key"
export LLM_MODEL_ID="deepseek-v4-flash"
export LLM_BASE_URL="https://api.deepseek.com"
export AMAP_API_KEY="your_amap_web_service_key"
export AMAP_MCP_COMMAND="uvx amap-mcp-server"
export AMAP_MCP_TIMEOUT_SECONDS="30"
```

已通过 `export` 设置的变量优先于 `.env` 中的同名变量。

首次使用高德 MCP Server 前执行一次安装：

```bash
python3 -m uv tool install amap-mcp-server
```

程序会用 uv 的离线模式启动已安装或已缓存的 MCP Server，工具调用期间
不会重复访问 PyPI。这样可以避免 PyPI 暂时不可用导致天气、景点或酒店
查询失败。

可选配置：

- `LLM_TIMEOUT_SECONDS`：单次请求超时秒数，默认 `60`
- `LLM_MAX_RETRIES`：SDK 请求重试次数，默认 `2`
- `AMAP_API_KEY`：高德 Web 服务 Key；调用高德 MCP 天气工具时必须配置
- `AMAP_MCP_COMMAND`：stdio MCP Server 启动命令，默认
  `uvx amap-mcp-server`；程序会自动转换为离线启动
- `AMAP_MCP_TIMEOUT_SECONDS`：MCP 初始化及工具调用超时，默认 `30`
- `API_CORS_ORIGINS`：允许访问 API 的前端 Origin，使用逗号分隔
- `TRAVEL_API_DB_PATH`：任务和计划数据库，默认 `var/travel_plan.db`
- `TRAVEL_API_MAX_WORKERS`：并发旅行规划任务数，默认 `2`
- `TRAVEL_API_TASK_TIMEOUT_SECONDS`：单个规划任务上限，默认 `600`
- `AUTH_COOKIE_NAME`：登录 Cookie 名称，默认 `travel_session`
- `AUTH_SESSION_DAYS`：登录有效天数，默认 `7`
- `AUTH_COOKIE_SECURE`：生产 HTTPS 环境设为 `true`，本地 HTTP 保持 `false`

## 运行

直接传入任务：

```bash
python -m agent_app "请给我制定一个三天的上海旅行计划"
```

默认使用 `INFO` 级别输出图节点、模型请求和各专家每轮运行日志。需要
查看发送给模型的每条消息时，可以开启 `DEBUG`：

```bash
python -m agent_app --log-level DEBUG \
  "2026年8月1日至3日去北京，预算5000元，喜欢历史文化，请制定行程"
```

日志会显示每一轮的轮次、LLM 的 `finish_reason`、调用的工具、工具参数和
工具返回结果。模型请求耗时及最终答案也会记录，但不会记录 API Key。
使用 `WARNING` 或 `ERROR` 可以减少正常运行日志。

运行示例：

```bash
python -m agent_app \
  "2026年8月1日至3日去北京，预算5000元，喜欢历史文化，请制定行程"
```

LLM 客户端原样返回 Chat Completions 的 `finish_reason`。Agent 收到
`tool_calls` 后执行工具，将 assistant 工具调用和带相同 `tool_call_id`
的结果追加到 `messages`，然后进入下一轮；收到 `stop` 后输出最终答案
并退出。

`TravelPlanningHarness.run()` 会把完整需求同时交给景点、天气和酒店研究
节点。LangGraph 等待三个分支全部完成，再将合并后的状态交给
`PlannerAgent`。节点与边是确定性的，不存在额外的主 Agent LLM：

```text
                                      ┌→ AttractionSearchAgent ─┐
用户原始需求 → START → 变更分析 ─────┼→ WeatherQueryAgent ─────┼→ PlannerAgent → END
                                      └→ HotelAgent ────────────┘
```

追问模式的拓扑为：

```text
追问 + previous_plan_id
  → 变更分析
  → 景点 / 天气 / 住宿节点（按分析结果重跑或复用）
  → PlannerAgent（接收 previous_plan 和历史研究结果）
  → Session 新版本
```

`WeatherQueryAgent` 从完整需求中只提取唯一目的地城市，然后调用底层
高德 MCP `maps_weather({"city": "城市名"})`，从结果中返回从明天开始的
未来 3 天预报。回复为固定 JSON，包含查询参数、预报范围、每日天气、
出行建议和数据说明。

`AttractionSearchAgent` 会把“历史文化”“自然风光”等偏好转换为适合高德
POI 搜索的关键词；需求中包含城市时，还会设置 `city` 和
`citylimit="true"`，然后调用 `maps_text_search` 并根据真实 POI 结果
返回景点列表。回复为固定 JSON，保留本次搜索参数以及最多 5 个景点的
名称、地址、类型、POI ID 和推荐理由。

`HotelAgent` 会把“经济型”“豪华型”等住宿需求转换为经济型酒店、快捷
酒店、豪华酒店或五星级酒店等 POI 关键词；需求中包含城市时，还会限制
城市范围，然后调用 `maps_text_search` 返回酒店名称和地址。
回复为固定 JSON；实时价格、评分、星级或联系方式没有出现在工具结果中
时统一使用 `null`，不会进行推测。

`PlannerAgent` 接收用户原始需求以及天气、景点、酒店三个专家的完整输出。
它选择住宿和每日景点顺序，并对每一段相邻地点调用
`compare_route_options`。该工具内部调用高德 MCP 地理编码及步行、驾车、
公共交通坐标版路线工具，将结果统一为公里和分钟。最终计划逐段展示三种
交通方式的距离和时间；接口未返回的路线会明确标注为不可用。公交总里程
只有在能从具体换乘路段完整汇总时才返回，接驳步行距离和换乘次数使用
独立字段，避免把起终点距离误写成公交里程。综合公共交通结果还会根据
高德线路类型区分地铁、公交、公交与地铁混合换乘及铁路，并保留线路名称；
前端据此显示“推荐地铁”“推荐公交”或“推荐公交+地铁”。
作为最终输出 Agent，`PlannerAgent` 只返回一个可由 `json.loads` 解析的
JSON 对象，固定包含需求摘要、天气、酒店、每日安排、逐段路线、预算、
预订与安全提示以及数据说明；未知数值统一使用 `null`。

路线比较会先对每个地点做一次地理编码并缓存坐标，再调用三种坐标版路线
工具，避免同一地点被重复编码而触发高德 QPS 限制。确定推荐交通方式后，
服务端再用高德 Web 服务路线 API 获取道路级折线并写入计划；前端地图据此
绘制真实道路形状，同时显示分段距离、方式和耗时。折线获取失败时仍保留
地点顺序连线和完整文字路线，不阻断规划结果。
`TravelPlanningHarness` 会直接返回 `PlannerAgent` 的计划，不再经过其他 LLM
二次改写。

推荐交通方式由后端确定性规则统一计算，而不是交给 LLM 自由判断，并将
地铁与公交作为不同公共交通类型展示：1 公里内推荐步行；1 至 10 公里时，
地铁或公交接驳步行合计不超过 600 米且换乘少于 2 次则推荐对应公共交通，
换乘 2 次及以上或接驳步行超过 600 米则推荐打车；超过 10 公里时，直达且
接驳近的地铁优先，直达且接驳近的公交仍可选择，否则推荐打车。计划生成、
读取和编辑保存时都会重新应用规则。

MCP 客户端通过 stdio 完成 `initialize`、`notifications/initialized`、
`tools/list` 和 `tools/call` 协议交互。第一次列出或调用工具时建立连接，
之后所有专家复用同一个 MCP 子进程；工作流退出上下文时统一关闭连接。
如果连接异常中断，当前请求会报告错误，下一次调用可以重新建立连接。
启动命令和超时均可通过环境变量配置。默认命令为 `uvx`；如果它不在
`PATH`，客户端还会自动尝试当前 Python 的 `uv` 模块及项目 `.venv`
中的 `uvx`。

项目通过一个轻量启动器运行高德 MCP Server，拦截上游公共交通工具的
`print(data)`，丢弃响应内容并只在 stderr 记录固定提示，避免其污染
stdio JSON-RPC 响应或产生超长日志。实际天气、POI 和路线逻辑仍由
`sugarforever/amap-mcp-server` 执行。

`SimpleAgent` 封装了原生 `finish_reason` 处理、工具消息追加、逐轮日志和
10 轮安全上限。`add_tool()` 采用执行函数优先的参数形式。对于普通 Python
函数，只需要传入函数本身，工具名取自函数名，描述取自 docstring，参数
schema 从类型注解和默认值自动生成：

```python
def get_time(timezone: str, include_seconds: bool = False):
    """查询指定时区的当前时间。"""
    ...

agent.add_tool(get_time)
```

需要覆盖自动推导结果时，可以使用 `name=`、`description=` 或
`parameters=`；如果已经有 OpenAI 或 MCP 工具定义，则使用
`schema=` 保留原始的精确参数定义。

高德工具 schema 不在项目里重复维护，而是在启动时调用 `list_tools()`
从 MCP Server 动态读取并直接注册：

```python
from functools import partial

tools = amap_mcp.list_tools()
weather_tool = next(
    tool for tool in tools if tool["name"] == "maps_weather"
)
weather_agent.add_tool(
    partial(amap_mcp.call_tool, "maps_weather"),
    schema=weather_tool,
)
```

`AmapMCPClient.call_tool(tool_name, **arguments)` 不依赖预先编写的 Python
包装函数，可以按 MCP 返回的工具名和参数动态调用任意已发布工具。自行
构建客户端时应使用上下文管理器，或在结束后显式调用 `close()`。

高德 Key 仅作为 MCP 子进程的 `AMAP_MAPS_API_KEY` 环境变量使用，不会
发送给 LLM，也不会写入工具调用日志。

也可以从标准输入传入：

```bash
echo "上海自然风光两日游，预算3000元，入住经济型酒店" | \
  python -m agent_app
```

可以将安全上限调低，但不能超过 10：

```bash
python -m agent_app --max-iterations 10 "分析这个问题并给出结论"
```

每个专家最多请求 LLM 10 次。如果某个专家第 10 轮后仍需继续调用工具，
会强制退出并返回 `任务未完成，已达最大循环次数`。

### 运行 Web API

先配置根目录 `.env`，然后启动 FastAPI：

```bash
python -m agent_app.api
```

默认监听 `http://127.0.0.1:8000`，健康检查为：

```bash
curl http://127.0.0.1:8000/api/health
```

### LangSmith Trace 观测

项目已接入 LangSmith，但默认关闭，不影响纯本地运行。先在 LangSmith 创建
API Key，再在 `.env` 中配置：

```bash
LANGSMITH_TRACING="true"
LANGSMITH_API_KEY="lsv2_..."
LANGSMITH_PROJECT="travel-planning-harness"
LANGSMITH_ENDPOINT="https://api.smith.langchain.com"
# API Key 关联多个 Workspace 时填写
LANGSMITH_WORKSPACE_ID=""
```

重启 API 后，新规划会形成以下观测层级：

```text
Travel Planning Harness
  ├── change_analysis
  ├── attraction_research
  │   ├── LLM · {model_id}
  │   └── Tool · maps_text_search
  ├── weather_research
  │   ├── LLM · {model_id}
  │   └── Tool · maps_weather
  ├── hotel_research
  │   ├── LLM · {model_id}
  │   └── Tool · maps_text_search
  └── itinerary_synthesis
      ├── LLM · {model_id}
      └── Tool · compare_route_options
```

每个 Trace 附带 `task_id`、`session_id`、目标版本、目的地、日期和规划模式；
其中 `thread_id=session_id`，因此多轮追问会在 LangSmith Threads 中聚合展示。
`GET /api/observability` 可查看开关、项目和端点是否就绪，但不会返回 API Key。

开启后，用户旅行要求、模型输入输出以及地图工具参数和结果会发送到配置的
LangSmith 服务。若数据不能离开本地环境，请保持 `LANGSMITH_TRACING=false`
或改用自托管 LangSmith 端点。官方说明见
[Trace LangGraph applications](https://docs.langchain.com/langsmith/trace-with-langgraph)
和 [Configure threads](https://docs.langchain.com/langsmith/threads)。

浏览器调用链为：

```text
POST /api/auth/register 或 /api/auth/login
  → GET /api/auth/me（刷新页面时恢复登录状态）
POST /api/travel-plans
  → GET /api/observability（LangSmith 观测配置状态）
  → GET /api/harness（状态图元数据）
  → GET /api/travel-plans/{task_id}/events（SSE）
  → GET /api/plans/{plan_id}
  → POST /api/travel-plans（携带 session_id + previous_plan_id 追问）
  → GET /api/sessions（搜索、游标分页的历史规划列表）
  → GET /api/sessions/{session_id}（会话版本链）
  → GET /api/sessions/{session_id}/turns（完整问题、执行摘要和结果轮次）
  → POST /api/sessions/{session_id}/fork（从任意历史版本创建新规划）
  → PATCH /api/plans/{plan_id}/itinerary（保存为新的不可变版本）
```

旅行规划、计划和历史接口均要求登录。账号、PBKDF2 密码哈希和登录会话保存在
同一个 SQLite 数据库中；浏览器只持有 HttpOnly、SameSite=Lax 的不透明 Cookie。
每个规划 Session 绑定创建它的用户，其他账号查询时不会返回该任务或计划。

创建接口立即返回 `202`。三个研究节点并行执行，汇合后运行行程合成节点；
Harness 节点、LLM 决策和工具调用的脱敏摘要通过 SSE 推送。任务、事件和最终计划写入
SQLite，因此页面刷新后可从“历史规划”恢复整段会话，而不依赖浏览器缓存。
工具参数会按注册 Schema 做运行时校验；瞬态超时、断线和网络错误采用有上限的
重试。失败任务保存 `error_code`、`retryable` 和 `error_id`，SSE 还会发送
`tool.retrying`、`tool.failed`、`node.degraded` 等脱敏事件。天气服务失败时允许以
“数据不可用”继续规划，景点和酒店检索失败时则受控终止，避免使用模型猜测数据。
任务总超时会标记失败并协作式取消 Harness；前端保留失败请求，可一键创建新任务重试。
编辑行程时，后端校验 `base_revision`、使用高德 MCP 重新计算受影响路线，
并追加新的 Plan 版本，原版本始终保留。前端在同一页展示所有轮次与版本；
历史版本只读，可以返回最新版本继续追问，也可以复制为独立的新 Session。
规划运行期间同页显示高德目的地地图，最终结果可按 DAY 切换酒店与景点的
路线、分段距离和推荐交通方式。

用户继续补充要求时，前端通过 `TravelRequest.session_id` 和
`TravelRequest.previous_plan_id` 创建同一 Session 的新任务。后端从 SQLite
加载上一版完整计划及景点、天气、住宿研究上下文。变更分析节点根据城市、
日期、偏好、预算、住宿类型和本轮自然语言要求生成选择性执行决策：例如
“换个酒店”只重跑住宿研究，“每天十点后出发”直接复用三个研究节点。
Planner 的修订模式会保留未受影响内容，只为新增或变化的相邻路段查询路线。
每轮产生新的不可变 `plan_id` 和递增版本号，并由 Session 记录当前版本；
基于过期 `previous_plan_id` 的并发追问返回 `409`。

也可以使用 Docker 私有部署：

```bash
docker compose up --build -d
```

容器只包含 Python API、Agent 代码和高德 MCP 工具，SQLite 数据保存在
`travel-plan-data` 卷中。生产环境应在容器前配置 HTTPS 反向代理，并把
`API_CORS_ORIGINS` 设置为实际前端域名，同时设置
`AUTH_COOKIE_SECURE=true`。

## 测试

默认测试只运行离线单元测试，不会访问 LLM 或天气服务：

```bash
python -m unittest discover -v
```

LangSmith Eval 默认 v2 已包含 33 条版本化核心用例（v1 的 32 条仍保留），日常可离线运行：

```bash
.venv/bin/python -m evals.run
```

数据集、评分指标、发布门禁、LangSmith 同步和真实 LLM 实验步骤见
[`evals/README.md`](./evals/README.md)。远端同步与实验默认被
`RUN_LANGSMITH_EVALS=1` 保护，未明确允许发送数据时不会连接 LangSmith。

`tests/test_weather_agent.py`、`tests/test_attraction_agent.py`、
`tests/test_hotel_agent.py` 和 `tests/test_planner_agent.py` 分别包含
天气、景点、酒店与行程规划专家的端到端测试，验证独立 LLM、对应 MCP
工具和高德 API。
`tests/test_travel_plan_agent.py` 验证编译后的 LangGraph 拓扑、并行研究状态
汇合、节点事件和资源释放。`tests/test_api.py` 使用真实
`TravelPlanningHarness` 加测试替身跑通创建任务、图执行、SSE、结果查询、
幂等提交、上下文继承的增量追问、行程编辑和修订号冲突，是不依赖外部
密钥的端到端测试。
真实测试会产生网络请求并可能消耗 API 配额；首次执行还需下载 MCP
Server 隔离环境。

直接运行测试文件时会自动启用真实请求，并且可以从任意当前目录执行：

```bash
python3 /Users/cxl/Documents/travel_plan_v2/tests/test_attraction_agent.py
python3 /Users/cxl/Documents/travel_plan_v2/tests/test_amap_mcp_list_tools.py
python3 /Users/cxl/Documents/travel_plan_v2/tests/test_hotel_agent.py
python3 /Users/cxl/Documents/travel_plan_v2/tests/test_planner_agent.py
python3 /Users/cxl/Documents/travel_plan_v2/tests/test_travel_plan_agent.py
python3 /Users/cxl/Documents/travel_plan_v2/tests/test_weather_agent.py
```

使用 `unittest` 模块或测试发现功能运行时，需要显式开启：

```bash
RUN_LIVE_API_TESTS=1 \
python -m unittest \
  tests.test_attraction_agent \
  tests.test_hotel_agent \
  tests.test_planner_agent \
  tests.test_travel_plan_agent \
  tests.test_weather_agent -v
```

真实测试最多执行 10 轮，单次 LLM 请求最多等待 90 秒，MCP 首次下载及
调用最多等待 180 秒。运行时会打印和 CLI 相同的 `INFO` 决策日志，测试
代码不会输出 API Key。

其中 `test_amap_mcp_list_tools.py` 不调用 LLM，也不执行具体地图查询；
它只连接真实高德 MCP Server，调用 `list_tools()`，以 JSON 格式打印
每个工具的名称、描述和完整参数 schema，并将同一份结果保存到
`tests/fixtures/amap_mcp_tools.json`。重新运行测试会刷新该文件。

## 安全说明

- `.env` 只用于本地开发，仓库中保留的是不含真实密钥的
  `.env.example`。
- `Settings` 的字符串表示会隐藏 API Key，降低日志误泄漏风险。
- `INFO`/`DEBUG` 日志会包含任务和模型输出的截断预览；处理敏感任务时请
  使用 `--log-level WARNING`，并谨慎保存终端日志。
- 如果密钥曾被提交到版本库或公开发送，请立即在服务商控制台轮换密钥。
