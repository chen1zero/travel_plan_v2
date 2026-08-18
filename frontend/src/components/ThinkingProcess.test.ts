import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type { PlanningLog } from "../types/travel";
import ThinkingProcess from "./ThinkingProcess.vue";

const logs: PlanningLog[] = [
  {
    id: "thought",
    timestamp: "10:00:00",
    stage: "attraction",
    message: "提炼景点搜索方向",
    kind: "thought",
  },
  {
    id: "action",
    timestamp: "10:00:01",
    stage: "attraction",
    message: "检索历史文化地点",
    kind: "action",
  },
  {
    id: "observation",
    timestamp: "10:00:02",
    stage: "attraction",
    message: "获得候选地点",
    kind: "observation",
  },
  {
    id: "conclusion",
    timestamp: "10:00:03",
    stage: "attraction",
    message: "形成景点结论",
    kind: "conclusion",
  },
];

describe("ThinkingProcess", () => {
  it("shows a foldable, sanitized execution summary", async () => {
    const wrapper = mount(ThinkingProcess, {
      props: {
        logs,
        mode: "initial",
        status: "completed",
        request: {
          destination_city: "北京",
          start_date: "2026-08-01",
          end_date: "2026-08-03",
          preferences: ["历史文化", "人文街区"],
          budget_cny: 5000,
          accommodation_type: "经济型",
          additional_requirements: "每天九点后出发",
        },
      },
    });

    expect(wrapper.text()).toContain("明确北京旅行规划要求");
    expect(wrapper.text()).toContain("用户希望规划 北京 3 天旅行");
    expect(wrapper.text()).toContain("搜索并筛选候选景点");
    expect(wrapper.text()).toContain("获得候选地点");
    expect(wrapper.text()).toContain("形成景点结论");
    expect(wrapper.text()).toContain("不包含模型内部原始推理");

    await wrapper.get("button.thinking-requirement-toggle").trigger("click");
    expect(
      wrapper
        .get("button.thinking-requirement-toggle")
        .attributes("aria-expanded"),
    )
      .toBe("false");
    expect(
      wrapper.get(".thinking-requirement-body").attributes("style"),
    ).toContain(
      "display: none",
    );

    await wrapper.get("button.thinking-action-toggle").trigger("click");
    expect(
      wrapper.get("button.thinking-action-toggle").attributes("aria-expanded"),
    ).toBe("true");
    expect(wrapper.text()).toContain("检索历史文化地点");
  });

  it("shows a focused context-inheriting trace for a follow-up", () => {
    const wrapper = mount(ThinkingProcess, {
      props: {
        logs: [
          {
            id: "change-analysis",
            timestamp: "10:01:59",
            stage: "harness",
            message: "将重跑：住宿；复用上一版：景点、天气",
            detail: "change.analysis",
            kind: "thought",
          },
          {
            id: "attraction-skipped",
            timestamp: "10:02:00",
            stage: "attraction",
            message: "景点条件未变化，复用上一版研究结果",
            detail: "node.skipped",
            kind: "conclusion",
          },
          {
            id: "revision-thought",
            timestamp: "10:02:01",
            stage: "planner",
            message: "读取上一版并定位受影响的第二天安排",
            kind: "thought",
          },
          {
            id: "revision-result",
            timestamp: "10:02:01",
            stage: "planner",
            message: "未修改内容已从上一版保留",
            kind: "conclusion",
          },
        ],
        mode: "refinement",
        status: "completed",
        request: {
          destination_city: "北京",
          start_date: "2026-08-01",
          end_date: "2026-08-03",
          preferences: ["历史文化"],
          budget_cny: 5000,
          accommodation_type: "经济型",
          additional_requirements: "第二天减少一个景点",
        },
      },
    });

    expect(wrapper.text()).toContain("在上一版基础上调整北京行程");
    expect(wrapper.text()).toContain("已载入上一版");
    expect(wrapper.text()).toContain("只重跑受影响的景点、天气或住宿节点");
    expect(wrapper.text()).toContain("将重跑：住宿；复用上一版：景点、天气");
    expect(wrapper.text()).toContain("景点条件未变化，复用上一版研究结果");
    expect(wrapper.text()).toContain("合并历史结果并修订完整行程");
    expect(wrapper.text()).not.toContain("搜索并筛选候选景点");
    expect(wrapper.text()).not.toContain("查询旅行日期天气");
  });
});
