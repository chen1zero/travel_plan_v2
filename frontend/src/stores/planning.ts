import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  createTravelPlan,
  getPlanningTask,
  type PlanningTask,
} from "../services/api";
import {
  subscribePlanningEvents,
  type PlanningEvent,
} from "../services/planning-events";
import type {
  PlanningLog,
  PlanningNodeStatus,
  PlanningStage,
  PlanningTraceKind,
  TravelRequest,
} from "../types/travel";

export const PLANNING_STAGES: Array<{
  key: PlanningStage;
  title: string;
  description: string;
}> = [
  {
    key: "attraction",
    title: "搜索心仪景点",
    description: "根据偏好筛选城市里的代表性目的地",
  },
  {
    key: "weather",
    title: "查询旅行天气",
    description: "查看旅行日期的温度、降雨与出行建议",
  },
  {
    key: "hotel",
    title: "推荐合适酒店",
    description: "结合预算、住宿类型与行程动线筛选",
  },
  {
    key: "planner",
    title: "编排行程路线",
    description: "逐段比较步行、驾车与公共交通时间",
  },
];

const REQUEST_STORAGE_KEY = "travel-planning-request";
const TASK_STORAGE_KEY = "travel-planning-task-id";
const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";
const PLANNING_TIMEOUT_MS = Number(
  import.meta.env.VITE_PLANNING_TIMEOUT_MS || 630_000,
);

function loadRequest(): TravelRequest | null {
  const value = sessionStorage.getItem(REQUEST_STORAGE_KEY);
  if (!value) return null;
  try {
    return JSON.parse(value) as TravelRequest;
  } catch {
    return null;
  }
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

export const usePlanningStore = defineStore("planning", () => {
  const request = ref<TravelRequest | null>(loadRequest());
  const taskId = ref(sessionStorage.getItem(TASK_STORAGE_KEY) || "");
  const currentStageIndex = ref(-1);
  const completedStageIndex = ref(-1);
  const nodeStatuses = ref<Record<PlanningStage, PlanningNodeStatus>>({
    attraction: "pending",
    weather: "pending",
    hotel: "pending",
    planner: "pending",
  });
  const logs = ref<PlanningLog[]>([]);
  const status = ref<
    "idle" | "planning" | "completed" | "failed"
  >("idle");
  const errorMessage = ref("");
  const errorCode = ref("");
  const retryable = ref<boolean | null>(null);
  const errorId = ref("");
  const connectionStatus = ref<
    "idle" | "connecting" | "connected" | "reconnecting"
  >("idle");
  let closeEventSource: (() => void) | null = null;

  const progress = computed(() => {
    if (status.value === "completed") return 100;
    const completedNodes = Object.values(nodeStatuses.value).filter(
      (nodeStatus) => nodeStatus === "completed",
    ).length;
    return Math.max(8, completedNodes * 25);
  });

  function setRequest(nextRequest: TravelRequest): void {
    request.value = nextRequest;
    sessionStorage.setItem(
      REQUEST_STORAGE_KEY,
      JSON.stringify(nextRequest),
    );
  }

  function clearRequest(): void {
    request.value = null;
    sessionStorage.removeItem(REQUEST_STORAGE_KEY);
  }

  function setTaskId(nextTaskId: string): void {
    taskId.value = nextTaskId;
    if (nextTaskId) {
      sessionStorage.setItem(TASK_STORAGE_KEY, nextTaskId);
    } else {
      sessionStorage.removeItem(TASK_STORAGE_KEY);
    }
  }

  function reset(): void {
    closeEventSource?.();
    closeEventSource = null;
    setTaskId("");
    currentStageIndex.value = -1;
    completedStageIndex.value = -1;
    nodeStatuses.value = {
      attraction: "pending",
      weather: "pending",
      hotel: "pending",
      planner: "pending",
    };
    logs.value = [];
    status.value = "idle";
    errorMessage.value = "";
    errorCode.value = "";
    retryable.value = null;
    errorId.value = "";
    connectionStatus.value = "idle";
  }

  function addLog(
    stage: PlanningStage | "harness",
    message: string,
    detail?: string,
    kind: PlanningTraceKind = "system",
  ): void {
    logs.value.push({
      id: crypto.randomUUID(),
      timestamp: new Date().toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }),
      stage,
      message,
      detail,
      kind,
    });
  }

  function traceKindForEvent(
    eventType: PlanningEvent["type"],
  ): PlanningTraceKind {
    if (eventType === "tool.started") return "action";
    if (
      eventType === "tool.completed" ||
      eventType === "tool.failed" ||
      eventType === "tool.retrying"
    ) {
      return "observation";
    }
    if (
      eventType === "node.completed" ||
      eventType === "node.skipped" ||
      eventType === "node.degraded" ||
      eventType === "stage.completed" ||
      eventType === "harness.completed" ||
      eventType === "plan.completed"
    ) {
      return "conclusion";
    }
    if (
      eventType === "node.started" ||
      eventType === "stage.started" ||
      eventType === "agent.iteration" ||
      eventType === "agent.recovering" ||
      eventType === "change.analysis" ||
      eventType === "plan.validation" ||
      eventType === "revision.validation"
    ) {
      return "thought";
    }
    return "system";
  }

  async function runMock(): Promise<string> {
    status.value = "planning";
    setTaskId(`task_demo_${Date.now()}`);
    addLog(
      "harness",
      "已接收旅行要求，开始拆解目标与约束",
      "Harness started",
      "system",
    );
    const mockTraces: Record<
      Exclude<PlanningStage, "planner">,
      [string, string, string]
    > = {
      attraction: [
        "从历史文化与人文街区偏好中提炼景点搜索方向",
        "检索北京历史文化类地点，并按城市范围过滤",
        "获得 12 个候选地点，保留故宫、景山等核心景点",
      ],
      weather: [
        "先核对旅行日期，再判断天气对户外安排的影响",
        "查询北京旅行期间的逐日天气预报",
        "已获得温度与降雨趋势，需要为午后阵雨留出弹性",
      ],
      hotel: [
        "结合经济型预算与景点分布，优先寻找交通便利区域",
        "检索王府井及周边经济型住宿",
        "找到多家候选住宿，王府井区域更利于串联每日行程",
      ],
    };
    for (const stage of PLANNING_STAGES.slice(0, 3)) {
      nodeStatuses.value[stage.key] = "running";
      addLog(
        stage.key,
        mockTraces[stage.key as Exclude<PlanningStage, "planner">][0],
        "思考摘要",
        "thought",
      );
    }
    await wait(520);
    for (const [index, stage] of PLANNING_STAGES.slice(0, 3).entries()) {
      const trace = mockTraces[
        stage.key as Exclude<PlanningStage, "planner">
      ];
      addLog(stage.key, trace[1], "地图工具调用", "action");
      await wait(220);
      addLog(stage.key, trace[2], "工具返回摘要", "observation");
      await wait(180);
      nodeStatuses.value[stage.key] = "completed";
      completedStageIndex.value = index;
      addLog(
        stage.key,
        `${stage.title}已形成可供规划使用的结论`,
        "节点完成",
        "conclusion",
      );
    }
    currentStageIndex.value = 3;
    nodeStatuses.value.planner = "running";
    addLog(
      "planner",
      "三路研究已汇合，先按日期、距离和开放时间编排顺序",
      "思考摘要",
      "thought",
    );
    await wait(420);
    addLog(
      "planner",
      "比较 8 段步行、驾车与公共交通路线",
      "路线工具调用",
      "action",
    );
    await wait(420);
    addLog(
      "planner",
      "路线耗时与距离已返回，短距离优先步行，其余优先公共交通",
      "工具返回摘要",
      "observation",
    );
    await wait(320);
    nodeStatuses.value.planner = "completed";
    completedStageIndex.value = 3;
    addLog(
      "planner",
      "每日安排、路线与预算已核对，形成最终旅行规划",
      "节点完成",
      "conclusion",
    );
    addLog(
      "harness",
      "全部步骤完成，规划结果已生成",
      "Harness completed",
      "conclusion",
    );
    status.value = "completed";
    return "plan_demo_beijing";
  }

  function handleRealEvent(
    event: PlanningEvent,
    onCompleted: (planId: string) => void,
    onFailed: (error: Error) => void,
  ): void {
    const index = event.stage
      ? PLANNING_STAGES.findIndex(
          (stage) => stage.key === event.stage,
        )
      : -1;
    if (index >= 0) currentStageIndex.value = index;
    if (
      (event.type === "node.started" || event.type === "stage.started") &&
      event.stage
    ) {
      nodeStatuses.value[event.stage] = "running";
    }
    if (
      (event.type === "node.completed" ||
        event.type === "node.skipped" ||
        event.type === "stage.completed") &&
      event.stage
    ) {
      nodeStatuses.value[event.stage] = "completed";
      completedStageIndex.value = index;
    }
    if (event.type === "node.failed" && event.stage) {
      nodeStatuses.value[event.stage] = "failed";
    }
    if (event.type === "node.degraded" && event.stage) {
      nodeStatuses.value[event.stage] = "completed";
      completedStageIndex.value = index;
    }
    if (
      event.stage ||
      event.type === "harness.started" ||
      event.type === "harness.completed" ||
      event.type === "change.analysis"
    ) {
      addLog(
        event.stage ?? "harness",
        event.message,
        event.type,
        traceKindForEvent(event.type),
      );
    }
    if (event.type === "plan.completed" && event.plan_id) {
      status.value = "completed";
      onCompleted(event.plan_id);
    }
    if (event.type === "task.failed") {
      status.value = "failed";
      errorMessage.value = event.message;
      errorCode.value = event.error_code || "";
      retryable.value = event.retryable ?? null;
      errorId.value = event.error_id || "";
      onFailed(new Error(event.message));
    }
  }

  function waitForRealTask(task: PlanningTask): Promise<string> {
    status.value = "planning";
    setTaskId(task.task_id);
    const stageIndex = task.current_stage
      ? PLANNING_STAGES.findIndex(
          (stage) => stage.key === task.current_stage,
        )
      : -1;
    if (stageIndex >= 0) currentStageIndex.value = stageIndex;

    if (task.status === "completed" && task.plan_id) {
      status.value = "completed";
      return Promise.resolve(task.plan_id);
    }
    if (task.status === "failed") {
      const message =
        task.error_message || "旅行规划未完成，请重新尝试";
      status.value = "failed";
      errorMessage.value = message;
      errorCode.value = task.error_code || "";
      retryable.value = task.retryable ?? null;
      errorId.value = task.error_id || "";
      return Promise.reject(new Error(message));
    }

    return new Promise<string>((resolve, reject) => {
      let settled = false;
      const settle = (
        callback: () => void,
      ): void => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeoutId);
        closeEventSource?.();
        closeEventSource = null;
        callback();
      };
      const complete = (planId: string): void =>
        settle(() => resolve(planId));
      const fail = (error: Error): void =>
        settle(() => reject(error));
      const timeoutId = window.setTimeout(() => {
        status.value = "failed";
        errorMessage.value = "规划超时，请使用原需求重新尝试";
        errorCode.value = "CLIENT_TIMEOUT";
        retryable.value = true;
        fail(new Error(errorMessage.value));
      }, PLANNING_TIMEOUT_MS);

      connectionStatus.value = "connecting";
      closeEventSource = subscribePlanningEvents(task.events_url, {
        onOpen: () => {
          connectionStatus.value = "connected";
        },
        onEvent: (event) =>
          handleRealEvent(event, complete, fail),
        onError: async () => {
          if (settled) return;
          connectionStatus.value = "reconnecting";
          try {
            const latest = await getPlanningTask(task.task_id);
            if (latest.status === "completed" && latest.plan_id) {
              status.value = "completed";
              complete(latest.plan_id);
            } else if (latest.status === "failed") {
              const message =
                latest.error_message ||
                "旅行规划未完成，请重新尝试";
              status.value = "failed";
              errorMessage.value = message;
              errorCode.value = latest.error_code || "";
              retryable.value = latest.retryable ?? null;
              errorId.value = latest.error_id || "";
              fail(new Error(message));
            }
          } catch {
            // EventSource will retry automatically; the overall timeout is
            // responsible for turning a prolonged outage into a failure.
          }
        },
      });
    });
  }

  async function runReal(
    existingTaskId?: string,
  ): Promise<string> {
    let task: PlanningTask;
    if (existingTaskId) {
      task = await getPlanningTask(existingTaskId);
    } else {
      if (!request.value) throw new Error("缺少旅行需求");
      task = await createTravelPlan(request.value);
    }
    return waitForRealTask(task);
  }

  async function run(existingTaskId?: string): Promise<string> {
    reset();
    try {
      if (USE_MOCK) {
        return runMock();
      }
      return await runReal(existingTaskId);
    } catch (error) {
      status.value = "failed";
      errorMessage.value =
        error instanceof Error
          ? error.message
          : "旅行规划未完成，请重新尝试";
      throw error;
    }
  }

  return {
    request,
    taskId,
    currentStageIndex,
    completedStageIndex,
    nodeStatuses,
    logs,
    status,
    errorMessage,
    errorCode,
    retryable,
    errorId,
    connectionStatus,
    progress,
    setRequest,
    clearRequest,
    reset,
    run,
  };
});
