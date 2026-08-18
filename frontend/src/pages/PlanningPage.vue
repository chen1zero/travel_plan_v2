<script setup lang="ts">
import {
  CloseCircleOutlined,
} from "@ant-design/icons-vue";
import { onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import PlanningProgressPanel from "../components/PlanningProgressPanel.vue";
import { usePlanningStore } from "../stores/planning";

const router = useRouter();
const route = useRoute();
const store = usePlanningStore();

watch(
  () => store.taskId,
  async (taskId) => {
    if (
      taskId &&
      String(route.params.taskId || "") !== taskId
    ) {
      await router.replace({
        name: "planning",
        params: { taskId },
      });
    }
  },
);

async function startPlanning(forceNew = false): Promise<void> {
  const routeTaskId = String(route.params.taskId || "");
  const existingTaskId =
    !forceNew && routeTaskId && routeTaskId !== "new"
      ? routeTaskId
      : undefined;
  if (!existingTaskId && !store.request) {
    await router.replace("/");
    return;
  }
  try {
    const planId = await store.run(existingTaskId);
    await new Promise((resolve) => window.setTimeout(resolve, 480));
    await router.replace(`/plans/${planId}`);
  } catch {
    // The store exposes a user-facing error state.
  }
}

onMounted(startPlanning);
</script>

<template>
  <main class="planning-page">
    <section class="planning-heading">
      <p class="eyebrow">THINK · ACT · OBSERVE · PLAN</p>
      <h1>
        正在一步步推演你的
        <em>{{ store.request?.destination_city || "旅行" }}</em>
        之旅
      </h1>
      <p>
        从理解要求到查证信息，再到编排行程和路线，
        每一步思考摘要与实际行动都会清晰呈现。
      </p>
    </section>

    <section
      v-if="store.status !== 'failed'"
      class="planning-progress-section"
    >
      <PlanningProgressPanel />
    </section>

    <a-result
      v-else
      class="planning-error"
      title="这次规划没有完成"
      :sub-title="
        store.errorMessage || '服务连接暂时中断，请保留原需求后重试。'
      "
    >
      <template #icon><CloseCircleOutlined /></template>
      <template #extra>
        <a-button @click="router.push('/')">返回修改需求</a-button>
        <a-button type="primary" @click="startPlanning(true)">
          使用原需求重试
        </a-button>
      </template>
    </a-result>
  </main>
</template>
