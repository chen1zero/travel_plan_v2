<script setup lang="ts">
import {
  CheckCircleOutlined,
  DownOutlined,
  LoadingOutlined,
  RightOutlined,
} from "@ant-design/icons-vue";
import { computed, reactive, ref, watch } from "vue";
import type {
  PlanningLog,
  PlanningStage,
  TravelRequest,
} from "../types/travel";

const props = defineProps<{
  logs: PlanningLog[];
  mode: "initial" | "refinement";
  request: TravelRequest;
  status: "planning" | "completed" | "failed";
}>();

const summaryOpen = ref(true);
const actionOpen = reactive<Record<string, boolean>>({});

const stageMeta: Record<
  PlanningStage,
  { action: string; fallback: string }
> = {
  attraction: {
    action: "搜索并筛选候选景点",
    fallback: "我先根据你的偏好梳理目的地，再查找适合放进行程的景点。",
  },
  weather: {
    action: "查询旅行日期天气",
    fallback: "我会核对旅行期间的天气，让室内外安排更合理。",
  },
  hotel: {
    action: "比较住宿位置与预算",
    fallback: "我会结合预算和每天的动线，选择更方便的住宿区域。",
  },
  planner: {
    action: "合并研究结果并编排行程",
    fallback: "景点、天气和住宿信息齐备后，我会把它们合成完整行程。",
  },
};

const stages: PlanningStage[] = [
  "attraction",
  "weather",
  "hotel",
  "planner",
];

const visibleStages = computed<PlanningStage[]>(() => stages);

const groups = computed(() =>
  visibleStages.value
    .map((stage) => {
      const logs = props.logs.filter((log) => log.stage === stage);
      const thought = logs.find((log) => log.kind === "thought");
      const skipped = logs.find(
        (log) => log.detail === "node.skipped",
      );
      const actions = logs.filter((log) => log.kind === "action");
      const results = logs.filter(
        (log) =>
          log.kind === "observation" || log.kind === "conclusion",
      );
      return {
        stage,
        intro:
          skipped?.message ?? thought?.message ?? stageMeta[stage].fallback,
        actions,
        results,
      };
    })
    .filter(
      (group) =>
        group.actions.length > 0 ||
        group.results.length > 0 ||
        props.status === "planning",
    ),
);

const dayCount = computed(() => {
  const start = new Date(`${props.request.start_date}T00:00:00Z`);
  const end = new Date(`${props.request.end_date}T00:00:00Z`);
  return Math.max(
    1,
    Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1,
  );
});

const requirementSummary = computed(() => {
  if (props.mode === "refinement") {
    const requirement =
      props.request.additional_requirements || "调整当前行程";
    return `已载入上一版 ${props.request.destination_city} 旅行计划及其中的完整日程、研究结果和路线。本轮新增要求是：${requirement.replaceAll("\n", "；")}。Harness 会先分析变化，只重跑受影响的景点、天气或住宿节点，其余节点直接继承上一版结果，再由 Planner 输出完整新版本。`;
  }
  const base = `用户希望规划 ${props.request.destination_city} ${dayCount.value} 天旅行，日期为 ${props.request.start_date} 至 ${props.request.end_date}，总预算约 ¥${props.request.budget_cny.toLocaleString("zh-CN")}，偏好 ${props.request.preferences.join("、")}，住宿倾向为${props.request.accommodation_type}。`;
  const extra = props.request.additional_requirements
    ? ` 还希望：${props.request.additional_requirements.replaceAll("\n", "；")}。`
    : "";
  return `${base}${extra} 我会并行收集景点、天气和住宿信息，再合并路线与预算，输出可执行的日程。`;
});

const requirementTitle = computed(() =>
  props.mode === "refinement"
    ? `在上一版基础上调整${props.request.destination_city}行程`
    : `明确${props.request.destination_city}旅行规划要求`,
);

function actionLabel(stage: PlanningStage): string {
  if (props.mode === "refinement") {
    if (stage === "planner") return "合并历史结果并修订完整行程";
    const label = {
      attraction: "景点研究",
      weather: "天气研究",
      hotel: "住宿研究",
    }[stage];
    return `判断是否需要更新${label}`;
  }
  return stageMeta[stage].action;
}

const analysisLogs = computed(() =>
  props.logs.filter(
    (log) =>
      log.stage === "harness" && log.detail === "change.analysis",
  ),
);

watch(
  () => props.status,
  (status) => {
    if (status === "planning") summaryOpen.value = true;
  },
);
</script>

<template>
  <section class="thinking-process" :class="`thinking-process--${status}`">
    <button
      type="button"
      class="thinking-requirement-toggle"
      :aria-expanded="summaryOpen"
      @click="summaryOpen = !summaryOpen"
    >
      <span>{{ requirementTitle }}</span>
      <DownOutlined :class="{ 'is-open': summaryOpen }" />
    </button>

    <div v-show="summaryOpen" class="thinking-requirement-body">
      <p>{{ requirementSummary }}</p>
      <div class="thinking-status-line">
        <LoadingOutlined v-if="status === 'planning'" spin />
        <CheckCircleOutlined v-else />
        <span v-if="status === 'planning'">思考中</span>
        <span v-else-if="status === 'completed'">已完成</span>
        <span v-else>未完成</span>
      </div>
      <div
        v-if="analysisLogs.length"
        class="thinking-change-analysis"
      >
        <strong>变更分析</strong>
        <p v-for="log in analysisLogs" :key="log.id">
          {{ log.message }}
        </p>
      </div>
    </div>

    <div class="thinking-divider" />

    <div v-if="logs.length" class="thinking-narrative">
      <article
        v-for="group in groups"
        :key="group.stage"
        class="thinking-narrative-step"
      >
        <p class="thinking-narrative-step__intro">
          {{ group.intro }}
        </p>

        <button
          type="button"
          class="thinking-action-toggle"
          :aria-expanded="Boolean(actionOpen[group.stage])"
          @click="actionOpen[group.stage] = !actionOpen[group.stage]"
        >
          <span>{{ actionLabel(group.stage) }}</span>
          <RightOutlined
            :class="{ 'is-open': actionOpen[group.stage] }"
          />
        </button>

        <div
          v-show="actionOpen[group.stage]"
          class="thinking-action-detail"
        >
          <p v-if="!group.actions.length">正在准备相关工具与数据。</p>
          <p v-for="action in group.actions" :key="action.id">
            {{ action.message }}
            <small v-if="action.detail">{{ action.detail }}</small>
          </p>
        </div>

        <div class="thinking-observations">
          <p v-for="result in group.results" :key="result.id">
            {{ result.message }}
          </p>
          <p
            v-if="
              status === 'planning' &&
              !group.results.length &&
              group === groups[groups.length - 1]
            "
            class="thinking-observations__waiting"
          >
            <LoadingOutlined spin /> 正在等待结果…
          </p>
        </div>
      </article>
    </div>

    <div v-else class="thinking-narrative-empty">
      <LoadingOutlined v-if="status === 'planning'" spin />
      正在理解你的旅行要求…
    </div>

    <p class="thinking-disclosure">
      页面展示的是脱敏执行摘要，不包含模型内部原始推理。
    </p>
  </section>
</template>
