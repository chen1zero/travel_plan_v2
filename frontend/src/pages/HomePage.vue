<script setup lang="ts">
import {
  CalendarOutlined,
  DownOutlined,
  EditOutlined,
  EnvironmentOutlined,
  PlusOutlined,
  SaveOutlined,
  SendOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UndoOutlined,
} from "@ant-design/icons-vue";
import { message } from "ant-design-vue";
import dayjs, { type Dayjs } from "dayjs";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import InlinePlanResult from "../components/InlinePlanResult.vue";
import ItineraryTimeline from "../components/ItineraryTimeline.vue";
import ThinkingProcess from "../components/ThinkingProcess.vue";
import TripMap from "../components/TripMap.vue";
import { getPlan, type PlanningSessionTurn } from "../services/api";
import { useHistoryStore } from "../stores/history";
import { usePlanStore } from "../stores/plan";
import { usePlanningStore } from "../stores/planning";
import type {
  AccommodationType,
  PlanningLog,
  PlanEnvelope,
  TravelRequest,
} from "../types/travel";

interface ConversationTurn {
  id: string;
  userText: string;
  request: TravelRequest;
  mode: "initial" | "refinement";
  status: "planning" | "completed" | "failed";
  logs: PlanningLog[];
  envelope: PlanEnvelope | null;
  error?: string;
  errorCode?: string;
  retryable?: boolean;
  errorId?: string;
}

const CONVERSATION_STORAGE_KEY = "travel-conversation-turns";
const MAX_PERSISTED_TURNS = 16;
const MAX_PERSISTED_LOGS_PER_TURN = 80;

const planningStore = usePlanningStore();
const planStore = usePlanStore();
const historyStore = useHistoryStore();
const restoredRequest = planningStore.request;

function restoreConversation(): ConversationTurn[] {
  if (!planStore.envelope || !restoredRequest) return [];
  try {
    const storedValue = localStorage.getItem(
      CONVERSATION_STORAGE_KEY,
    );
    const stored = storedValue
      ? (JSON.parse(storedValue) as ConversationTurn[])
      : [];
    const validTurns = Array.isArray(stored)
      ? stored.filter(isStoredTurn)
      : [];
    const latestStoredEnvelope = [...validTurns]
      .reverse()
      .find((turn) => turn.envelope)?.envelope;
    if (
      latestStoredEnvelope?.session_id ===
        planStore.envelope.session_id &&
      latestStoredEnvelope.plan_id === planStore.envelope.plan_id
    ) {
      return validTurns;
    }
  } catch {
    localStorage.removeItem(CONVERSATION_STORAGE_KEY);
  }

  const revision = planStore.envelope.revision;
  return [
    {
      id: crypto.randomUUID(),
      userText:
        restoredRequest.additional_requirements ||
        `继续查看上次的${restoredRequest.destination_city}旅行规划`,
      request: { ...restoredRequest },
      mode: revision > 1 ? "refinement" : "initial",
      status: "completed",
      logs: [
        {
          id: crypto.randomUUID(),
          timestamp: "",
          stage: "planner",
          message: `已从本地恢复第 ${revision} 版计划及其完整上下文`,
          kind: "thought",
        },
        {
          id: crypto.randomUUID(),
          timestamp: "",
          stage: "planner",
          message: "读取已保存的日程、天气、酒店与路线结果",
          kind: "action",
        },
        {
          id: crypto.randomUUID(),
          timestamp: "",
          stage: "planner",
          message: "上下文已恢复，可以继续补充要求",
          kind: "conclusion",
        },
      ],
      envelope: cloneEnvelope(planStore.envelope),
    },
  ];
}

function isStoredTurn(value: unknown): value is ConversationTurn {
  if (!value || typeof value !== "object") return false;
  const turn = value as Partial<ConversationTurn>;
  return (
    typeof turn.id === "string" &&
    typeof turn.userText === "string" &&
    !!turn.request &&
    (turn.status === "completed" || turn.status === "failed") &&
    Array.isArray(turn.logs)
  );
}

function persistConversation(value: ConversationTurn[]): void {
  const completedTurns = value
    .filter((turn) => turn.status !== "planning")
    .slice(-MAX_PERSISTED_TURNS)
    .map((turn) => ({
      ...turn,
      logs: turn.logs.slice(-MAX_PERSISTED_LOGS_PER_TURN),
    }));
  try {
    localStorage.setItem(
      CONVERSATION_STORAGE_KEY,
      JSON.stringify(completedTurns),
    );
  } catch {
    try {
      localStorage.setItem(
        CONVERSATION_STORAGE_KEY,
        JSON.stringify(completedTurns.slice(-6)),
      );
    } catch {
      localStorage.removeItem(CONVERSATION_STORAGE_KEY);
    }
  }
}

const cities = [
  { value: "北京", label: "北京 · 历史与现代交织" },
  { value: "上海", label: "上海 · 都市与海派文化" },
  { value: "杭州", label: "杭州 · 湖山与宋韵" },
  { value: "成都", label: "成都 · 烟火与慢生活" },
  { value: "西安", label: "西安 · 古都与盛唐遗风" },
  { value: "广州", label: "广州 · 岭南与美食" },
];

const preferenceOptions = [
  "历史文化",
  "自然风光",
  "美食探索",
  "人文街区",
  "艺术展览",
  "亲子体验",
  "休闲度假",
];

const examples = [
  "每天 9 点后出发，节奏松弛一些",
  "带父母出行，少走路并避开太晒的时段",
  "想多安排本地美食，酒店靠近地铁",
];

const form = reactive({
  destination: restoredRequest?.destination_city ?? "北京",
  budget: restoredRequest?.budget_cny ?? 5000,
  accommodation:
    restoredRequest?.accommodation_type ??
    ("经济型" as AccommodationType),
});
const today = dayjs().startOf("day");
const defaultStartDate = today.add(7, "day");
const dates = ref<[Dayjs, Dayjs]>([
  restoredRequest ? dayjs(restoredRequest.start_date) : defaultStartDate,
  restoredRequest
    ? dayjs(restoredRequest.end_date)
    : defaultStartDate.add(2, "day"),
]);
const selectedPreferences = ref<string[]>(
  restoredRequest
    ? [...restoredRequest.preferences]
    : ["历史文化", "人文街区"],
);
const customPreference = ref("");
const prompt = ref("");
const turns = ref<ConversationTurn[]>(restoreConversation());
const submitting = ref(false);
const activeTurnId = ref<string | null>(null);
const showConstraints = ref(true);
const feedEnd = ref<HTMLElement | null>(null);
const autoFollowLatest = ref(true);
const selectedPlanId = ref(planStore.envelope?.plan_id ?? "");
const forking = ref(false);

const hasTurns = computed(() => turns.value.length > 0);
const latestCompletedTurn = computed(() => {
  for (let index = turns.value.length - 1; index >= 0; index -= 1) {
    const turn = turns.value[index];
    if (turn?.envelope) return turn;
  }
  return null;
});
const activeSession = computed(() => historyStore.selectedSession);
const viewingHistoricalVersion = computed(
  () =>
    !!activeSession.value?.current_plan_id &&
    !!selectedPlanId.value &&
    selectedPlanId.value !== activeSession.value.current_plan_id,
);

watch(
  () => planningStore.logs.length,
  () => {
    if (autoFollowLatest.value) scrollToLatest(false);
  },
);

watch(
  turns,
  (value) => persistConversation(value),
  { deep: true },
);

watch(
  () => historyStore.selectionToken,
  () => void restoreServerSession(),
);

watch(
  () => historyStore.newPlanningToken,
  () => startNewPlanning(),
);

onMounted(() => {
  window.addEventListener("scroll", handleViewportScroll, {
    passive: true,
  });
  void syncLatestSessionPlan();
});

onBeforeUnmount(() => {
  window.removeEventListener("scroll", handleViewportScroll);
});

function togglePreference(preference: string): void {
  if (selectedPreferences.value.includes(preference)) {
    selectedPreferences.value = selectedPreferences.value.filter(
      (item) => item !== preference,
    );
  } else if (selectedPreferences.value.length < 5) {
    selectedPreferences.value.push(preference);
  } else {
    message.info("最多选择 5 个旅行偏好");
  }
}

function addCustomPreference(): void {
  const value = customPreference.value.trim();
  if (!value || selectedPreferences.value.includes(value)) return;
  if (selectedPreferences.value.length >= 5) {
    message.info("最多选择 5 个旅行偏好");
    return;
  }
  selectedPreferences.value.push(value);
  customPreference.value = "";
}

function scrollToLatest(smooth = true): void {
  autoFollowLatest.value = true;
  void nextTick(() => {
    feedEnd.value?.scrollIntoView?.({
      behavior: smooth ? "smooth" : "auto",
      block: "end",
    });
  });
}

function handleViewportScroll(): void {
  const documentHeight = document.documentElement.scrollHeight;
  autoFollowLatest.value =
    window.scrollY + window.innerHeight >= documentHeight - 180;
}

async function syncLatestSessionPlan(): Promise<void> {
  try {
    const changed = await planStore.syncLatestSession();
    if (!changed || !planStore.envelope) return;
    const latestTurn = latestCompletedTurn.value;
    if (!latestTurn) return;
    latestTurn.envelope = cloneEnvelope(planStore.envelope);
    latestTurn.logs = [
      ...latestTurn.logs,
      {
        id: crypto.randomUUID(),
        timestamp: "",
        stage: "planner",
        message: `已同步服务器最新第 ${planStore.envelope.revision} 版计划`,
        kind: "conclusion",
      },
    ];
    syncNormalizedConstraints(latestTurn, planStore.envelope);
  } catch {
    // Keep the locally restored plan available when the API is offline.
  }
}

function traceKindForHistoryEvent(type: string): PlanningLog["kind"] {
  if (type === "tool.started") return "action";
  if (
    type === "tool.completed" ||
    type === "tool.failed" ||
    type === "tool.retrying"
  ) {
    return "observation";
  }
  if (
    type === "node.completed" ||
    type === "node.skipped" ||
    type === "node.degraded" ||
    type === "stage.completed" ||
    type === "harness.completed" ||
    type === "plan.completed"
  ) {
    return "conclusion";
  }
  if (
    type === "node.started" ||
    type === "stage.started" ||
    type === "agent.iteration" ||
    type === "agent.recovering" ||
    type === "change.analysis" ||
    type === "plan.validation" ||
    type === "revision.validation"
  ) {
    return "thought";
  }
  return "system";
}

function historyLogs(turn: PlanningSessionTurn): PlanningLog[] {
  return turn.events
    .filter(
      (event) =>
        !!event.stage ||
        event.type === "harness.started" ||
        event.type === "harness.completed" ||
        event.type === "change.analysis" ||
        event.type === "plan.completed",
    )
    .map((event) => ({
      id: `history-${event.event_id}`,
      timestamp: dayjs(event.timestamp).format("HH:mm:ss"),
      stage: event.stage ?? "harness",
      message: event.message,
      detail: event.type,
      kind: traceKindForHistoryEvent(event.type),
    }));
}

async function restoreServerSession(): Promise<void> {
  const session = historyStore.selectedSession;
  if (!session) return;
  try {
    const planIds = historyStore.selectedTurns
      .map((turn) => turn.plan_id)
      .filter((value): value is string => !!value);
    const envelopes = await Promise.all(planIds.map((id) => getPlan(id)));
    const envelopeById = new Map(
      envelopes.map((envelope) => [envelope.plan_id, envelope]),
    );
    const restoredTurns: ConversationTurn[] = historyStore.selectedTurns.map(
      (turn) => {
        const envelope = turn.plan_id
          ? envelopeById.get(turn.plan_id)
          : undefined;
        return {
          id: `server-${turn.task_id}`,
          userText: turn.user_text,
          request: turn.request,
          mode: (turn.revision ?? 1) > 1 ? "refinement" : "initial",
          status:
            turn.status === "failed"
              ? "failed"
              : turn.status === "completed"
                ? "completed"
                : "planning",
          logs: historyLogs(turn),
          envelope: envelope ? cloneEnvelope(envelope) : null,
          error: turn.error_message,
          errorCode: turn.error_code,
          retryable: turn.retryable,
          errorId: turn.error_id,
        };
      },
    );
    turns.value = restoredTurns;
    selectedPlanId.value = session.current_plan_id ?? "";
    const currentEnvelope = session.current_plan_id
      ? envelopeById.get(session.current_plan_id)
      : undefined;
    const latestTurn = [...restoredTurns]
      .reverse()
      .find((turn) => turn.envelope);
    if (currentEnvelope) planStore.setEnvelope(currentEnvelope);
    if (latestTurn) {
      planningStore.setRequest(latestTurn.request);
      form.destination = latestTurn.request.destination_city;
      form.budget = latestTurn.request.budget_cny;
      form.accommodation = latestTurn.request.accommodation_type;
      dates.value = [
        dayjs(latestTurn.request.start_date),
        dayjs(latestTurn.request.end_date),
      ];
      selectedPreferences.value = [...latestTurn.request.preferences];
    }
    showConstraints.value = false;
    await nextTick();
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  } catch (error) {
    message.error(
      error instanceof Error ? error.message : "历史规划恢复失败",
    );
  }
}

function selectPlanVersion(planId: string): void {
  selectedPlanId.value = planId;
  void nextTick(() => {
    document
      .querySelector<HTMLElement>(`[data-plan-id="${planId}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function returnToLatestVersion(): void {
  const currentPlanId = activeSession.value?.current_plan_id;
  if (currentPlanId) selectPlanVersion(currentPlanId);
}

function startNewPlanning(): void {
  turns.value = [];
  activeTurnId.value = null;
  selectedPlanId.value = "";
  prompt.value = "";
  showConstraints.value = true;
  planningStore.reset();
  planningStore.clearRequest();
  planStore.clear();
  historyStore.clearSelection();
  localStorage.removeItem(CONVERSATION_STORAGE_KEY);
  window.scrollTo?.({ top: 0, behavior: "smooth" });
}

async function forkHistoricalVersion(): Promise<void> {
  if (!activeSession.value || !selectedPlanId.value || forking.value) return;
  forking.value = true;
  try {
    await historyStore.fork(
      activeSession.value.session_id,
      selectedPlanId.value,
    );
    message.success("已从该版本创建独立的新规划");
  } catch (error) {
    message.error(
      error instanceof Error ? error.message : "创建新规划失败",
    );
  } finally {
    forking.value = false;
  }
}

async function refreshHistoryAfterPlanning(): Promise<void> {
  await historyStore.loadSessions(true);
}

function describeConstraintChanges(
  previousRequest: TravelRequest | undefined,
): string[] {
  if (!previousRequest) return [];

  const changes: string[] = [];
  const destination = form.destination.trim();
  const startDate = dates.value[0].format("YYYY-MM-DD");
  const endDate = dates.value[1].format("YYYY-MM-DD");
  const preferences = [...selectedPreferences.value].sort();
  const previousPreferences = [...previousRequest.preferences].sort();

  if (destination !== previousRequest.destination_city) {
    changes.push(`将目的地调整为${destination}`);
  }
  if (
    startDate !== previousRequest.start_date ||
    endDate !== previousRequest.end_date
  ) {
    changes.push(
      `将旅行日期调整为${dates.value[0].format("M月D日")}至${dates.value[1].format("M月D日")}`,
    );
  }
  if (preferences.join("\u0000") !== previousPreferences.join("\u0000")) {
    changes.push(`将旅行偏好调整为${selectedPreferences.value.join("、")}`);
  }
  if (form.budget !== previousRequest.budget_cny) {
    changes.push(`将总预算调整为${form.budget}元`);
  }
  if (form.accommodation !== previousRequest.accommodation_type) {
    changes.push(`将住宿调整为${form.accommodation}`);
  }

  return changes;
}

function validate(): boolean {
  if (!form.destination.trim()) {
    message.warning("请填写目的地城市");
    return false;
  }
  if (!dates.value?.[0] || !dates.value?.[1]) {
    message.warning("请选择旅行日期");
    return false;
  }
  if (dates.value[0].isBefore(today, "day")) {
    message.warning("出发日期不能早于今天");
    return false;
  }
  if (dates.value[1].diff(dates.value[0], "day") > 30) {
    message.warning("单次旅行日期不能超过 31 天");
    return false;
  }
  if (!selectedPreferences.value.length) {
    message.warning("请至少选择一个旅行偏好");
    return false;
  }
  if (!form.budget || form.budget <= 0) {
    message.warning("请填写有效的旅行预算");
    return false;
  }
  if (
    hasTurns.value &&
    !prompt.value.trim() &&
    !describeConstraintChanges(latestCompletedTurn.value?.request).length
  ) {
    message.info("写下希望调整的要求后再发送");
    return false;
  }
  return true;
}

function buildRequest(
  userText: string,
  previousEnvelope: PlanEnvelope | null,
): TravelRequest {
  return {
    destination_city: form.destination.trim(),
    start_date: dates.value[0].format("YYYY-MM-DD"),
    end_date: dates.value[1].format("YYYY-MM-DD"),
    preferences: [...selectedPreferences.value],
    budget_cny: form.budget,
    accommodation_type: form.accommodation,
    additional_requirements: userText || undefined,
    session_id: previousEnvelope?.session_id || undefined,
    previous_plan_id: previousEnvelope?.plan_id || undefined,
  };
}

function cloneEnvelope(value: PlanEnvelope): PlanEnvelope {
  return JSON.parse(JSON.stringify(value)) as PlanEnvelope;
}

function disablePastDate(current: Dayjs): boolean {
  return current.isBefore(today, "day");
}

function canEditTurn(
  turn: ConversationTurn,
  index: number,
): boolean {
  return (
    index === turns.value.length - 1 &&
    turn.status === "completed" &&
    !!turn.envelope &&
    turn.envelope.plan_id === selectedPlanId.value &&
    planStore.envelope?.plan_id === turn.envelope.plan_id &&
    !viewingHistoricalVersion.value &&
    !submitting.value
  );
}

function enterManualEdit(turn: ConversationTurn): void {
  if (!turn.envelope) return;
  planStore.setEnvelope(cloneEnvelope(turn.envelope));
  planStore.enterEdit();
}

function removeDraftItem(itemId: string): void {
  const day = planStore.currentDay;
  if (!day) return;
  if (day.schedule.length <= 1) {
    message.warning("每天至少保留一个景点");
    return;
  }
  planStore.removeItem(day.day, itemId);
  message.info("景点已从草稿中移除，可使用撤销恢复");
}

async function saveManualEdits(sourceTurn: ConversationTurn): Promise<void> {
  if (!sourceTurn.envelope) return;
  const previousEnvelope = cloneEnvelope(sourceTurn.envelope);
  try {
    await planStore.saveEdits();
    if (!planStore.envelope) {
      throw new Error("保存后的计划为空");
    }
    const updatedEnvelope = cloneEnvelope(planStore.envelope);
    const request: TravelRequest = {
      ...sourceTurn.request,
      additional_requirements: "手动调整行程",
      session_id: updatedEnvelope.session_id,
      previous_plan_id: previousEnvelope.plan_id,
    };
    turns.value.push({
      id: crypto.randomUUID(),
      userText: "手动调整行程顺序",
      request,
      mode: "refinement",
      status: "completed",
      logs: [
        {
          id: crypto.randomUUID(),
          timestamp: "",
          stage: "planner",
          message: "保存手动调整，并重新计算受影响的相邻路线",
          kind: "action",
        },
        {
          id: crypto.randomUUID(),
          timestamp: "",
          stage: "planner",
          message: `已创建不可变的第 ${updatedEnvelope.revision} 版计划`,
          kind: "conclusion",
        },
      ],
      envelope: updatedEnvelope,
    });
    selectedPlanId.value = updatedEnvelope.plan_id;
    planningStore.setRequest(request);
    historyStore.clearSelection();
    void refreshHistoryAfterPlanning();
    message.success("行程已更新，受影响路线已重新计算");
  } catch (error) {
    message.error(
      error instanceof Error ? error.message : "保存失败，请重试",
    );
  }
}

function logsFor(turn: ConversationTurn): PlanningLog[] {
  return turn.id === activeTurnId.value
    ? planningStore.logs
    : turn.logs;
}

function syncNormalizedConstraints(
  turn: ConversationTurn,
  envelope: PlanEnvelope,
): void {
  const summary = envelope.plan.request_summary;
  const accommodationTypes: AccommodationType[] = [
    "经济型",
    "舒适型",
    "豪华型",
    "不限",
  ];
  form.budget = summary.budget_cny;
  const accommodation = accommodationTypes.find(
    (value) => value === summary.hotel_requirement,
  );
  if (accommodation) form.accommodation = accommodation;
  turn.request = {
    ...turn.request,
    budget_cny: summary.budget_cny,
    accommodation_type: accommodation ?? turn.request.accommodation_type,
  };
  planningStore.setRequest(turn.request);
}

async function executeTurn(turn: ConversationTurn): Promise<void> {
  turns.value.push(turn);
  activeTurnId.value = turn.id;
  submitting.value = true;
  showConstraints.value = false;
  planningStore.setRequest(turn.request);
  scrollToLatest();

  try {
    const planId = await planningStore.run();
    await planStore.load(planId, turn.request);
    if (!planStore.envelope) {
      throw new Error("规划结果为空，请重新尝试");
    }
    syncNormalizedConstraints(turn, planStore.envelope);
    turn.logs = planningStore.logs.map((log) => ({ ...log }));
    turn.envelope = cloneEnvelope(planStore.envelope);
    turn.status = "completed";
    selectedPlanId.value = planStore.envelope.plan_id;
    historyStore.clearSelection();
    void refreshHistoryAfterPlanning();
  } catch (error) {
    turn.logs = planningStore.logs.map((log) => ({ ...log }));
    turn.status = "failed";
    turn.error =
      error instanceof Error ? error.message : "旅行规划未完成，请重试";
    turn.errorCode = planningStore.errorCode || undefined;
    turn.retryable = planningStore.retryable ?? undefined;
    turn.errorId = planningStore.errorId || undefined;
  } finally {
    activeTurnId.value = null;
    submitting.value = false;
    if (autoFollowLatest.value) scrollToLatest();
  }
}

async function submit(): Promise<void> {
  if (submitting.value || !validate()) return;

  const previousTurn = latestCompletedTurn.value;
  const structuredChangeText = describeConstraintChanges(
    previousTurn?.request,
  ).join("；");
  const userText =
    prompt.value.trim() ||
    structuredChangeText ||
    `请规划 ${form.destination} ` +
      `${dates.value[0].format("M月D日")}至` +
      `${dates.value[1].format("M月D日")}的旅行`;
  const baseEnvelope = previousTurn?.envelope
    ? cloneEnvelope(previousTurn.envelope)
    : null;
  const request = buildRequest(userText, baseEnvelope);
  prompt.value = "";
  await executeTurn({
    id: crypto.randomUUID(),
    userText,
    request,
    mode: baseEnvelope ? "refinement" : "initial",
    status: "planning",
    logs: [],
    envelope: null,
  });
}

async function retryTurn(failedTurn: ConversationTurn): Promise<void> {
  if (submitting.value || failedTurn.status !== "failed") return;
  await executeTurn({
    id: crypto.randomUUID(),
    userText: failedTurn.userText,
    request: JSON.parse(
      JSON.stringify(failedTurn.request),
    ) as TravelRequest,
    mode: failedTurn.mode,
    status: "planning",
    logs: [],
    envelope: null,
  });
}

function useExample(value: string): void {
  prompt.value = value;
}

function handleComposerKeydown(event: KeyboardEvent): void {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  void submit();
}
</script>

<template>
  <main class="conversation-page">
    <section v-if="!hasTurns" class="conversation-welcome">
      <span class="conversation-welcome__mark">
        <ThunderboltOutlined />
      </span>
      <p>LANGGRAPH TRAVEL HARNESS</p>
      <h1>把旅行要求告诉我，<br />我会边思考边完成规划。</h1>
      <span>
        景点、天气和住宿并行研究，过程可展开查看，结果直接在当前页面输出。
      </span>
      <div class="conversation-examples">
        <button
          v-for="example in examples"
          :key="example"
          type="button"
          @click="useExample(example)"
        >
          <PlusOutlined /> {{ example }}
        </button>
      </div>
    </section>

    <section
      v-if="activeSession?.plans.length"
      class="plan-version-bar"
      aria-label="旅行计划版本"
    >
      <div>
        <span>版本记录</span>
        <small>所有历史版本均为只读快照</small>
      </div>
      <nav>
        <button
          v-for="version in activeSession.plans"
          :key="version.plan_id"
          type="button"
          :class="{
            'plan-version-bar__selected':
              selectedPlanId === version.plan_id,
          }"
          @click="selectPlanVersion(version.plan_id)"
        >
          V{{ version.revision }}
          <i v-if="version.plan_id === activeSession.current_plan_id">最新</i>
        </button>
      </nav>
    </section>

    <section
      v-if="viewingHistoricalVersion"
      class="historical-version-banner"
    >
      <span>
        当前查看的是历史版本 V{{
          activeSession?.plans.find((item) => item.plan_id === selectedPlanId)
            ?.revision
        }}，为避免覆盖后续修改，此版本仅供查看。
      </span>
      <div>
        <button
          type="button"
          @click="returnToLatestVersion"
        >
          返回最新版本
        </button>
        <button
          type="button"
          class="historical-version-banner__fork"
          :disabled="forking"
          @click="forkHistoricalVersion"
        >
          {{ forking ? "创建中…" : "基于此版本创建新规划" }}
        </button>
      </div>
    </section>

    <section
      v-if="hasTurns"
      class="conversation-feed"
      aria-live="polite"
    >
      <article
        v-for="(turn, index) in turns"
        :key="turn.id"
        class="conversation-turn"
        :class="{
          'conversation-turn--selected':
            turn.envelope?.plan_id === selectedPlanId,
        }"
        :data-plan-id="turn.envelope?.plan_id"
      >
        <div class="conversation-user">
          <div>
            <p>{{ turn.userText }}</p>
            <small>
              {{ turn.request.destination_city }} ·
              {{ turn.request.start_date }} 至 {{ turn.request.end_date }} ·
              ¥{{ turn.request.budget_cny.toLocaleString("zh-CN") }}
            </small>
          </div>
          <span>你</span>
        </div>

        <div class="conversation-assistant">
          <span class="conversation-assistant__avatar">途</span>
          <div class="conversation-assistant__content">
            <p class="conversation-assistant__label">
              旅行规划助手
              <small>
                第 {{ index + 1 }} 轮{{ turn.mode === "refinement" ? "增量修订" : "规划" }}
              </small>
            </p>
            <ThinkingProcess
              :logs="logsFor(turn)"
              :mode="turn.mode"
              :request="turn.request"
              :status="turn.status"
            />
            <TripMap
              v-if="turn.status === 'planning'"
              class="conversation-planning-map"
              :locations="[]"
              :loading="true"
              :destination-city="turn.request.destination_city"
              :day-label="'景点筛选完成后自动绘制路线'"
            />
            <InlinePlanResult
              v-if="turn.envelope"
              :plan="turn.envelope.plan"
              :revision="turn.envelope.revision"
              :show-map="turn.envelope.plan_id === selectedPlanId"
            />
            <div
              v-if="canEditTurn(turn, index) && !planStore.editing"
              class="conversation-manual-edit-entry"
            >
              <button
                type="button"
                class="conversation-manual-edit-open"
                @click="enterManualEdit(turn)"
              >
                <EditOutlined /> 手动调整行程顺序
              </button>
              <small>保存后会创建新版本，并重新计算相邻路线</small>
            </div>
            <section
              v-if="canEditTurn(turn, index) && planStore.editing"
              class="conversation-manual-editor"
              aria-label="手动调整行程"
            >
              <header>
                <div>
                  <strong>手动调整行程</strong>
                  <small>拖动、上移、下移或删除景点</small>
                </div>
                <nav aria-label="选择编辑日期">
                  <button
                    v-for="day in planStore.visibleDays"
                    :key="day.day"
                    type="button"
                    :class="{
                      'conversation-manual-editor__day--active':
                        planStore.selectedDay === day.day,
                    }"
                    @click="planStore.selectedDay = day.day"
                  >
                    DAY {{ day.day }}
                  </button>
                </nav>
              </header>
              <ItineraryTimeline
                v-if="planStore.currentDay"
                :day="planStore.currentDay"
                :editing="true"
                :routes-stale="planStore.routesStale"
                @move="
                  (from, to) =>
                    planStore.currentDay &&
                    planStore.moveItem(planStore.currentDay.day, from, to)
                "
                @remove="removeDraftItem"
              />
              <footer>
                <button type="button" @click="planStore.cancelEdit">
                  取消
                </button>
                <button
                  type="button"
                  :disabled="planStore.history.length === 0"
                  @click="planStore.undo"
                >
                  <UndoOutlined /> 撤销
                </button>
                <button
                  type="button"
                  class="conversation-manual-editor__save"
                  :disabled="planStore.saving"
                  @click="saveManualEdits(turn)"
                >
                  <SaveOutlined />
                  {{ planStore.saving ? "保存中…" : "保存为新版本" }}
                </button>
              </footer>
            </section>
            <div v-if="turn.error" class="conversation-error">
              <span>
                {{ turn.error }}
                <small v-if="turn.errorId">错误编号：{{ turn.errorId }}</small>
              </span>
              <button
                v-if="
                  index === turns.length - 1 && turn.retryable !== false
                "
                type="button"
                :disabled="submitting"
                @click="retryTurn(turn)"
              >
                使用原需求重试
              </button>
            </div>
          </div>
        </div>
      </article>
      <div ref="feedEnd" class="conversation-feed__end" />
    </section>

    <button
      v-if="hasTurns && !autoFollowLatest"
      type="button"
      class="conversation-jump-latest"
      aria-label="回到最新一轮规划"
      @click="scrollToLatest()"
    >
      回到最新 <DownOutlined />
    </button>

    <section
      class="conversation-composer"
      :class="{ 'conversation-composer--docked': hasTurns }"
      aria-label="旅行要求输入区"
    >
      <button
        v-if="hasTurns"
        type="button"
        class="conversation-context"
        :aria-expanded="showConstraints"
        @click="showConstraints = !showConstraints"
      >
        <span>
          <SettingOutlined />
          {{ form.destination }} ·
          {{ dates[0].format("M月D日") }}—{{ dates[1].format("M月D日") }} ·
          {{ selectedPreferences.join(" / ") }}
        </span>
        <b>调整基础条件 <DownOutlined /></b>
      </button>

      <a-form :model="form" layout="vertical" @finish="submit">
        <a-alert
          v-if="viewingHistoricalVersion"
          class="historical-composer-alert"
          type="info"
          message="历史版本不能直接追问；请返回最新版本，或基于此版本创建新规划。"
          show-icon
        />
        <div
          v-show="!hasTurns || showConstraints"
          class="conversation-constraints"
        >
          <div class="conversation-constraint-grid">
            <a-form-item label="目的地">
              <a-auto-complete
                v-model:value="form.destination"
                :options="cities"
                size="large"
                placeholder="搜索城市"
                :disabled="submitting"
                :filter-option="
                  (input: string, option: { value: string }) =>
                    option.value.includes(input)
                "
              >
                <template #suffix><EnvironmentOutlined /></template>
              </a-auto-complete>
            </a-form-item>
            <a-form-item label="旅行日期">
              <a-range-picker
                v-model:value="dates"
                size="large"
                format="YYYY年M月D日"
                :allow-clear="false"
                :disabled="submitting"
                :disabled-date="disablePastDate"
                style="width: 100%"
              >
                <template #suffixIcon><CalendarOutlined /></template>
              </a-range-picker>
            </a-form-item>
            <a-form-item label="总预算">
              <a-input-number
                v-model:value="form.budget"
                size="large"
                :min="1"
                :step="500"
                :precision="0"
                :disabled="submitting"
                style="width: 100%"
              >
                <template #addonBefore>¥</template>
              </a-input-number>
            </a-form-item>
            <a-form-item label="住宿">
              <a-select
                v-model:value="form.accommodation"
                size="large"
                :disabled="submitting"
                :options="
                  ['经济型', '舒适型', '豪华型', '不限'].map((value) => ({
                    value,
                    label: value,
                  }))
                "
              />
            </a-form-item>
          </div>

          <div class="conversation-preferences">
            <span>旅行偏好</span>
            <button
              v-for="preference in preferenceOptions"
              :key="preference"
              type="button"
              :class="{
                'conversation-preference--active':
                  selectedPreferences.includes(preference),
              }"
              :disabled="submitting"
              @click="togglePreference(preference)"
            >
              {{ preference }}
            </button>
            <input
              v-model="customPreference"
              type="text"
              placeholder="+ 自定义"
              aria-label="自定义旅行偏好"
              :disabled="submitting"
              @keyup.enter.prevent="addCustomPreference"
              @blur="addCustomPreference"
            />
          </div>
        </div>

        <div class="conversation-input-shell">
          <textarea
            v-model="prompt"
            :disabled="submitting || viewingHistoricalVersion"
            :placeholder="
              hasTurns
                ? '继续调整，例如：第二天减少一个景点，午餐想吃本地菜…'
                : '补充你的要求，例如：每天 9 点后出发、带父母、少走路…'
            "
            rows="2"
            maxlength="500"
            @keydown="handleComposerKeydown"
          />
          <div>
            <small>
              {{
                hasTurns
                  ? "输入追加要求，或修改上方条件后直接继续规划"
                  : "Enter 发送 · Shift + Enter 换行"
              }}
            </small>
            <button
              type="submit"
              class="conversation-send"
              :disabled="submitting || viewingHistoricalVersion"
              :aria-label="hasTurns ? '发送追加要求' : '开始规划'"
            >
              <span>{{ hasTurns ? "继续规划" : "开始规划" }}</span>
              <a-spin v-if="submitting" size="small" />
              <SendOutlined v-else />
            </button>
          </div>
        </div>
      </a-form>
      <p class="conversation-composer__note">
        思考区展示可审计的执行摘要，不展示模型内部原始推理。
      </p>
    </section>
  </main>
</template>
