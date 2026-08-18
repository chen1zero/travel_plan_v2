import { describe, expect, it } from "vitest";
import type { RouteMode, RouteSegment } from "../types/travel";
import { recommendedRouteDisplay } from "./route-display";

function mode(
  distance: number | null,
  minutes: number,
  extra: Partial<RouteMode> = {},
): RouteMode {
  return {
    available: true,
    distance_km: distance,
    duration_minutes: minutes,
    error: null,
    ...extra,
  };
}

function route(
  recommendedMode: RouteSegment["recommended_mode"],
): RouteSegment {
  return {
    route_id: "route-1",
    sequence: 1,
    origin: { name: "起点", address: "", city: "北京" },
    destination: { name: "终点", address: "", city: "北京" },
    walking: mode(1.22, 17),
    driving: mode(1.89, 10),
    public_transit: mode(null, 17, {
      walking_distance_km: 0.56,
      transfer_count: 1,
      transit_type: "subway",
      line_names: ["地铁1号线"],
    }),
    recommended_mode: recommendedMode,
    recommendation_reason: "测试推荐",
  };
}

describe("recommendedRouteDisplay", () => {
  it("shows the selected walking route metrics", () => {
    const display = recommendedRouteDisplay(route("walking"));

    expect(display.compactLabel).toBe(
      "1.22 公里 · 步行 17 分钟",
    );
  });

  it("labels transit access walking instead of transit distance", () => {
    const display = recommendedRouteDisplay(
      route("public_transit"),
    );

    expect(display.distanceLabel).toBe("接驳步行 0.56 公里");
    expect(display.modeLabel).toBe("地铁");
    expect(display.compactLabel).toContain("地铁 17 分钟");
    expect(display.compactLabel).not.toContain("公交 0.56 公里");
  });
});
