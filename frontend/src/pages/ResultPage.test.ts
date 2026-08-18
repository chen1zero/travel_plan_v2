import Antd from "ant-design-vue";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import {
  createMemoryHistory,
  createRouter,
} from "vue-router";
import { beforeEach, describe, expect, it } from "vitest";
import { usePlanStore } from "../stores/plan";
import { usePlanningStore } from "../stores/planning";
import ResultPage from "./ResultPage.vue";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: memoryStorage(),
});
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  value: memoryStorage(),
});

describe("ResultPage plan loading", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("loads the route plan instead of keeping a cached one-day plan", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const store = usePlanStore();
    const planningStore = usePlanningStore();
    planningStore.status = "completed";
    planningStore.currentStageIndex = 3;
    planningStore.completedStageIndex = 3;
    planningStore.logs.push({
      id: "completed-log",
      timestamp: "10:30:00",
      stage: "planner",
      message: "行程与路线规划已完成",
      detail: "推荐方式已经校验",
    });
    await store.load("old-plan");
    store.envelope!.plan_id = "old-plan";
    store.envelope!.plan.daily_itinerary =
      store.envelope!.plan.daily_itinerary.slice(0, 1);

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/plans/:planId",
          component: ResultPage,
        },
        {
          path: "/",
          component: { template: "<div>home</div>" },
        },
      ],
    });
    await router.push("/plans/new-three-day-plan");
    await router.isReady();

    const wrapper = mount(ResultPage, {
      global: {
        plugins: [pinia, router, Antd],
      },
    });
    await flushPromises();

    expect(store.visibleDays).toHaveLength(3);
    expect(wrapper.findAll(".day-tab-label")).toHaveLength(3);
    expect(wrapper.text()).toContain("DAY 01");
    expect(wrapper.text()).toContain("DAY 02");
    expect(wrapper.text()).toContain("DAY 03");
    expect(wrapper.text()).toContain("行程与路线规划已完成");
    const processSection = wrapper.get(
      ".result-process-section",
    );
    const resultHero = wrapper.get(".result-hero");
    expect(
      processSection.element.compareDocumentPosition(
        resultHero.element,
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
  });
});
