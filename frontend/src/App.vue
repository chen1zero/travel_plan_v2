<script setup lang="ts">
import { CompassOutlined } from "@ant-design/icons-vue";
import { onBeforeUnmount, onMounted } from "vue";
import AppHeader from "./components/AppHeader.vue";
import HistoryDrawer from "./components/HistoryDrawer.vue";
import AuthPage from "./pages/AuthPage.vue";
import { useAuthStore } from "./stores/auth";

const authStore = useAuthStore();

function handleExpiredSession(): void {
  authStore.markSignedOut();
}

onMounted(() => {
  window.addEventListener("travel-auth-expired", handleExpiredSession);
  void authStore.bootstrap();
});

onBeforeUnmount(() => {
  window.removeEventListener("travel-auth-expired", handleExpiredSession);
});
</script>

<template>
  <a-config-provider
    :theme="{
      token: {
        colorPrimary: '#be5735',
        colorInfo: '#2f6f64',
        colorSuccess: '#2f7d61',
        colorWarning: '#d88c38',
        borderRadius: 12,
        fontFamily:
          'Inter, PingFang SC, Microsoft YaHei, system-ui, sans-serif',
      },
    }"
  >
    <div v-if="authStore.loading" class="auth-loading">
      <span class="brand-mark"><CompassOutlined /></span>
      <p>正在恢复登录状态…</p>
    </div>
    <AuthPage v-else-if="!authStore.user" />
    <div v-else class="app-shell">
      <AppHeader />
      <HistoryDrawer />
      <router-view />
    </div>
  </a-config-provider>
</template>
