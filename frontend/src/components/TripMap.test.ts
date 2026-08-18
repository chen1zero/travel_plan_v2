import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type {
  RouteMode,
  RouteSegment,
} from "../types/travel";
import {
  ROUTE_LABEL_ZOOMS,
  routeLabelLayerOptions,
} from "../utils/map-label-layer";
import TripMap from "./TripMap.vue";

const routeMode = (
  distance: number,
  duration: number,
): RouteMode => ({
  available: true,
  distance_km: distance,
  duration_minutes: duration,
  error: null,
});

const route = (
  sequence: number,
  origin: string,
  destination: string,
  distance: number,
  duration: number,
): RouteSegment => ({
  route_id: `route-${sequence}`,
  sequence,
  origin: { name: origin, address: "", city: "北京" },
  destination: {
    name: destination,
    address: "",
    city: "北京",
  },
  walking: routeMode(distance, duration + 20),
  driving: routeMode(distance, duration + 5),
  public_transit: {
    ...routeMode(distance, duration),
    transit_type: "bus",
    line_names: ["测试公交"],
  },
  recommended_mode: "public_transit",
  recommendation_reason: "测试推荐",
});

describe("TripMap route labels", () => {
  it("keeps every real-map route label visible without collision filtering", () => {
    expect(routeLabelLayerOptions()).toEqual({
      collision: false,
      allowCollision: false,
      zooms: [3, 20],
      zIndex: 240,
    });
    expect(ROUTE_LABEL_ZOOMS).toEqual([3, 20]);
  });

  it("shows every valid attraction and connects hotel to each stop", () => {
    const wrapper = mount(TripMap, {
      props: {
        dayLabel: "第 1 天",
        locations: [
          {
            id: "hotel",
            name: "测试酒店",
            address: "酒店地址",
            longitude: 116.4,
            latitude: 39.9,
            kind: "hotel",
          },
          {
            id: "place-1",
            name: "景点一",
            address: "地址一",
            longitude: 116.41,
            latitude: 39.91,
            kind: "attraction",
            order: 1,
          },
          {
            id: "place-2",
            name: "景点二",
            address: "地址二",
            longitude: 116.42,
            latitude: 39.92,
            kind: "attraction",
            order: 2,
          },
        ],
        routes: [
          route(1, "测试酒店", "景点一", 1.5, 15),
          route(2, "景点一", "景点二", 2.3, 22),
        ],
      },
    });

    expect(wrapper.findAll(".fallback-marker")).toHaveLength(2);
    expect(wrapper.findAll(".hotel-marker")).toHaveLength(1);
    expect(wrapper.findAll(".fallback-route")).toHaveLength(2);
    expect(wrapper.findAll(".fallback-route-info")).toHaveLength(2);
    expect(wrapper.find(".fallback-name--right").exists()).toBe(true);
    expect(wrapper.find(".fallback-name--top").exists()).toBe(true);
    expect(wrapper.findAll(".map-actions button")).toHaveLength(3);
    expect(
      wrapper.get('button[aria-label="缩小地图"]').attributes(
        "disabled",
      ),
    ).toBeDefined();
    expect(
      wrapper.get('button[aria-label="放大地图"]').attributes(
        "disabled",
      ),
    ).toBeDefined();
    expect(wrapper.text()).toContain("测试酒店");
    expect(wrapper.text()).toContain("景点一");
    expect(wrapper.text()).toContain("景点二");
    expect(wrapper.text()).toContain("1.5 公里 · 公交 15 分钟");
    expect(wrapper.text()).toContain("2.3 公里 · 公交 22 分钟");
  });

  it("does not draw markers or routes for invalid coordinates", () => {
    const wrapper = mount(TripMap, {
      props: {
        dayLabel: "第 1 天",
        locations: [],
      },
    });

    expect(wrapper.findAll(".fallback-marker")).toHaveLength(0);
    expect(wrapper.findAll(".fallback-route")).toHaveLength(0);
    expect(wrapper.text()).toContain("暂未获得景点坐标");
  });

  it("shows a live Amap placeholder while the itinerary is planning", () => {
    const wrapper = mount(TripMap, {
      props: {
        dayLabel: "景点筛选完成后自动绘制路线",
        destinationCity: "杭州",
        loading: true,
        locations: [],
      },
    });

    expect(wrapper.classes()).toContain("trip-map--planning");
    expect(wrapper.get(".map-planning-overlay").text()).toContain(
      "正在定位 杭州",
    );
    expect(wrapper.text()).toContain("分段距离");
    expect(wrapper.text()).not.toContain("暂未获得景点坐标");
  });
});
