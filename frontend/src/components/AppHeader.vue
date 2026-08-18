<script setup lang="ts">
import {
  CompassOutlined,
  HistoryOutlined,
  LogoutOutlined,
  PlusOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from "@ant-design/icons-vue";
import { useAuthStore } from "../stores/auth";
import { useHistoryStore } from "../stores/history";

const historyStore = useHistoryStore();
const authStore = useAuthStore();
</script>

<template>
  <header class="site-header">
    <div class="brand-button">
      <span class="brand-mark"><CompassOutlined /></span>
      <span class="brand-copy">
        <strong>途画</strong>
        <small>LANGGRAPH 旅行助手</small>
      </span>
    </div>

    <div class="header-actions">
      <button
        type="button"
        class="header-action-button"
        @click="historyStore.openDrawer"
      >
        <HistoryOutlined /> 历史规划
      </button>
      <button
        type="button"
        class="header-action-button header-action-button--primary"
        @click="historyStore.requestNewPlanning"
      >
        <PlusOutlined /> 新规划
      </button>
      <span class="header-harness">
        <ThunderboltOutlined /> LangGraph Harness
      </span>
      <span class="header-user">
        <UserOutlined /> {{ authStore.user?.username }}
      </span>
      <button
        type="button"
        class="header-action-button header-logout"
        @click="authStore.logout()"
      >
        <LogoutOutlined /> 退出
      </button>
    </div>
  </header>
</template>
