<script setup lang="ts">
import {
  CalendarOutlined,
  CarOutlined,
  EnvironmentOutlined,
  HomeOutlined,
  WalletOutlined,
} from "@ant-design/icons-vue";
import { computed, ref, watch } from "vue";
import type {
  DailyItinerary,
  MapLocation,
  TravelPlan,
} from "../types/travel";
import { recommendedRouteDisplay } from "../utils/route-display";
import TripMap from "./TripMap.vue";

const props = withDefaults(
  defineProps<{
    plan: TravelPlan;
    revision: number;
    showMap?: boolean;
  }>(),
  {
    showMap: true,
  },
);

const activeMapDay = ref(props.plan.daily_itinerary[0]?.day ?? 1);

const selectedMapDay = computed(
  () =>
    props.plan.daily_itinerary.find(
      (day) => day.day === activeMapDay.value,
    ) ?? props.plan.daily_itinerary[0],
);

const mapLocations = computed<MapLocation[]>(() => {
  const day = selectedMapDay.value;
  if (!day) return [];
  const hotel = props.plan.selected_hotel;
  const locations = [
    {
      id: `revision-${props.revision}-hotel`,
      name: hotel.name,
      address: hotel.address,
      longitude: hotel.location.longitude,
      latitude: hotel.location.latitude,
      kind: "hotel" as const,
    },
    ...day.schedule.map((item, index) => ({
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

watch(
  () => props.revision,
  () => {
    activeMapDay.value = props.plan.daily_itinerary[0]?.day ?? 1;
  },
);

function formatDate(date: string): string {
  const value = new Date(`${date}T00:00:00`);
  if (Number.isNaN(value.getTime())) return date;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(value);
}

function money(value: number | null): string {
  return value == null ? "待确认" : `¥${value.toLocaleString("zh-CN")}`;
}

function mapDayLabel(day: DailyItinerary): string {
  return `第 ${day.day} 天 · ${day.theme}`;
}
</script>

<template>
  <section class="inline-plan" aria-label="旅行规划结果">
    <header class="inline-plan__hero">
      <div>
        <p>规划结果 · REVISION {{ revision }}</p>
        <h2>
          {{ plan.request_summary.destination_city }}
          <span>{{ plan.request_summary.days }} 天旅行提案</span>
        </h2>
      </div>
      <div class="inline-plan__budget">
        <small>预计总花费</small>
        <strong>{{ money(plan.budget_summary.estimated_total) }}</strong>
        <span>
          预算 {{ money(plan.budget_summary.total_budget) }}
        </span>
      </div>
    </header>

    <div class="inline-plan__facts">
      <span>
        <CalendarOutlined />
        {{ plan.request_summary.start_date }} —
        {{ plan.request_summary.end_date }}
      </span>
      <span>
        <HomeOutlined /> {{ plan.selected_hotel.name }}
      </span>
      <span>
        <WalletOutlined />
        结余 {{ money(plan.budget_summary.remaining) }}
      </span>
    </div>

    <article class="inline-hotel">
      <span class="inline-hotel__icon"><HomeOutlined /></span>
      <div>
        <small>推荐住宿</small>
        <h3>{{ plan.selected_hotel.name }}</h3>
        <p>{{ plan.selected_hotel.selection_reason }}</p>
        <span>{{ plan.selected_hotel.address }}</span>
      </div>
      <strong>
        {{ money(plan.selected_hotel.price_cny_per_night) }}
        <small>/ 晚</small>
      </strong>
    </article>

    <div class="inline-weather" aria-label="旅行天气">
      <article
        v-for="weather in plan.weather_summary"
        :key="weather.date"
      >
        <small>{{ formatDate(weather.date) }}</small>
        <strong>{{ weather.day_weather ?? "天气待确认" }}</strong>
        <span>
          {{ weather.min_temperature_c ?? "待确认" }}° —
          {{ weather.max_temperature_c ?? "待确认" }}°
        </span>
      </article>
    </div>

    <section
      v-if="showMap && selectedMapDay"
      class="inline-map-section"
      aria-label="高德景点路线地图"
    >
      <header class="inline-map-section__heading">
        <div>
          <small>AMAP ROUTE VIEW</small>
          <h3>按天查看景点连线与分段距离</h3>
        </div>
        <nav aria-label="选择地图日期">
          <button
            v-for="day in plan.daily_itinerary"
            :key="day.day"
            type="button"
            :class="{ 'is-active': activeMapDay === day.day }"
            @click="activeMapDay = day.day"
          >
            DAY {{ String(day.day).padStart(2, "0") }}
          </button>
        </nav>
      </header>

      <div class="inline-map-section__content">
        <TripMap
          :locations="mapLocations"
          :routes="selectedMapDay.routes"
          :day-label="mapDayLabel(selectedMapDay)"
        />
        <ol class="inline-map-routes" aria-label="地图路线距离明细">
          <li
            v-for="route in selectedMapDay.routes"
            :key="route.route_id"
          >
            <span>{{ route.sequence }}</span>
            <div>
              <small>{{ route.origin.name }} → {{ route.destination.name }}</small>
              <strong>
                {{ recommendedRouteDisplay(route).distanceLabel }}
                · {{ recommendedRouteDisplay(route).modeLabel }}
                {{ recommendedRouteDisplay(route).durationLabel }}
              </strong>
            </div>
          </li>
        </ol>
      </div>
    </section>

    <div class="inline-days">
      <article
        v-for="day in plan.daily_itinerary"
        :key="day.day"
        class="inline-day"
      >
        <header>
          <span>DAY {{ String(day.day).padStart(2, "0") }}</span>
          <div>
            <small>{{ formatDate(day.date) }}</small>
            <h3>{{ day.theme }}</h3>
          </div>
          <strong>{{ money(day.estimated_cost_cny.subtotal) }}</strong>
        </header>

        <p class="inline-day__weather">
          {{ day.weather_advice }}
        </p>

        <ol class="inline-schedule">
          <li
            v-for="(item, index) in day.schedule"
            :key="item.schedule_item_id"
          >
            <time>{{ item.time_slot }}</time>
            <span class="inline-schedule__dot" />
            <div>
              <h4>{{ item.place_name }}</h4>
              <p>{{ item.activity }}</p>
              <small><EnvironmentOutlined /> {{ item.address }}</small>
              <div v-if="day.routes[index]" class="inline-route">
                <CarOutlined />
                下一段建议{{
                  recommendedRouteDisplay(day.routes[index]).modeLabel
                }}
                ·
                {{
                  recommendedRouteDisplay(day.routes[index]).durationLabel
                }}
              </div>
            </div>
          </li>
        </ol>
      </article>
    </div>

    <footer class="inline-plan__footer">
      <div>
        <h3>预订与安全提醒</h3>
        <ul>
          <li
            v-for="tip in plan.booking_and_safety_tips"
            :key="tip"
          >
            {{ tip }}
          </li>
        </ul>
      </div>
      <div>
        <h3>预算说明</h3>
        <ul>
          <li v-for="note in plan.budget_summary.notes" :key="note">
            {{ note }}
          </li>
        </ul>
      </div>
    </footer>
  </section>
</template>
