<script setup lang="ts">
import { computed } from "vue";
import type { TravelPlan } from "../types/travel";

const props = defineProps<{
  budget: TravelPlan["budget_summary"];
}>();

const items = computed(() => [
  {
    label: "交通",
    value: props.budget.breakdown.transport,
    color: "#2f6f64",
  },
  {
    label: "门票",
    value: props.budget.breakdown.tickets,
    color: "#be5735",
  },
  {
    label: "餐饮",
    value: props.budget.breakdown.food,
    color: "#d89b3d",
  },
  {
    label: "酒店",
    value: props.budget.breakdown.hotel,
    color: "#6c6f9b",
  },
]);

const knownTotal = computed(() =>
  items.value.reduce((sum, item) => sum + (item.value ?? 0), 0),
);

function width(value: number | null): string {
  if (!value || knownTotal.value === 0) return "0%";
  return `${Math.max(7, (value / knownTotal.value) * 100)}%`;
}

function money(value: number | null): string {
  return value === null
    ? "待确认"
    : `¥${value.toLocaleString("zh-CN")}`;
}
</script>

<template>
  <section class="budget-panel">
    <header>
      <div>
        <p class="section-kicker">预算明细</p>
        <h2>花得明白，留有余地</h2>
      </div>
      <div class="budget-total">
        <span>总预算</span>
        <strong>{{ money(budget.total_budget) }}</strong>
      </div>
    </header>

    <div class="budget-summary">
      <div>
        <span>当前估算</span>
        <b>{{ money(budget.estimated_total) }}</b>
      </div>
      <div>
        <span>预计剩余</span>
        <b class="remaining">{{ money(budget.remaining) }}</b>
      </div>
    </div>

    <div class="budget-bar" aria-label="预算类别占比">
      <span
        v-for="item in items"
        :key="item.label"
        :style="{
          width: width(item.value),
          backgroundColor: item.color,
        }"
      />
    </div>

    <div class="budget-legend">
      <div v-for="item in items" :key="item.label">
        <i :style="{ backgroundColor: item.color }" />
        <span>{{ item.label }}</span>
        <b>{{ money(item.value) }}</b>
      </div>
    </div>

    <p
      v-for="note in budget.notes"
      :key="note"
      class="budget-note"
    >
      {{ note }}
    </p>
  </section>
</template>
