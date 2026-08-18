<script setup lang="ts">
import {
  ArrowLeftOutlined,
  BankOutlined,
  CalendarOutlined,
  CheckOutlined,
  EditOutlined,
  EnvironmentOutlined,
  ReloadOutlined,
  SafetyOutlined,
  SaveOutlined,
  UndoOutlined,
  WalletOutlined,
} from "@ant-design/icons-vue";
import dayjs from "dayjs";
import {
  computed,
  onMounted,
  ref,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { message } from "ant-design-vue";
import BudgetPanel from "../components/BudgetPanel.vue";
import ItineraryTimeline from "../components/ItineraryTimeline.vue";
import PlanningProgressPanel from "../components/PlanningProgressPanel.vue";
import TripMap from "../components/TripMap.vue";
import WeatherStrip from "../components/WeatherStrip.vue";
import { usePlanStore } from "../stores/plan";
import { usePlanningStore } from "../stores/planning";
import type { MapLocation } from "../types/travel";

const route = useRoute();
const router = useRouter();
const planStore = usePlanStore();
const planningStore = usePlanningStore();
const loading = ref(false);

const plan = computed(() => planStore.plan);
const dayTab = computed({
  get: () => String(planStore.selectedDay),
  set: (value: string) => {
    planStore.selectedDay = Number(value);
  },
});

const mapLocations = computed<MapLocation[]>(() => {
  if (!plan.value || !planStore.currentDay) return [];
  const locations = [
    {
      id: "selected-hotel",
      name: plan.value.selected_hotel.name,
      address: plan.value.selected_hotel.address,
      longitude: plan.value.selected_hotel.location.longitude,
      latitude: plan.value.selected_hotel.location.latitude,
      kind: "hotel",
    },
    ...planStore.currentDay.schedule.map((item, index) => ({
      id: item.schedule_item_id,
      name: item.place_name,
      address: item.address,
      longitude: item.location.longitude,
      latitude: item.location.latitude,
      kind: "attraction" as const,
      order: index + 1,
    })),
  ];
  return locations.filter(
    (location): location is MapLocation =>
      typeof location.longitude === "number" &&
      Number.isFinite(location.longitude) &&
      typeof location.latitude === "number" &&
      Number.isFinite(location.latitude),
  );
});

const dateLabel = computed(() => {
  if (!plan.value) return "";
  const summary = plan.value.request_summary;
  return `${dayjs(summary.start_date).format("M月D日")}—${dayjs(
    summary.end_date,
  ).format("M月D日")}`;
});

const weatherAlert = computed(() => {
  const days = plan.value?.weather_summary ?? [];
  const index = days.findIndex((day) =>
    `${day.day_weather}${day.night_weather}`.match(
      /雨|雪|雷|雾|沙|霾/,
    ),
  );
  if (index < 0) {
    return {
      title: "天气整体适宜",
      detail: "仍建议出发前再次查看实时天气",
    };
  }
  return {
    title: `第 ${index + 1} 天${days[index].day_weather}`,
    detail:
      days[index].advice[0] ||
      "请根据天气变化灵活调整户外安排",
  };
});

async function loadPlan(): Promise<void> {
  const planId = String(route.params.planId);
  loading.value = true;
  try {
    await planStore.load(
      planId,
      planningStore.request,
    );
  } catch (error) {
    message.error(
      error instanceof Error ? error.message : "行程加载失败",
    );
    await router.replace("/");
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  try {
    await planStore.saveEdits();
    message.success("行程已更新，受影响路线已重新计算");
  } catch (error) {
    message.error(
      error instanceof Error ? error.message : "保存失败，请重试",
    );
  }
}

function removeItem(itemId: string): void {
  if (!planStore.currentDay) return;
  if (planStore.currentDay.schedule.length <= 1) {
    message.warning("每天至少保留一个景点");
    return;
  }
  planStore.removeItem(planStore.currentDay.day, itemId);
  message.info("景点已从草稿中移除，可使用撤销恢复");
}

onMounted(loadPlan);
</script>

<template>
  <main v-if="plan" class="result-page">
    <section
      v-if="planningStore.logs.length > 0"
      class="result-process-section"
    >
      <header class="result-process-heading">
        <div>
          <p class="section-kicker">完整推演过程</p>
          <h2>从你的要求，到这份旅行规划</h2>
        </div>
        <p>
          思考摘要、工具行动和查证结果都已保留，下面是最终规划。
        </p>
      </header>
      <PlanningProgressPanel />
    </section>

    <section class="result-hero">
      <button
        type="button"
        class="back-link"
        @click="router.push('/')"
      >
        <ArrowLeftOutlined /> 返回重新规划
      </button>

      <div class="result-title-row">
        <div>
          <p class="eyebrow">YOUR CURATED JOURNEY · REV. {{ planStore.envelope?.revision }}</p>
          <h1>
            {{ plan.request_summary.destination_city }}
            <span>·</span>
            {{ plan.request_summary.days }}日
            {{ plan.request_summary.preferences[0] }}之旅
          </h1>
          <div class="result-meta">
            <span><CalendarOutlined /> {{ dateLabel }}</span>
            <span>
              <WalletOutlined />
              预算 ¥{{ plan.request_summary.budget_cny.toLocaleString() }}
            </span>
            <span>
              <BankOutlined />
              {{ plan.request_summary.hotel_requirement }}酒店
            </span>
          </div>
        </div>

        <div v-if="!planStore.editing" class="result-actions">
          <a-button size="large" @click="router.push('/')">
            <ReloadOutlined /> 重新规划
          </a-button>
          <a-button
            type="primary"
            size="large"
            class="primary-cta"
            @click="planStore.enterEdit"
          >
            <EditOutlined /> 编辑行程
          </a-button>
        </div>
        <div v-else class="result-actions">
          <a-button size="large" @click="planStore.cancelEdit">
            取消
          </a-button>
          <a-button
            :disabled="planStore.history.length === 0"
            size="large"
            @click="planStore.undo"
          >
            <UndoOutlined /> 撤销
          </a-button>
          <a-button
            type="primary"
            size="large"
            class="primary-cta"
            :loading="planStore.saving"
            @click="save"
          >
            <SaveOutlined /> 保存调整
          </a-button>
        </div>
      </div>

      <div class="result-snapshot">
        <article>
          <span>旅行主题</span>
          <strong>{{ plan.request_summary.preferences.join(" · ") }}</strong>
          <small>按你的偏好筛选</small>
        </article>
        <article>
          <span>预计支出</span>
          <strong>
            {{
              plan.budget_summary.estimated_total === null
                ? "部分待确认"
                : `¥${plan.budget_summary.estimated_total.toLocaleString()}`
            }}
          </strong>
          <small>
            {{
              plan.budget_summary.remaining === null
                ? "价格更新后计算"
                : `预计剩余 ¥${plan.budget_summary.remaining.toLocaleString()}`
            }}
          </small>
        </article>
        <article>
          <span>已选住宿</span>
          <strong>{{ plan.selected_hotel.name }}</strong>
          <small>{{ plan.selected_hotel.address }}</small>
        </article>
        <article class="snapshot-alert">
          <span>需要留意</span>
          <strong>{{ weatherAlert.title }}</strong>
          <small>{{ weatherAlert.detail }}</small>
        </article>
      </div>
    </section>

    <WeatherStrip :days="plan.weather_summary" />

    <section class="itinerary-section">
      <div class="itinerary-heading">
        <div>
          <p class="section-kicker">每日行程</p>
          <h2>
            松弛有度的{{ plan.request_summary.destination_city
            }}{{ plan.request_summary.days }}日
          </h2>
        </div>
        <p>
          每一段路线都对比了步行、驾车和公共交通。
          点击日期查看当天地图。
        </p>
      </div>

      <a-tabs v-model:active-key="dayTab" class="day-tabs">
        <a-tab-pane
          v-for="day in planStore.visibleDays"
          :key="String(day.day)"
        >
          <template #tab>
            <span class="day-tab-label">
              <b>DAY {{ String(day.day).padStart(2, "0") }}</b>
              <span>{{ day.date.slice(5).replace("-", "月") }}日</span>
            </span>
          </template>
        </a-tab-pane>
      </a-tabs>

      <div v-if="planStore.currentDay" class="itinerary-grid">
        <div class="itinerary-content">
          <div class="day-overview">
            <span>DAY {{ String(planStore.currentDay.day).padStart(2, "0") }}</span>
            <div>
              <h3>{{ planStore.currentDay.theme }}</h3>
              <p>{{ planStore.currentDay.weather_advice }}</p>
            </div>
          </div>

          <ItineraryTimeline
            :day="planStore.currentDay"
            :editing="planStore.editing"
            :routes-stale="planStore.routesStale"
            @move="
              (from, to) =>
                planStore.moveItem(
                  planStore.currentDay!.day,
                  from,
                  to,
                )
            "
            @remove="removeItem"
          />
        </div>

        <aside class="itinerary-rail">
          <TripMap
            :locations="mapLocations"
            :routes="planStore.currentDay.routes"
            :routes-stale="planStore.routesStale"
            :day-label="`第 ${planStore.currentDay.day} 天 · ${planStore.currentDay.theme}`"
          />

          <article class="hotel-card">
            <div class="hotel-card__icon"><BankOutlined /></div>
            <div>
              <span>本次推荐住宿</span>
              <h3>{{ plan.selected_hotel.name }}</h3>
              <p><EnvironmentOutlined /> {{ plan.selected_hotel.address }}</p>
              <small>{{ plan.selected_hotel.selection_reason }}</small>
            </div>
            <b>
              {{
                plan.selected_hotel.price_cny_per_night === null
                  ? "价格待确认"
                  : `¥${plan.selected_hotel.price_cny_per_night}/晚`
              }}
            </b>
          </article>
        </aside>
      </div>
    </section>

    <section class="result-bottom-grid">
      <BudgetPanel :budget="plan.budget_summary" />
      <section class="tips-panel">
        <header>
          <SafetyOutlined />
          <div>
            <p class="section-kicker">预订与安全提示</p>
            <h2>出发前，再确认这些</h2>
          </div>
        </header>
        <ul>
          <li
            v-for="tip in plan.booking_and_safety_tips"
            :key="tip"
          >
            <CheckOutlined /> <span>{{ tip }}</span>
          </li>
        </ul>
        <div class="unresolved">
          <span>仍需确认</span>
          <a-tag
            v-for="field in plan.request_summary.unresolved_fields"
            :key="field"
          >
            {{ field }}
          </a-tag>
        </div>
      </section>
    </section>

    <footer class="result-footer">
      <span>途画 · 智能旅行助手</span>
      <p>{{ plan.data_notes.join(" · ") }}</p>
    </footer>
  </main>

  <main v-else class="page-loading">
    <a-spin size="large" :spinning="loading" />
    <p>正在打开你的旅行计划…</p>
  </main>
</template>
