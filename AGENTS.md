# Travel Plan V2：Agent 快速上下文

> 本文件面向后续维护本仓库的编码 Agent。先阅读“不可违反的硬约束”，再开始修改。
> 本文件最后核对日期：2026-08-17。依赖的精确版本以 `uv.lock` 和
> `frontend/package-lock.json` 为最终依据。

## 1. 项目概览与目的

这是一个使用 LangGraph `StateGraph` 搭建的多 Agent 旅行规划 Harness，
同时包含 FastAPI 后端和 Vue 3 单页 Web 前端。目标不是展示静态 Demo，而是
使用真实 LLM 与高德地图数据完成以下闭环：

1. 用户输入目的地、日期、预算、住宿和旅行偏好；
2. 景点、天气、酒店三个研究 Agent 并行执行；
3. Planner Agent 汇总研究结果、查询相邻地点路线并生成结构化行程；
4. 前端在同一页面展示可审计的执行摘要、地图、预算和完整规划；
5. 用户继续追问时继承上一版计划，只重跑受变化影响的研究节点；
6. 每轮结果保存为同一 Session 中不可变的新版本，可浏览、追问或分叉历史版本。

核心拓扑：

```text
用户需求 → 变更分析 ─┬→ AttractionSearchAgent ─┐
                     ├→ WeatherQueryAgent ─────┼→ PlannerAgent → 计划版本
                     └→ HotelAgent ────────────┘
```

主要目录：

```text
agent_app/              Python 后端与 LangGraph Harness
  agents/               四个专家 Agent 和通用 ReAct 循环
  harness/              图状态、变更分析、节点编排、修订校验
  tools/                工具调度、路线比较和确定性交通规则
  infrastructure/       LLM、高德 MCP、高德 Web Service 客户端
  api/                  FastAPI、SSE、SQLite、Session/版本接口
  observability/        LangSmith Trace 接入
frontend/               Vue 3 单页旅行规划前端
tests/                  Python 单元与端到端回归测试
docs/                   产品、前端和接口设计文档
var/                    本地 SQLite 数据；不提交 Git
```

## 2. 技术栈与当前版本

### 后端

- Python：要求 `>=3.10`；当前本地为 `3.10.20`，Docker 使用 `3.12-slim`
- LangGraph `1.2.11`
- FastAPI `0.141.1` + Uvicorn `0.52.3`
- OpenAI Python SDK `2.54.0`，连接 OpenAI 兼容 Chat Completions 服务
- LangSmith `0.10.18`，默认关闭、按环境变量启用
- uv `0.12.4`；高德 MCP Server 由 uv tool 管理
- SQLite：Python 标准库，保存任务、事件、Session 和计划版本
- 高德地图：stdio MCP 用于天气、POI 和路线；Web Service 用于道路折线

### 前端

- Node.js：要求 `>=22.13.0`；当前本地为 `26.3.0`
- Vue `3.5.40`、Vue Router `5.2.0`
- TypeScript `5.9.3`、Vite `8.2.0`
- Pinia `4.0.2`
- Ant Design Vue `4.2.6`
- Axios `1.19.0`
- 高德 JS API Loader `1.0.1`
- Vitest `4.1.10`、Vue Test Utils `2.4.11`

## 3. 首次运行

### 3.1 前置要求

- Python 3.10+
- Node.js 22.13+
- 可用的 OpenAI 兼容 LLM Key
- 可用的高德 Web 服务 Key
- 如需真实浏览器地图，另需高德“Web 端（JS API）”Key和安全密钥

### 3.2 初始化后端

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uv tool install amap-mcp-server
```

仅当根目录 `.env` 不存在时复制示例，切勿覆盖已有配置：

```bash
cp .env.example .env
```

至少填写：

```dotenv
LLM_API_KEY="..."
LLM_MODEL_ID="..."
LLM_BASE_URL="https://..."
AMAP_API_KEY="..."
```

启动 API：

```bash
source .venv/bin/activate
python -m agent_app.api
```

默认地址与健康检查：

```text
API:    http://127.0.0.1:8000
Health: http://127.0.0.1:8000/api/health
```

### 3.3 初始化前端

打开第二个终端：

```bash
cd frontend
npm ci
cp .env.example .env   # 仅在 frontend/.env 不存在时执行
npm run dev -- --host 127.0.0.1 --port 5173
```

前端地址为 `http://127.0.0.1:5173/`。开发环境推荐将
`VITE_API_BASE_URL` 设置为 `/api`，使用 Vite 代理；若直接连接后端，必须是
`http://127.0.0.1:8000/api`，注意末尾的 `/api` 前缀。

真实运行必须保持：

```dotenv
VITE_USE_MOCK=false
```

### 3.4 可选：CLI 与 Docker

```bash
python -m agent_app \
  "2026年8月1日至3日去北京，预算5000元，喜欢历史文化，请制定行程"

docker compose up --build -d
```

Docker Compose 只启动后端，前端仍需单独运行或部署。

## 4. 不可违反的硬约束

### 4.1 产品与交互

1. **默认不得使用 Mock 数据。** 本地和正式流程均使用真实 LLM/高德结果；
   `frontend/src/mocks/` 只能用于测试或显式 Demo 模式。
2. **规划流程必须保持单页。** 首轮规划、多轮追问、执行摘要、历史回答和最终
   结果都在首页连续展示，不为这些步骤新增强制页面跳转。
3. **不得展示模型的原始思维链。** 前端只能展示可审计的执行摘要：计划、动作、
   工具结果摘要、节点状态和结论。
4. **追问必须继承上下文。** 后续请求必须携带同一 `session_id` 和最新
   `previous_plan_id`，后端加载上一版计划与研究结果后再修订。
5. **结构化条件变更也是有效追问。** 用户只修改目的地、日期、偏好、预算或住宿，
   即使未填写追加文本，也必须能够直接继续规划。目的地变化必须重跑三个研究节点。
6. **历史版本不可覆盖。** 每轮 Agent 结果和人工编辑都创建递增的新版本；历史版本
   只读。基于过期版本的并发修改必须返回冲突，不能静默覆盖最新计划。
7. **同一行程不得重复安排同一景点。** 新规划、修订和手动保存都必须经过唯一性校验。
8. **规划数据必须按用户隔离。** 旅行任务、计划和历史接口要求登录；Session 只能由
   所属用户读取和修改，密码不得明文保存，登录 Cookie 必须保持 HttpOnly。

### 4.2 Harness 与数据真实性

1. LangGraph 拓扑保持确定性：变更分析后并行运行或复用景点、天气、住宿节点，
   三个分支汇合后才运行 Planner；不要增加一个主 LLM 临时决定图拓扑。
2. Harness 负责状态和编排，不直接调用 LLM。Agent 不得自行创建 LLM 或 MCP
   连接；依赖必须由 `agent_app/main.py` 统一组装并共享。
3. 追问只重跑受影响节点，Planner 合成节点始终运行；未变化研究结果直接复用。
4. Agent 单次任务最多 10 轮。不得移除循环上限、工具调用 ID 对应关系或资源关闭逻辑。
5. 外部接口未提供的价格、评分、坐标、距离、时长等数据使用 `null`、不可用状态或
   明确说明，严禁模型猜测和伪造。
6. Planner 必须输出可解析的单个 JSON 对象，并满足 API 的结构化计划契约。

### 4.3 路线与地图

1. 推荐交通方式由 `agent_app/tools/route_policy.py` 的确定性规则计算，不能交给
   LLM 自由决定：
   - `≤1km`：推荐步行；
   - `1–10km`：地铁或公交接驳步行合计 `≤600m` 且换乘少于 2 次时推荐对应
     公共交通，否则推荐打车；
   - `>10km`：直达且接驳近的地铁优先，直达公交可选，否则推荐打车。
2. 地铁和公交是不同的展示类型，但后端契约中的推荐模式仍统一使用
   `public_transit`，通过 `transit_type` 区分。
3. 后端高德 Web 服务 Key 与前端高德 JS API Key 必须分离；后端 Key 不得发送到
   浏览器。前端构建变量只允许使用专门的 Web 端 Key。
4. **规划结果必须展示景点路线地图。** 每个包含景点的日程都要在地图中展示酒店、
   景点位置、访问顺序和相邻地点之间的路线折线，不能只提供文字行程。
5. 每个相邻地点路段都要保留距离、时间和推荐方式。景点路线地图上的**每一条折线**
   都必须像路线信息牌一样明确展示“距离 · 方式 · 时间”（至少包含距离和时间）；
   不得只展示部分折线的标签，也不得因碰撞避让、缩放级别或标签优先级而隐藏。
   标签重叠时应调整位置或错位展示，不能通过省略标签解决。
6. 道路折线获取失败不能使整份计划失败；必须降级为地点顺序连线，并继续展示
   已获得的文字路线和距离信息。

### 4.4 API、持久化与前端状态

1. 浏览器 API 路径统一位于 `/api`；更改 `VITE_API_BASE_URL` 时不得漏掉此前缀。
2. 创建任务保持异步 `202 + SSE + 最终计划查询` 模式，不要把长时间规划改成一个
   阻塞 HTTP 请求。
3. SQLite 是服务端历史记录的事实来源；浏览器缓存只能用于恢复体验，不能替代
   Session、任务、事件和计划版本存储。
4. SSE 事件只发送脱敏后的执行信息。修改事件类型时必须同步更新 Pydantic schema、
   前端事件映射和相关测试。
5. 行程编辑保存后必须重新应用交通规则，并重新计算受影响的相邻路线。

### 4.5 安全与可观测性

1. 不得读取、打印、提交或写入文档中的真实 `.env` 密钥；只维护无秘密的
   `.env.example`。日志和异常不得包含 LLM、高德或 LangSmith Key。
2. LangSmith 默认关闭。只有 `LANGSMITH_TRACING=true` 且用户明确允许数据发送时
   才启用；开启后旅行要求、模型输入输出及工具摘要会离开本地环境。
3. 高德 MCP 必须使用项目的安全启动器，避免上游 `print` 污染 stdio JSON-RPC。
4. 网络真实测试会消耗额度，除非用户明确要求，否则只运行默认离线测试。

## 5. 修改后的最低验证要求

后端改动：

```bash
.venv/bin/python -m unittest discover -v
```

前端改动：

```bash
cd frontend
npm run test -- --run
npm run type-check
npm run build
```

跨前后端、多轮追问、Session 或数据库改动，至少同时运行两组测试。默认测试
不访问真实外部服务。只有显式设置 `RUN_LIVE_API_TESTS=1` 的测试才允许访问
LLM/高德；运行前确认密钥、额度和测试目标。

修改完成后还应：

- 执行 `git diff --check`；
- 确认 `GET /api/health` 返回成功；
- 确认前端仍运行在 `http://127.0.0.1:5173/`；
- 若契约、启动方式或硬约束变化，同步更新本文件及对应详细文档。

## 6. 常用环境变量

| 变量 | 用途 | 备注 |
|---|---|---|
| `LLM_API_KEY` | OpenAI 兼容 LLM Key | 后端必需，秘密 |
| `LLM_MODEL_ID` | 模型 ID | 后端必需 |
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | 后端必需 |
| `AMAP_API_KEY` | 高德 Web 服务/MCP Key | 真实地图研究必需，秘密 |
| `AMAP_MCP_COMMAND` | MCP 启动命令 | 默认 `uvx amap-mcp-server` |
| `TRAVEL_API_DB_PATH` | SQLite 路径 | 默认 `var/travel_plan.db` |
| `AUTH_SESSION_DAYS` | 登录有效天数 | 默认 `7` |
| `AUTH_COOKIE_SECURE` | 登录 Cookie 仅限 HTTPS | 本地 `false`，生产应为 `true` |
| `API_CORS_ORIGINS` | 允许的前端 Origin | 逗号分隔 |
| `LANGSMITH_TRACING` | Trace 开关 | 默认 `false` |
| `LANGSMITH_API_KEY` | LangSmith Key | 开启 Trace 时必需，秘密 |
| `LANGSMITH_PROJECT` | Trace 项目名 | 默认 `travel-planning-harness` |
| `VITE_API_BASE_URL` | 浏览器 API 根路径 | 推荐 `/api` |
| `VITE_USE_MOCK` | 前端 Mock 开关 | 真实流程必须 `false` |
| `VITE_AMAP_JS_KEY` | 高德浏览器 JS API Key | 与后端 Key 分离 |
| `VITE_AMAP_SECURITY_JS_CODE` | 高德浏览器安全密钥 | 与 JS API Key 配套 |

完整变量及默认值见 [`.env.example`](./.env.example) 和
[`frontend/.env.example`](./frontend/.env.example)。

## 7. 关键代码入口

- 依赖组装与 CLI：[`agent_app/main.py`](./agent_app/main.py)
- LangGraph Harness：[`agent_app/harness/travel_planning.py`](./agent_app/harness/travel_planning.py)
- 共享状态：[`agent_app/harness/state.py`](./agent_app/harness/state.py)
- 修订结果校验：[`agent_app/harness/revision_validation.py`](./agent_app/harness/revision_validation.py)
- FastAPI 路由：[`agent_app/api/app.py`](./agent_app/api/app.py)
- 用户认证：[`agent_app/api/auth.py`](./agent_app/api/auth.py)
- SQLite/Session/版本：[`agent_app/api/repository.py`](./agent_app/api/repository.py)
- 自然语言追问归一化：[`agent_app/api/services/follow_up.py`](./agent_app/api/services/follow_up.py)
- 交通推荐规则：[`agent_app/tools/route_policy.py`](./agent_app/tools/route_policy.py)
- 高德 MCP 客户端：[`agent_app/infrastructure/amap_client.py`](./agent_app/infrastructure/amap_client.py)
- 高德道路折线：[`agent_app/infrastructure/amap_web_client.py`](./agent_app/infrastructure/amap_web_client.py)
- LangSmith：[`agent_app/observability/langsmith.py`](./agent_app/observability/langsmith.py)
- 单页规划入口：[`frontend/src/pages/HomePage.vue`](./frontend/src/pages/HomePage.vue)
- 规划状态与 SSE：[`frontend/src/stores/planning.ts`](./frontend/src/stores/planning.ts)
- 历史规划：[`frontend/src/stores/history.ts`](./frontend/src/stores/history.ts)
- 地图组件：[`frontend/src/components/TripMap.vue`](./frontend/src/components/TripMap.vue)
- 前后端类型契约：[`agent_app/api/schemas.py`](./agent_app/api/schemas.py)、
  [`frontend/src/types/travel.ts`](./frontend/src/types/travel.ts)

## 8. 更详细的文档

- [项目 README：架构、配置、API、LangSmith、测试与安全](./README.md)
- [前端 README：本地运行、后端接入和高德地图](./frontend/README.md)
- [前端产品与技术设计](./docs/frontend-design.md)
- [Python 依赖范围](./pyproject.toml)
- [Python 精确锁定版本](./uv.lock)
- [前端依赖与脚本](./frontend/package.json)
- [Docker 部署入口](./docker-compose.yml)

## 9. 常见问题速查

- **API 启动时报缺少配置**：检查根目录 `.env` 的三个 `LLM_*` 必需项；真实
  地图工具还需 `AMAP_API_KEY`。
- **前端请求 404**：检查 `VITE_API_BASE_URL` 是否包含 `/api`。
- **高德 MCP 启动失败**：重新执行
  `python -m uv tool install amap-mcp-server`，并检查 `AMAP_MCP_COMMAND`。
- **浏览器地图降级为示意图**：检查 `VITE_AMAP_JS_KEY` 和
  `VITE_AMAP_SECURITY_JS_CODE`；不要误用后端 Web 服务 Key。
- **追问返回 409**：当前页面基于旧计划版本；加载 Session 最新版本后再追问。
- **LangSmith 没有 Trace**：检查开关、Key、Project、Endpoint；修改 `.env` 后必须
  重启后端，新请求才会产生 Trace。
