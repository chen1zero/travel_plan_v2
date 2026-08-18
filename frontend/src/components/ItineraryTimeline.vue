<script setup lang="ts">
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CarOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  DragOutlined,
  EnvironmentOutlined,
  SwapOutlined,
} from "@ant-design/icons-vue";
import { ref } from "vue";
import type {
  DailyItinerary,
  RouteSegment,
} from "../types/travel";
import {
  durationLabel,
  publicTransitModeLabel,
  recommendedRouteDisplay,
  type TransportMode,
} from "../utils/route-display";

const props = defineProps<{
  day: DailyItinerary;
  editing: boolean;
  routesStale: boolean;
}>();

const emit = defineEmits<{
  move: [from: number, to: number];
  remove: [itemId: string];
}>();

const draggingIndex = ref<number | null>(null);

function recommendedLabel(route: RouteSegment): string {
  return recommendedRouteDisplay(route).modeLabel;
}

function transitLabel(route: RouteSegment): string {
  return publicTransitModeLabel(route.public_transit);
}

function transitLines(route: RouteSegment): string {
  return route.public_transit.line_names?.join(" → ") ?? "";
}

function duration(
  route: RouteSegment,
  mode: TransportMode,
): string {
  return route[mode].available
    ? durationLabel(route[mode])
    : "不可用";
}

function drop(index: number): void {
  if (draggingIndex.value === null) return;
  emit("move", draggingIndex.value, index);
  draggingIndex.value = null;
}
</script>

<template>
  <section class="itinerary-timeline">
    <a-alert
      v-if="editing && routesStale"
      class="route-stale-alert"
      type="warning"
      show-icon
      message="景点顺序已修改"
      description="保存后将重新计算受影响的路线，地图编号已按新顺序更新。"
    />

    <article
      v-for="(item, index) in props.day.schedule"
      :key="item.schedule_item_id"
      class="schedule-block"
      :class="{ 'schedule-block--editing': editing }"
      :draggable="editing"
      @dragstart="draggingIndex = index"
      @dragover.prevent
      @drop="drop(index)"
    >
      <div
        v-if="day.routes[index]"
        class="route-connector"
        :class="{ 'route-connector--stale': routesStale }"
      >
        <span class="connector-line" />
        <div class="route-summary">
          <template v-if="!routesStale">
            <strong class="route-pair">
              {{ day.routes[index].origin.name }}
              <span>→</span>
              {{ day.routes[index].destination.name }}
            </strong>
            <span class="route-recommended">
              <SwapOutlined />
              推荐{{ recommendedLabel(day.routes[index]) }}
              ·
              {{
                recommendedRouteDisplay(
                  day.routes[index],
                ).distanceLabel
              }}
              ·
              {{
                recommendedRouteDisplay(
                  day.routes[index],
                ).durationLabel
              }}
            </span>
            <span>
              步行 {{ duration(day.routes[index], "walking") }}
            </span>
            <span>
              <CarOutlined />
              打车 {{ duration(day.routes[index], "driving") }}
            </span>
            <span>
              {{ transitLabel(day.routes[index]) }}
              {{ duration(day.routes[index], "public_transit") }}
              <template v-if="transitLines(day.routes[index])">
                · {{ transitLines(day.routes[index]) }}
              </template>
            </span>
          </template>
          <span v-else class="route-recalculate">
            保存调整后重新计算相邻距离与推荐交通时间
          </span>
        </div>
      </div>

      <div class="schedule-card">
        <div class="schedule-order">
          <DragOutlined v-if="editing" />
          <span v-else>{{ item.order }}</span>
        </div>

        <div class="schedule-time">
          <ClockCircleOutlined />
          <strong>{{ item.time_slot }}</strong>
          <span>
            {{
              item.duration_minutes === null
                ? "时长待确认"
                : `${item.duration_minutes} 分钟`
            }}
          </span>
        </div>

        <div class="schedule-main">
          <div class="schedule-title-row">
            <h3>{{ item.place_name }}</h3>
            <div v-if="editing" class="edit-actions">
              <a-button
                type="text"
                size="small"
                :disabled="index === 0"
                aria-label="上移景点"
                @click="emit('move', index, index - 1)"
              >
                <ArrowUpOutlined />
              </a-button>
              <a-button
                type="text"
                size="small"
                :disabled="index === day.schedule.length - 1"
                aria-label="下移景点"
                @click="emit('move', index, index + 1)"
              >
                <ArrowDownOutlined />
              </a-button>
              <a-popconfirm
                title="确定从行程中删除这个景点？"
                ok-text="删除"
                cancel-text="保留"
                @confirm="emit('remove', item.schedule_item_id)"
              >
                <a-button
                  danger
                  type="text"
                  size="small"
                  aria-label="删除景点"
                >
                  <DeleteOutlined />
                </a-button>
              </a-popconfirm>
            </div>
          </div>
          <p class="schedule-address">
            <EnvironmentOutlined /> {{ item.address }}
          </p>
          <p class="schedule-activity">{{ item.activity }}</p>
          <div class="schedule-notes">
            <span v-for="note in item.notes" :key="note">
              {{ note }}
            </span>
          </div>
        </div>
      </div>
    </article>
  </section>
</template>
