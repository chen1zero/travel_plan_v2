<script setup lang="ts">
import {
  CalendarOutlined,
  ClockCircleOutlined,
  SearchOutlined,
} from "@ant-design/icons-vue";
import { message } from "ant-design-vue";
import dayjs from "dayjs";
import { useHistoryStore } from "../stores/history";

const historyStore = useHistoryStore();

function formatDate(value: string): string {
  return dayjs(value).format("M月D日 HH:mm");
}

async function selectSession(sessionId: string): Promise<void> {
  try {
    await historyStore.selectSession(sessionId);
  } catch (error) {
    message.error(
      error instanceof Error ? error.message : "历史规划加载失败",
    );
  }
}
</script>

<template>
  <a-drawer
    v-model:open="historyStore.drawerOpen"
    placement="right"
    :width="440"
    :closable="true"
    root-class-name="history-drawer"
  >
    <template #title>
      <div class="history-drawer__title">
        <strong>历史规划</strong>
        <span>服务器已保存的完整对话与计划版本</span>
      </div>
    </template>

    <div class="history-search">
      <SearchOutlined />
      <input
        v-model="historyStore.query"
        type="search"
        placeholder="搜索城市、日期或追加要求"
        aria-label="搜索历史规划"
        @keyup.enter="historyStore.loadSessions(true)"
      />
      <button type="button" @click="historyStore.loadSessions(true)">
        搜索
      </button>
    </div>

    <a-spin v-if="historyStore.loading" class="history-loading" />
    <a-empty
      v-else-if="!historyStore.sessions.length"
      description="还没有可浏览的历史规划"
    />
    <div v-else class="history-list">
      <button
        v-for="session in historyStore.sessions"
        :key="session.session_id"
        type="button"
        class="history-card"
        :class="{
          'history-card--active':
            historyStore.selectedSessionId === session.session_id,
        }"
        @click="selectSession(session.session_id)"
      >
        <span class="history-card__topline">
          <strong>{{ session.title }}</strong>
          <i>V{{ session.current_revision }}</i>
        </span>
        <span class="history-card__meta">
          <CalendarOutlined />
          {{ session.start_date }}—{{ session.end_date }}
          <b>¥{{ session.budget_cny?.toLocaleString("zh-CN") }}</b>
        </span>
        <span v-if="session.latest_requirement" class="history-card__requirement">
          “{{ session.latest_requirement }}”
        </span>
        <span class="history-card__footer">
          <em>{{ session.revision_count }} 个版本</em>
          <small><ClockCircleOutlined /> {{ formatDate(session.updated_at) }}</small>
        </span>
      </button>

      <button
        v-if="historyStore.nextCursor"
        type="button"
        class="history-load-more"
        :disabled="historyStore.loadingMore"
        @click="historyStore.loadSessions(false)"
      >
        {{ historyStore.loadingMore ? "加载中…" : "加载更多" }}
      </button>
    </div>

    <a-alert
      v-if="historyStore.errorMessage"
      type="error"
      :message="historyStore.errorMessage"
      show-icon
    />
  </a-drawer>
</template>
