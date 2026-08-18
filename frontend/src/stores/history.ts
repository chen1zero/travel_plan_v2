import { defineStore } from "pinia";
import { ref } from "vue";
import {
  forkPlanningSession,
  getPlanningSession,
  getPlanningSessionTurns,
  listPlanningSessions,
  type PlanningSession,
  type PlanningSessionSummary,
  type PlanningSessionTurn,
} from "../services/api";
import type { PlanEnvelope } from "../types/travel";

export const useHistoryStore = defineStore("history", () => {
  const drawerOpen = ref(false);
  const sessions = ref<PlanningSessionSummary[]>([]);
  const nextCursor = ref<string | undefined>();
  const loading = ref(false);
  const loadingMore = ref(false);
  const errorMessage = ref("");
  const query = ref("");
  const selectedSessionId = ref("");
  const selectedSession = ref<PlanningSession | null>(null);
  const selectedTurns = ref<PlanningSessionTurn[]>([]);
  const selectionToken = ref(0);
  const newPlanningToken = ref(0);

  async function loadSessions(reset = true): Promise<void> {
    if (reset) {
      loading.value = true;
      nextCursor.value = undefined;
    } else {
      if (!nextCursor.value || loadingMore.value) return;
      loadingMore.value = true;
    }
    errorMessage.value = "";
    try {
      const result = await listPlanningSessions({
        limit: 20,
        query: query.value.trim() || undefined,
        cursor: reset ? undefined : nextCursor.value,
      });
      sessions.value = reset
        ? result.items
        : [...sessions.value, ...result.items];
      nextCursor.value = result.next_cursor;
    } catch (error) {
      errorMessage.value =
        error instanceof Error ? error.message : "历史规划加载失败";
    } finally {
      loading.value = false;
      loadingMore.value = false;
    }
  }

  function openDrawer(): void {
    drawerOpen.value = true;
    void loadSessions(true);
  }

  async function selectSession(sessionId: string): Promise<void> {
    const [session, turns] = await Promise.all([
      getPlanningSession(sessionId),
      getPlanningSessionTurns(sessionId),
    ]);
    selectedSessionId.value = sessionId;
    selectedSession.value = session;
    selectedTurns.value = turns;
    selectionToken.value += 1;
    drawerOpen.value = false;
  }

  function requestNewPlanning(): void {
    newPlanningToken.value += 1;
    drawerOpen.value = false;
  }

  function clearSelection(): void {
    selectedSessionId.value = "";
    selectedSession.value = null;
    selectedTurns.value = [];
  }

  function reset(): void {
    drawerOpen.value = false;
    sessions.value = [];
    nextCursor.value = undefined;
    loading.value = false;
    loadingMore.value = false;
    errorMessage.value = "";
    query.value = "";
    selectedSessionId.value = "";
    selectedSession.value = null;
    selectedTurns.value = [];
  }

  async function fork(
    sessionId: string,
    planId: string,
  ): Promise<PlanEnvelope> {
    const envelope = await forkPlanningSession(sessionId, planId);
    await loadSessions(true);
    await selectSession(envelope.session_id);
    return envelope;
  }

  return {
    drawerOpen,
    sessions,
    nextCursor,
    loading,
    loadingMore,
    errorMessage,
    query,
    selectedSessionId,
    selectedSession,
    selectedTurns,
    selectionToken,
    newPlanningToken,
    loadSessions,
    openDrawer,
    selectSession,
    requestNewPlanning,
    clearSelection,
    reset,
    fork,
  };
});
