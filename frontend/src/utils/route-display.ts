import type {
  RouteMode,
  RouteSegment,
} from "../types/travel";

export type TransportMode =
  | "walking"
  | "driving"
  | "public_transit";

const MODE_LABELS: Record<TransportMode, string> = {
  walking: "步行",
  driving: "打车",
  public_transit: "公共交通",
};

const TRANSIT_LABELS: Record<
  NonNullable<RouteMode["transit_type"]>,
  string
> = {
  subway: "地铁",
  bus: "公交",
  mixed: "公交+地铁",
  rail: "轨道交通",
  unknown: "公共交通",
};

const distanceFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 2,
});

export function transportModeLabel(
  mode: TransportMode,
): string {
  return MODE_LABELS[mode];
}

export function publicTransitModeLabel(mode: RouteMode): string {
  return TRANSIT_LABELS[mode.transit_type ?? "unknown"];
}

export function distanceLabel(mode: RouteMode): string {
  return mode.distance_km === null
    ? "距离待确认"
    : `${distanceFormatter.format(mode.distance_km)} 公里`;
}

export function durationLabel(mode: RouteMode): string {
  return mode.duration_minutes === null
    ? "时间待确认"
    : `${mode.duration_minutes} 分钟`;
}

export function recommendedRouteDisplay(route: RouteSegment) {
  const mode = route.recommended_mode;
  const metrics = route[mode];
  const modeLabel =
    mode === "public_transit"
      ? publicTransitModeLabel(metrics)
      : transportModeLabel(mode);
  const selectedDistanceLabel =
    mode === "public_transit" &&
    metrics.distance_km === null &&
    metrics.walking_distance_km !== null &&
    metrics.walking_distance_km !== undefined
      ? `接驳步行 ${distanceFormatter.format(
          metrics.walking_distance_km,
        )} 公里`
      : distanceLabel(metrics);
  return {
    mode,
    modeLabel,
    distanceLabel: selectedDistanceLabel,
    durationLabel: durationLabel(metrics),
    compactLabel: `${selectedDistanceLabel} · ${modeLabel} ${durationLabel(
      metrics,
    )}`,
  };
}
