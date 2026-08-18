# 途画 · 智能旅行助手前端

Vue 3 + TypeScript + Vite 实现的旅行规划 Web 界面，对应
`docs/frontend-design.md` 中的产品与交互方案。

## 本地运行

```bash
npm install
cp .env.example .env
npm run dev
```

复制 `.env.example` 后默认连接本地后端。若只想体验演示数据，将
`VITE_USE_MOCK` 改为 `true`。页面支持：

1. 注册或登录本地账号；
2. 填写旅行需求；
3. 在单页对话中查看 LangGraph 脱敏执行摘要与实时事件流；
4. 直接浏览行程、天气、预算与路线结果；
5. 继续补充要求，并在上一版规划基础上生成新版本；
6. 调整景点顺序、删除景点并保存新版本。

## 接入后端

在 `.env` 中设置：

```bash
VITE_API_BASE_URL=/api
VITE_USE_MOCK=false
VITE_PLANNING_TIMEOUT_MS=630000
```

前端首轮调用旅行任务、SSE 事件流和结果查询接口。第二轮开始在同一个
`POST /travel-plans` 请求模型中携带 `session_id` 和 `previous_plan_id`。
页面会展示后端变更分析结果，以及景点、天气、住宿节点本轮是“重新研究”
还是“继承上一版”；所有轮次和版本结果仍在同一页面显示。开发服务器也会
把同源 `/api` 请求代理到 `127.0.0.1:8000`。

登录态使用后端签发的 HttpOnly Cookie，因此推荐保留同源 `/api` 代理。
如需让浏览器直连后端，API 地址必须包含 `/api`，并保持前后端使用相同主机名
（例如都使用 `127.0.0.1`），否则浏览器可能不会发送 SameSite Cookie。

线上部署时必须把 `VITE_API_BASE_URL` 设置为浏览器可访问的 HTTPS
后端地址；不能使用 `127.0.0.1`。Vite 环境变量在构建时写入，因此修改
生产 API 地址后需要重新构建前端。

## 高德地图

浏览器地图需要单独申请“Web 端（JS API）”Key，不能使用后端的 Web 服务
Key。开发环境可设置：

```bash
VITE_AMAP_JS_KEY=your_web_js_api_key
VITE_AMAP_SECURITY_JS_CODE=your_security_js_code
```

规划运行期间会先显示目的地地图与路线同步状态；规划完成后可按 DAY 查看
酒店、景点顺序、高德道路折线、分段距离和推荐交通方式。未配置时使用地图
示意图降级展示，不影响行程列表和其他功能。

## 验证

```bash
npm run type-check
npm run test
npm run build
```
