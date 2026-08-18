import Antd from "ant-design-vue";
import {
  createPinia,
  setActivePinia,
} from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDemoPlan } from "../mocks/demo-plan";
import { usePlanStore } from "../stores/plan";
import { usePlanningStore } from "../stores/planning";
import HomePage from "./HomePage.vue";

vi.mock("../services/api", async (importOriginal) => {
  const original = await importOriginal<
    typeof import("../services/api")
  >();
  return {
    ...original,
    listPlanningSessions: vi.fn().mockResolvedValue({ items: [] }),
    getPlanningSession: vi.fn().mockImplementation(
      async (sessionId: string) => ({
        session_id: sessionId,
        current_plan_id: undefined,
        created_at: "2099-08-01T00:00:00Z",
        updated_at: "2099-08-01T00:00:00Z",
        plans: [],
      }),
    ),
  };
});

describe("HomePage single-page planning conversation", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    });
    sessionStorage.clear();
    localStorage.clear();
    document.body.innerHTML = "";
  });

  it("renders the first result and a follow-up result without navigation", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const planningStore = usePlanningStore();
    const planStore = usePlanStore();

    vi.spyOn(planningStore, "run").mockImplementation(async () => {
      planningStore.logs.push({
        id: crypto.randomUUID(),
        timestamp: "10:00:00",
        stage: "planner",
        message: "已完成行程合成",
        kind: "conclusion",
      });
      planningStore.status = "completed";
      return "plan_demo_beijing";
    });
    vi.spyOn(planStore, "load").mockImplementation(
      async (_planId, request) => {
        const next = createDemoPlan(request ?? undefined);
        if (request?.previous_plan_id) {
          next.plan_id = "plan_demo_beijing_v2";
          next.previous_plan_id = request.previous_plan_id;
          next.session_id = request.session_id || next.session_id;
          next.revision = 2;
        }
        planStore.envelope = next;
      },
    );

    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });

    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();

    expect(wrapper.findAll(".conversation-turn")).toHaveLength(1);
    expect(wrapper.text()).toContain("北京 3 天旅行提案");
    expect(wrapper.find(".inline-map-section").exists()).toBe(true);
    expect(wrapper.findAll(".inline-map-section__heading button")).toHaveLength(
      3,
    );
    expect(wrapper.text()).toContain("按天查看景点连线与分段距离");
    expect(wrapper.text()).toContain("明确北京旅行规划要求");
    expect(wrapper.text()).toContain("已完成");
    expect(
      sessionStorage.getItem("travel-planning-request"),
    ).not.toBeNull();

    await wrapper
      .get(".conversation-input-shell textarea")
      .setValue("第二天减少一个景点，午餐安排本地菜");
    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();

    expect(wrapper.findAll(".conversation-turn")).toHaveLength(2);
    expect(wrapper.text()).toContain("第 2 轮增量修订");
    expect(wrapper.text()).toContain("第二天减少一个景点");
    expect(wrapper.text()).toContain("在上一版基础上调整北京行程");
    expect(
      planningStore.request?.additional_requirements,
    ).toBe("第二天减少一个景点，午餐安排本地菜");
    expect(planningStore.request).toEqual(
      expect.objectContaining({
        session_id: "session_demo_beijing",
        previous_plan_id: "plan_demo_beijing",
        additional_requirements: "第二天减少一个景点，午餐安排本地菜",
      }),
    );
    expect(planningStore.run).toHaveBeenCalledTimes(2);
    expect(planStore.load).toHaveBeenCalledTimes(2);
    expect(planStore.envelope?.revision).toBe(2);
    expect(wrapper.findAll(".inline-map-section")).toHaveLength(1);
  });

  it("retries a failed turn with the exact saved request", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const planningStore = usePlanningStore();
    const planStore = usePlanStore();
    vi.spyOn(planningStore, "run")
      .mockRejectedValueOnce(new Error("外部服务暂时不可用"))
      .mockResolvedValueOnce("retry-plan");
    vi.spyOn(planStore, "load").mockImplementation(
      async (_planId, request) => {
        const next = createDemoPlan(request ?? undefined);
        next.plan_id = "retry-plan";
        planStore.envelope = next;
      },
    );

    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });

    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();
    const failedRequest = JSON.parse(
      JSON.stringify(planningStore.request),
    );

    expect(wrapper.text()).toContain("外部服务暂时不可用");
    await wrapper.get(".conversation-error button").trigger("click");
    await flushPromises();

    expect(planningStore.run).toHaveBeenCalledTimes(2);
    expect(planningStore.request).toEqual(failedRequest);
    expect(wrapper.findAll(".conversation-turn")).toHaveLength(2);
    expect(wrapper.text()).toContain("北京 3 天旅行提案");
  });

  it("continues planning when the destination field changes without extra text", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const planningStore = usePlanningStore();
    const planStore = usePlanStore();

    vi.spyOn(planningStore, "run").mockResolvedValue("destination-plan");
    vi.spyOn(planStore, "load").mockImplementation(
      async (_planId, request) => {
        const next = createDemoPlan(request ?? undefined);
        if (request?.previous_plan_id) {
          next.plan_id = "destination-plan-v2";
          next.previous_plan_id = request.previous_plan_id;
          next.session_id = request.session_id || next.session_id;
          next.revision = 2;
        }
        planStore.envelope = next;
      },
    );

    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });

    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();
    await wrapper.get("button.conversation-context").trigger("click");
    await wrapper
      .get(".conversation-constraints .ant-select-selection-search-input")
      .setValue("上海");
    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();

    expect(planningStore.run).toHaveBeenCalledTimes(2);
    expect(planningStore.request).toEqual(
      expect.objectContaining({
        destination_city: "上海",
        previous_plan_id: "plan_demo_beijing",
        additional_requirements: "将目的地调整为上海",
      }),
    );
    expect(wrapper.findAll(".conversation-turn")).toHaveLength(2);
    expect(wrapper.text()).toContain("将目的地调整为上海");
  });

  it("keeps the Amap panel visible while a plan is running", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const planningStore = usePlanningStore();
    const planStore = usePlanStore();
    let completePlanning: ((planId: string) => void) | undefined;
    vi.spyOn(planningStore, "run").mockImplementation(
      () =>
        new Promise<string>((resolve) => {
          completePlanning = resolve;
        }),
    );
    vi.spyOn(planStore, "load").mockImplementation(
      async (_planId, request) => {
        planStore.envelope = createDemoPlan(request ?? undefined);
      },
    );
    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });

    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();

    expect(wrapper.find(".conversation-planning-map").exists()).toBe(true);
    expect(wrapper.text()).toContain("正在定位 北京");
    expect(wrapper.find(".inline-map-section").exists()).toBe(false);

    completePlanning?.("plan_demo_beijing");
    await flushPromises();

    expect(wrapper.find(".conversation-planning-map").exists()).toBe(false);
    expect(wrapper.find(".inline-map-section").exists()).toBe(true);
  });

  it("edits the latest itinerary on the home page and records a new version", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const planningStore = usePlanningStore();
    const planStore = usePlanStore();
    vi.spyOn(planningStore, "run").mockResolvedValue("manual-source-plan");
    vi.spyOn(planStore, "load").mockImplementation(
      async (_planId, request) => {
        const next = createDemoPlan(request ?? undefined);
        next.plan_id = "manual-source-plan";
        planStore.setEnvelope(next);
      },
    );
    vi.spyOn(planStore, "saveEdits").mockImplementation(async () => {
      const previous = planStore.envelope!;
      const next = JSON.parse(JSON.stringify(previous));
      next.plan_id = "manual-saved-plan";
      next.previous_plan_id = previous.plan_id;
      next.revision = previous.revision + 1;
      next.source_type = "manual_edit";
      planStore.setEnvelope(next);
      planStore.cancelEdit();
    });

    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });
    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();

    await wrapper.get(".conversation-manual-edit-open").trigger("click");
    expect(wrapper.find(".conversation-manual-editor").exists()).toBe(true);
    expect(wrapper.text()).toContain("拖动、上移、下移或删除景点");

    await wrapper
      .get(".conversation-manual-editor__save")
      .trigger("click");
    await flushPromises();

    expect(planStore.saveEdits).toHaveBeenCalledTimes(1);
    expect(planStore.envelope?.plan_id).toBe("manual-saved-plan");
    expect(wrapper.findAll(".conversation-turn")).toHaveLength(2);
    expect(wrapper.text()).toContain("手动调整行程顺序");
    expect(wrapper.text()).toContain("已创建不可变的第 2 版计划");
  });

  it("restores the saved plan context after a page reload", async () => {
    const request = {
      destination_city: "北京",
      start_date: "2099-08-01",
      end_date: "2099-08-03",
      preferences: ["历史文化", "人文街区"],
      budget_cny: 5000,
      accommodation_type: "经济型" as const,
      additional_requirements: "每天九点后出发",
    };
    const saved = createDemoPlan(request);
    saved.revision = 3;
    localStorage.setItem(
      "travel-current-plan",
      JSON.stringify(saved),
    );
    sessionStorage.setItem(
      "travel-planning-request",
      JSON.stringify(request),
    );

    const pinia = createPinia();
    setActivePinia(pinia);
    const planningStore = usePlanningStore();
    const planStore = usePlanStore();
    vi.spyOn(planningStore, "run").mockResolvedValue(
      "restored-follow-up-plan",
    );
    vi.spyOn(planStore, "load").mockImplementation(
      async (_planId, followUpRequest) => {
        const next = createDemoPlan(followUpRequest ?? undefined);
        next.plan_id = "restored-follow-up-plan";
        next.previous_plan_id = saved.plan_id;
        next.session_id = saved.session_id;
        next.revision = 4;
        planStore.envelope = next;
      },
    );

    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });

    expect(wrapper.findAll(".conversation-turn")).toHaveLength(1);
    expect(wrapper.text()).toContain("已从本地恢复第 3 版计划");
    expect(wrapper.text()).toContain("上下文已恢复，可以继续补充要求");

    await wrapper
      .get(".conversation-input-shell textarea")
      .setValue("第三天改成轻松的半日行程");
    await wrapper.get("button.conversation-send").trigger("click");
    await flushPromises();

    expect(planningStore.run).toHaveBeenCalledTimes(1);
    expect(planningStore.request).toEqual(
      expect.objectContaining({
        destination_city: "北京",
        session_id: saved.session_id,
        previous_plan_id: saved.plan_id,
        additional_requirements: "第三天改成轻松的半日行程",
      }),
    );
    expect(wrapper.findAll(".conversation-turn")).toHaveLength(2);
  });

  it("restores every completed turn and stops forcing the reader down", async () => {
    const initialRequest = {
      destination_city: "北京",
      start_date: "2099-08-01",
      end_date: "2099-08-02",
      preferences: ["历史文化"],
      budget_cny: 5000,
      accommodation_type: "经济型" as const,
      additional_requirements: "请规划北京两日游",
    };
    const firstPlan = createDemoPlan(initialRequest);
    const followUpRequest = {
      ...initialRequest,
      additional_requirements: "第二天换一个景点",
      session_id: firstPlan.session_id,
      previous_plan_id: firstPlan.plan_id,
    };
    const secondPlan = createDemoPlan(followUpRequest);
    secondPlan.plan_id = "plan_demo_beijing_v2";
    secondPlan.previous_plan_id = firstPlan.plan_id;
    secondPlan.session_id = firstPlan.session_id;
    secondPlan.revision = 2;
    localStorage.setItem(
      "travel-current-plan",
      JSON.stringify(secondPlan),
    );
    sessionStorage.setItem(
      "travel-planning-request",
      JSON.stringify(followUpRequest),
    );
    localStorage.setItem(
      "travel-conversation-turns",
      JSON.stringify([
        {
          id: "turn-1",
          userText: "请规划北京两日游",
          request: initialRequest,
          mode: "initial",
          status: "completed",
          logs: [],
          envelope: firstPlan,
        },
        {
          id: "turn-2",
          userText: "第二天换一个景点",
          request: followUpRequest,
          mode: "refinement",
          status: "completed",
          logs: [],
          envelope: secondPlan,
        },
      ]),
    );

    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });

    expect(wrapper.findAll(".conversation-turn")).toHaveLength(2);
    expect(wrapper.text()).toContain("请规划北京两日游");
    expect(wrapper.text()).toContain("第二天换一个景点");

    Object.defineProperty(document.documentElement, "scrollHeight", {
      configurable: true,
      value: 3000,
    });
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 800,
    });
    Object.defineProperty(window, "scrollY", {
      configurable: true,
      value: 200,
    });
    window.dispatchEvent(new Event("scroll"));
    await flushPromises();

    expect(wrapper.find(".conversation-jump-latest").exists()).toBe(true);
  });

  it("replaces a cached plan with the latest session revision", async () => {
    const request = {
      destination_city: "杭州",
      start_date: "2099-08-13",
      end_date: "2099-08-14",
      preferences: ["历史文化", "人文街区"],
      budget_cny: 5000,
      accommodation_type: "舒适型" as const,
      additional_requirements: "第二天安排一个夜景景点",
    };
    const cachedPlan = createDemoPlan(request);
    cachedPlan.revision = 2;
    const latestPlan = createDemoPlan(request);
    latestPlan.session_id = cachedPlan.session_id;
    latestPlan.previous_plan_id = cachedPlan.plan_id;
    latestPlan.plan_id = "plan_hangzhou_v3";
    latestPlan.revision = 3;
    localStorage.setItem(
      "travel-current-plan",
      JSON.stringify(cachedPlan),
    );
    sessionStorage.setItem(
      "travel-planning-request",
      JSON.stringify(request),
    );

    const pinia = createPinia();
    setActivePinia(pinia);
    const planStore = usePlanStore();
    vi.spyOn(planStore, "syncLatestSession").mockImplementation(
      async () => {
        planStore.setEnvelope(latestPlan);
        return true;
      },
    );
    const wrapper = mount(HomePage, {
      attachTo: document.body,
      global: { plugins: [pinia, Antd] },
    });
    await flushPromises();

    expect(planStore.envelope?.plan_id).toBe("plan_hangzhou_v3");
    expect(wrapper.text()).toContain("已同步服务器最新第 3 版计划");
  });
});
