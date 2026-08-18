<script setup lang="ts">
import {
  BankOutlined,
  BranchesOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  CloudOutlined,
  EyeOutlined,
  FileDoneOutlined,
  LoadingOutlined,
  MergeCellsOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  ToolOutlined,
  UserOutlined,
} from "@ant-design/icons-vue";
import { computed } from "vue";
import {
  PLANNING_STAGES,
  usePlanningStore,
} from "../stores/planning";
import type {
  PlanningLog,
  PlanningStage,
  PlanningTraceKind,
} from "../types/travel";

const store = usePlanningStore();
const researchStages = PLANNING_STAGES.slice(0, 3);

const stageIcons = {
  attraction: SearchOutlined,
  weather: CloudOutlined,
  hotel: BankOutlined,
  planner: MergeCellsOutlined,
};

const traceMeta: Record<
  PlanningTraceKind,
  { label: string; icon: typeof BulbOutlined }
> = {
  thought: { label: "思考摘要", icon: BulbOutlined },
  action: { label: "采取行动", icon: ToolOutlined },
  observation: { label: "获得观察", icon: EyeOutlined },
  conclusion: { label: "形成结论", icon: CheckCircleOutlined },
  system: { label: "流程事件", icon: BranchesOutlined },
};

const activeSummary = computed(() => {
  const running = PLANNING_STAGES.filter(
    (stage) => store.nodeStatuses[stage.key] === "running",
  );
  if (store.status === "completed") return "推演完成，规划结果已生成";
  if (running.length > 1) return `${running.length} 条研究思路正在并行推进`;
  if (running.length === 1) return `正在${running[0].title}`;
  return "正在理解你的旅行要求";
});

function statusLabel(stage: PlanningStage): string {
  const status = store.nodeStatuses[stage];
  if (status === "completed") return "已完成";
  if (status === "running") return "思考中";
  if (status === "failed") return "未完成";
  return "待开始";
}

function stageLogs(stage: PlanningStage): PlanningLog[] {
  return store.logs.filter((log) => log.stage === stage);
}

function logKind(log: PlanningLog): PlanningTraceKind {
  return log.kind ?? "system";
}
</script>

<template>
  <section class="reasoning-workspace" aria-live="polite">
    <header class="reasoning-overview">
      <div class="progress-number">
        <strong>{{ store.progress }}</strong>
        <span>%</span>
      </div>
      <div class="progress-copy">
        <span>TRAVEL PLANNING · EXECUTION TRACE</span>
        <b>{{ activeSummary }}</b>
      </div>
      <div class="reasoning-progress">
        <a-progress
          :percent="store.progress"
          :show-info="false"
          stroke-color="#ff6b35"
          trail-color="#e8e3d8"
        />
        <small>
          <SafetyCertificateOutlined />
          展示可审计摘要，不展示模型内部原始推理
        </small>
      </div>
    </header>

    <div class="reasoning-flow">
      <section class="requirement-card">
        <span class="flow-index">01</span>
        <span class="requirement-icon"><UserOutlined /></span>
        <div class="requirement-main">
          <p>收到你的要求</p>
          <h3>
            规划一趟{{ store.request?.destination_city || "目的地" }}之旅
          </h3>
          <div v-if="store.request" class="requirement-facts">
            <span>{{ store.request.start_date }} → {{ store.request.end_date }}</span>
            <span>预算 ¥{{ store.request.budget_cny.toLocaleString() }}</span>
            <span>{{ store.request.accommodation_type }}住宿</span>
          </div>
          <div v-if="store.request" class="requirement-preferences">
            <b v-for="preference in store.request.preferences" :key="preference">
              {{ preference }}
            </b>
          </div>
          <blockquote v-if="store.request?.additional_requirements">
            “{{ store.request.additional_requirements }}”
          </blockquote>
        </div>
        <span class="requirement-state"><CheckCircleOutlined /> 已理解</span>
      </section>

      <div class="flow-connector">
        <span />
        <small>拆解为三条并行研究思路</small>
        <span />
      </div>

      <section class="reasoning-phase">
        <header class="phase-heading">
          <span class="flow-index">02</span>
          <div>
            <p>并行思考与行动</p>
            <h3>分别查证景点、天气和住宿</h3>
          </div>
          <b><BranchesOutlined /> PARALLEL</b>
        </header>

        <div class="reasoning-lanes">
          <article
            v-for="stage in researchStages"
            :key="stage.key"
            class="reasoning-lane"
            :class="`reasoning-lane--${store.nodeStatuses[stage.key]}`"
          >
            <header>
              <span class="lane-icon">
                <CheckCircleOutlined
                  v-if="store.nodeStatuses[stage.key] === 'completed'"
                />
                <LoadingOutlined
                  v-else-if="store.nodeStatuses[stage.key] === 'running'"
                  spin
                />
                <component :is="stageIcons[stage.key]" v-else />
              </span>
              <div>
                <small>{{ stage.key.toUpperCase() }} RESEARCH</small>
                <strong>{{ stage.title }}</strong>
              </div>
              <b>{{ statusLabel(stage.key) }}</b>
            </header>

            <div class="lane-trace">
              <div v-if="stageLogs(stage.key).length === 0" class="trace-empty">
                <span />
                <p>等待开始这一步推演</p>
              </div>
              <article
                v-for="log in stageLogs(stage.key)"
                :key="log.id"
                class="trace-entry"
                :class="`trace-entry--${logKind(log)}`"
              >
                <span class="trace-icon">
                  <component :is="traceMeta[logKind(log)].icon" />
                </span>
                <div>
                  <header>
                    <b>{{ traceMeta[logKind(log)].label }}</b>
                    <time>{{ log.timestamp }}</time>
                  </header>
                  <p>{{ log.message }}</p>
                  <small v-if="log.detail">{{ log.detail }}</small>
                </div>
              </article>
            </div>
          </article>
        </div>
      </section>

      <div class="merge-step">
        <span />
        <div><MergeCellsOutlined /> 汇合三路结论</div>
        <span />
      </div>

      <section
        class="synthesis-phase"
        :class="`synthesis-phase--${store.nodeStatuses.planner}`"
      >
        <header class="phase-heading">
          <span class="flow-index">03</span>
          <span class="lane-icon">
            <CheckCircleOutlined
              v-if="store.nodeStatuses.planner === 'completed'"
            />
            <LoadingOutlined
              v-else-if="store.nodeStatuses.planner === 'running'"
              spin
            />
            <MergeCellsOutlined v-else />
          </span>
          <div>
            <p>综合思考与规划</p>
            <h3>编排每天行程，并核对每一段路线</h3>
          </div>
          <b>{{ statusLabel("planner") }}</b>
        </header>

        <div class="synthesis-trace">
          <div v-if="stageLogs('planner').length === 0" class="trace-empty">
            <span />
            <p>等待三路研究全部完成后开始</p>
          </div>
          <article
            v-for="log in stageLogs('planner')"
            :key="log.id"
            class="trace-entry trace-entry--wide"
            :class="`trace-entry--${logKind(log)}`"
          >
            <span class="trace-icon">
              <component :is="traceMeta[logKind(log)].icon" />
            </span>
            <div>
              <header>
                <b>{{ traceMeta[logKind(log)].label }}</b>
                <time>{{ log.timestamp }}</time>
              </header>
              <p>{{ log.message }}</p>
              <small v-if="log.detail">{{ log.detail }}</small>
            </div>
          </article>
        </div>
      </section>

      <section
        class="output-step"
        :class="{ 'output-step--ready': store.status === 'completed' }"
      >
        <span class="flow-index">04</span>
        <span class="output-icon"><FileDoneOutlined /></span>
        <div>
          <p>输出规划结果</p>
          <h3>
            {{
              store.status === "completed"
                ? "思考与行动已完成，完整行程如下"
                : "完成全部核对后，将生成完整旅行计划"
            }}
          </h3>
        </div>
        <CheckCircleOutlined v-if="store.status === 'completed'" />
        <LoadingOutlined v-else spin />
      </section>
    </div>
  </section>
</template>
