import { defineStore } from "pinia";
import { ref } from "vue";
import {
  getCurrentUser,
  loginUser,
  logoutUser,
  registerUser,
  type AuthUser,
} from "../services/api";
import { useHistoryStore } from "./history";
import { usePlanStore } from "./plan";
import { usePlanningStore } from "./planning";

const ACTIVE_USER_STORAGE_KEY = "travel-active-user-id";
const USER_CACHE_KEYS = [
  "travel-current-plan",
  "travel-conversation-turns",
];
const USER_SESSION_KEYS = [
  "travel-planning-request",
  "travel-planning-task-id",
];

function clearPlanningCache(): void {
  USER_CACHE_KEYS.forEach((key) => localStorage.removeItem(key));
  USER_SESSION_KEYS.forEach((key) => sessionStorage.removeItem(key));
}

function clearPlanningState(): void {
  clearPlanningCache();
  const planningStore = usePlanningStore();
  planningStore.reset();
  planningStore.clearRequest();
  usePlanStore().clear();
  useHistoryStore().reset();
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<AuthUser | null>(null);
  const loading = ref(true);
  const submitting = ref(false);
  const errorMessage = ref("");

  function activateUser(nextUser: AuthUser): void {
    const previousUserId = localStorage.getItem(
      ACTIVE_USER_STORAGE_KEY,
    );
    if (previousUserId !== nextUser.user_id) {
      clearPlanningState();
    }
    localStorage.setItem(ACTIVE_USER_STORAGE_KEY, nextUser.user_id);
    user.value = nextUser;
  }

  function markSignedOut(): void {
    user.value = null;
    localStorage.removeItem(ACTIVE_USER_STORAGE_KEY);
    clearPlanningState();
  }

  async function bootstrap(): Promise<void> {
    loading.value = true;
    try {
      activateUser(await getCurrentUser());
    } catch {
      markSignedOut();
    } finally {
      loading.value = false;
    }
  }

  async function login(username: string, password: string): Promise<void> {
    submitting.value = true;
    errorMessage.value = "";
    try {
      activateUser(await loginUser(username, password));
    } catch (error) {
      errorMessage.value =
        error instanceof Error ? error.message : "登录失败，请稍后重试";
      throw error;
    } finally {
      submitting.value = false;
    }
  }

  async function register(
    username: string,
    password: string,
  ): Promise<void> {
    submitting.value = true;
    errorMessage.value = "";
    try {
      activateUser(await registerUser(username, password));
    } catch (error) {
      errorMessage.value =
        error instanceof Error ? error.message : "注册失败，请稍后重试";
      throw error;
    } finally {
      submitting.value = false;
    }
  }

  async function logout(): Promise<void> {
    try {
      await logoutUser();
    } catch {
      // A local sign-out must still succeed if the API is temporarily down.
    } finally {
      markSignedOut();
    }
  }

  return {
    user,
    loading,
    submitting,
    errorMessage,
    bootstrap,
    login,
    register,
    logout,
    markSignedOut,
  };
});
