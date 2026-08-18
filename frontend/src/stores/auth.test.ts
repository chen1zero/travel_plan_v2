import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDemoPlan } from "../mocks/demo-plan";
import { useAuthStore } from "./auth";
import { useHistoryStore } from "./history";
import { usePlanStore } from "./plan";
import { usePlanningStore } from "./planning";

describe("auth store", () => {
  beforeEach(() => {
    const createStorage = () => {
      const values = new Map<string, string>();
      return {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
      };
    };
    vi.stubGlobal("localStorage", createStorage());
    vi.stubGlobal("sessionStorage", createStorage());
    setActivePinia(createPinia());
  });

  it("clears persisted and in-memory planning data on sign-out", () => {
    const authStore = useAuthStore();
    const planStore = usePlanStore();
    const planningStore = usePlanningStore();
    const historyStore = useHistoryStore();
    const request = {
      destination_city: "北京",
      start_date: "2026-08-01",
      end_date: "2026-08-03",
      preferences: ["历史文化"],
      budget_cny: 5000,
      accommodation_type: "经济型" as const,
    };

    planStore.setEnvelope(createDemoPlan(request));
    planningStore.setRequest(request);
    historyStore.errorMessage = "old-user-data";
    authStore.user = {
      user_id: "user_old",
      username: "old_user",
      created_at: "2026-08-17T00:00:00Z",
    };

    authStore.markSignedOut();

    expect(authStore.user).toBeNull();
    expect(planStore.envelope).toBeNull();
    expect(planningStore.request).toBeNull();
    expect(historyStore.errorMessage).toBe("");
    expect(localStorage.getItem("travel-current-plan")).toBeNull();
    expect(sessionStorage.getItem("travel-planning-request")).toBeNull();
  });
});
