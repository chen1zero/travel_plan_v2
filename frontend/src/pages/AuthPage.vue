<script setup lang="ts">
import {
  CompassOutlined,
  LockOutlined,
  UserOutlined,
} from "@ant-design/icons-vue";
import { computed, ref } from "vue";
import { useAuthStore } from "../stores/auth";

const authStore = useAuthStore();
const mode = ref<"login" | "register">("login");
const username = ref("");
const password = ref("");
const localError = ref("");

const title = computed(() =>
  mode.value === "login" ? "欢迎回来" : "创建账号",
);

function switchMode(): void {
  mode.value = mode.value === "login" ? "register" : "login";
  localError.value = "";
  authStore.errorMessage = "";
}

async function submit(): Promise<void> {
  localError.value = "";
  const normalizedUsername = username.value.trim();
  if (!/^[A-Za-z0-9_]{3,32}$/.test(normalizedUsername)) {
    localError.value = "用户名需为 3–32 位字母、数字或下划线";
    return;
  }
  if (password.value.length < 8) {
    localError.value = "密码至少需要 8 位";
    return;
  }
  try {
    if (mode.value === "login") {
      await authStore.login(normalizedUsername, password.value);
    } else {
      await authStore.register(normalizedUsername, password.value);
    }
  } catch {
    // The store exposes a user-facing error below the form.
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-card" aria-labelledby="auth-title">
      <div class="auth-brand-mark"><CompassOutlined /></div>
      <p class="auth-kicker">途画 · 智能旅行助手</p>
      <h1 id="auth-title">{{ title }}</h1>
      <p class="auth-intro">
        {{
          mode === "login"
            ? "登录后继续查看和修订你的旅行规划"
            : "一个账号即可保存全部规划历史"
        }}
      </p>

      <form class="auth-form" @submit.prevent="submit">
        <label>
          <span>用户名</span>
          <div class="auth-input-shell">
            <UserOutlined />
            <input
              v-model="username"
              name="username"
              autocomplete="username"
              maxlength="32"
              placeholder="字母、数字或下划线"
            />
          </div>
        </label>
        <label>
          <span>密码</span>
          <div class="auth-input-shell">
            <LockOutlined />
            <input
              v-model="password"
              name="password"
              type="password"
              :autocomplete="
                mode === 'login' ? 'current-password' : 'new-password'
              "
              maxlength="128"
              placeholder="至少 8 位"
            />
          </div>
        </label>

        <p
          v-if="localError || authStore.errorMessage"
          class="auth-error"
          role="alert"
        >
          {{ localError || authStore.errorMessage }}
        </p>
        <button
          type="submit"
          class="auth-submit"
          :disabled="authStore.submitting"
        >
          {{
            authStore.submitting
              ? "请稍候…"
              : mode === "login"
                ? "登录"
                : "注册并登录"
          }}
        </button>
      </form>

      <button type="button" class="auth-switch" @click="switchMode">
        {{
          mode === "login"
            ? "还没有账号？立即注册"
            : "已有账号？返回登录"
        }}
      </button>
    </section>
  </main>
</template>
