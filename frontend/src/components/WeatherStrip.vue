<script setup lang="ts">
import {
  CloudOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons-vue";
import dayjs from "dayjs";
import type { WeatherDay } from "../types/travel";

defineProps<{
  days: WeatherDay[];
}>();

function weekday(date: string): string {
  const labels = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  return labels[dayjs(date).day()];
}

function weatherTone(weather: string | null): string {
  const value = weather ?? "";
  return value.includes("雷")
    ? "storm"
    : value.includes("雨")
      ? "rain"
      : "clear";
}

function temperature(value: number | null): string {
  return value === null ? "待确认" : `${value}°`;
}
</script>

<template>
  <section class="weather-strip" aria-labelledby="weather-title">
    <div class="weather-intro">
      <p class="section-kicker">旅行天气</p>
      <h2 id="weather-title">晴雨都在计划内</h2>
      <span>天气变化已纳入每日行程建议</span>
    </div>
    <div class="weather-days">
      <article
        v-for="day in days"
        :key="day.date"
        class="weather-day"
        :class="`weather-day--${weatherTone(day.day_weather)}`"
      >
        <div>
          <span>{{ weekday(day.date) }}</span>
          <small>{{ day.date.slice(5).replace("-", ".") }}</small>
        </div>
        <component
          :is="
            day.day_weather?.includes('雷')
              ? ThunderboltOutlined
              : CloudOutlined
          "
          class="weather-icon"
        />
        <div class="weather-temp">
          <strong>{{ temperature(day.max_temperature_c) }}</strong>
          <span>/ {{ temperature(day.min_temperature_c) }}</span>
        </div>
        <p>{{ day.day_weather ?? "天气待确认" }}</p>
      </article>
    </div>
  </section>
</template>
