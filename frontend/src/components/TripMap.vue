<script setup lang="ts">
import AMapLoader from "@amap/amap-jsapi-loader";
import {
  AimOutlined,
  EnvironmentFilled,
  MinusOutlined,
  PlusOutlined,
} from "@ant-design/icons-vue";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import type {
  MapLocation,
  RouteSegment,
} from "../types/travel";
import {
  ROUTE_LABEL_ZOOMS,
  routeLabelLayerOptions,
} from "../utils/map-label-layer";
import { recommendedRouteDisplay } from "../utils/route-display";

const props = withDefaults(
  defineProps<{
    locations: MapLocation[];
    routes?: RouteSegment[];
    routesStale?: boolean;
    dayLabel: string;
    loading?: boolean;
    destinationCity?: string;
  }>(),
  {
    routes: () => [],
    routesStale: false,
    loading: false,
    destinationCity: "",
  },
);

const mapContainer = ref<HTMLElement | null>(null);
const mapReady = ref(false);
const mapError = ref(false);
const hasAmapKey = Boolean(import.meta.env.VITE_AMAP_JS_KEY);
let map: {
  clearMap: () => void;
  add: (overlays: unknown[] | unknown) => void;
  addControl: (control: unknown) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  setCenter: (center: unknown) => void;
  setZoom: (zoom: number) => void;
  setFitView: (...arguments_: unknown[]) => void;
  destroy: () => void;
} | null = null;
let amap: Record<string, new (...args: unknown[]) => unknown> | null = null;
let fitMarkers: unknown[] = [];

function isValidLocation(location: MapLocation): boolean {
  return (
    Number.isFinite(location.longitude) &&
    Number.isFinite(location.latitude)
  );
}

function isCoordinate(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "number" &&
    Number.isFinite(value[0]) &&
    typeof value[1] === "number" &&
    Number.isFinite(value[1])
  );
}

function routePath(
  route: RouteSegment | undefined,
  origin: MapLocation,
  destination: MapLocation,
): Array<[number, number]> {
  if (route && !props.routesStale) {
    const polyline = route[route.recommended_mode]?.polyline;
    if (Array.isArray(polyline)) {
      const validPath = polyline.filter(isCoordinate);
      if (validPath.length >= 2) return validPath;
    }
  }
  return [
    [origin.longitude, origin.latitude],
    [destination.longitude, destination.latitude],
  ];
}

const validLocations = computed(() =>
  props.locations.filter(isValidLocation),
);

const mapSegments = computed(() =>
  props.locations.slice(0, -1).flatMap((origin, index) => {
    const destination = props.locations[index + 1];
    if (
      !destination ||
      !isValidLocation(origin) ||
      !isValidLocation(destination)
    ) {
      return [];
    }
    const route =
      props.routes.find(
        (item) => item.sequence === index + 1,
      ) ?? props.routes[index];
    const routeDisplay =
      route && !props.routesStale
        ? recommendedRouteDisplay(route)
        : null;
    return [
      {
        id: `${origin.id}-${destination.id}`,
        sequence: index,
        origin,
        destination,
        mode: route?.recommended_mode,
        path: routePath(route, origin, destination),
        hasRoadGeometry: Boolean(
          route &&
            !props.routesStale &&
            route[route.recommended_mode]?.polyline?.length,
        ),
        label: props.routesStale
          ? "距离与时间待重新计算"
          : routeDisplay?.compactLabel ||
            "距离与时间待确认",
      },
    ];
  }),
);

const pointPositions = computed(() => {
  if (validLocations.value.length === 0) return [];
  const longitudes = validLocations.value.map(
    (item) => item.longitude,
  );
  const latitudes = validLocations.value.map((item) => item.latitude);
  const minLongitude = Math.min(...longitudes);
  const maxLongitude = Math.max(...longitudes);
  const minLatitude = Math.min(...latitudes);
  const maxLatitude = Math.max(...latitudes);
  const longitudeRange = maxLongitude - minLongitude;
  const latitudeRange = maxLatitude - minLatitude;

  return validLocations.value.map((item, index) => ({
    ...item,
    labelDirection: ["bottom", "right", "top", "left"][
      index % 4
    ],
    left:
      longitudeRange < 0.0001
        ? 50
        : 14 +
          ((item.longitude - minLongitude) / longitudeRange) * 72,
    top:
      latitudeRange < 0.0001
        ? 50
        : 82 -
          ((item.latitude - minLatitude) / latitudeRange) * 64,
  }));
});

const fallbackSegments = computed(() =>
  mapSegments.value.flatMap((segment) => {
    const origin = pointPositions.value.find(
      (point) => point.id === segment.origin.id,
    );
    const destination = pointPositions.value.find(
      (point) => point.id === segment.destination.id,
    );
    if (!origin || !destination) return [];
    const deltaX = destination.left - origin.left;
    const deltaY = destination.top - origin.top;
    const heightToWidthRatio = 390 / 430;
    return [
      {
        ...segment,
        left: origin.left,
        top: origin.top,
        labelLeft: (origin.left + destination.left) / 2,
        labelTop:
          (origin.top + destination.top) / 2 +
          (segment.sequence % 2 === 0 ? -7 : 7),
        width: Math.sqrt(
          deltaX ** 2 +
            (deltaY * heightToWidthRatio) ** 2,
        ),
        angle:
          Math.atan2(
            deltaY * heightToWidthRatio,
            deltaX,
          ) *
          (180 / Math.PI),
      },
    ];
  }),
);

function pathMidpoint(
  path: Array<[number, number]>,
): [number, number] {
  return path[Math.floor((path.length - 1) / 2)] ?? path[0];
}

function routeColor(mode?: RouteSegment["recommended_mode"]): string {
  if (mode === "walking") return "#2f7d61";
  if (mode === "public_transit") return "#356f98";
  return "#be5735";
}

function escapeHtml(value: string): string {
  const entities: Record<string, string> = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };
  return value.replace(/[&<>"']/g, (character) => entities[character]);
}

function normalizeAmapPosition(value: unknown): unknown {
  if (isCoordinate(value)) return value;
  if (typeof value === "string") {
    const coordinate = value.split(",").map(Number);
    return isCoordinate(coordinate) ? coordinate : null;
  }
  if (value && typeof value === "object") {
    const point = value as { lng?: unknown; lat?: unknown };
    const coordinate = [Number(point.lng), Number(point.lat)];
    return isCoordinate(coordinate) ? coordinate : null;
  }
  return null;
}

async function renderRealMap(): Promise<void> {
  if (import.meta.env.MODE === "test") return;
  const key = import.meta.env.VITE_AMAP_JS_KEY;
  if (!key || !mapContainer.value) return;
  try {
    if (import.meta.env.VITE_AMAP_SECURITY_JS_CODE) {
      window._AMapSecurityConfig = {
        securityJsCode: import.meta.env.VITE_AMAP_SECURITY_JS_CODE,
      };
    }
    amap = (await AMapLoader.load({
      key,
      version: "2.0",
      plugins: ["AMap.Scale", "AMap.ToolBar", "AMap.Geocoder"],
    })) as typeof amap;
    if (!amap) return;
    const MapConstructor = amap.Map;
    map = new MapConstructor(mapContainer.value, {
      zoom: 12,
      mapStyle: "amap://styles/whitesmoke",
      viewMode: "2D",
      zoomEnable: true,
      scrollWheel: true,
      doubleClickZoom: true,
      touchZoom: true,
    }) as typeof map;
    const ToolBarConstructor = amap.ToolBar;
    const ScaleConstructor = amap.Scale;
    map.addControl(
      new ToolBarConstructor({
        position: "RB",
      }),
    );
    map.addControl(new ScaleConstructor());
    mapReady.value = true;
    renderOverlays();
  } catch {
    mapError.value = true;
  }
}

function renderOverlays(): void {
  if (!map || !amap) return;
  map.clearMap();
  if (validLocations.value.length === 0) {
    fitMarkers = [];
    if (props.loading && props.destinationCity) {
      centerPlanningDestination();
    }
    return;
  }
  const markers = validLocations.value.map((location) => {
    const MarkerConstructor = amap!.Marker;
    return new MarkerConstructor({
      position: [location.longitude, location.latitude],
      title: location.name,
      content: `
        <div class="amap-trip-point">
          <div class="amap-trip-marker ${
            location.kind === "hotel" ? "is-hotel" : ""
          }">${
            location.kind === "hotel" ? "H" : location.order
          }</div>
        </div>
      `,
      offset: [-18, -18],
      zIndex: 120,
    });
  });
  const overlays: unknown[] = [...markers];
  const LabelMarkerConstructor = amap.LabelMarker;
  const pointLabels = validLocations.value.map(
    (location, index) =>
      new LabelMarkerConstructor({
        position: [location.longitude, location.latitude],
        rank:
          location.kind === "hotel"
            ? 100
            : Math.max(30, 80 - index),
        zooms: [10, 20],
        text: {
          content: location.name,
          direction: ["bottom", "right", "top", "left"][
            index % 4
          ],
          offset: [0, 8],
          style: {
            fontSize: 12,
            fontWeight: "600",
            fillColor: "#173f35",
            strokeColor: "#ffffff",
            strokeWidth: 3,
            padding: [5, 8],
            backgroundColor: "rgba(255, 253, 248, 0.96)",
            borderColor: "rgba(23, 63, 53, 0.14)",
            borderWidth: 1,
          },
        },
      }),
  );
  const routeLabels: unknown[] = [];
  if (mapSegments.value.length > 0) {
    const PolylineConstructor = amap.Polyline;
    mapSegments.value.forEach((segment) => {
      overlays.push(
        new PolylineConstructor({
          path: [
            ...segment.path,
          ],
          strokeColor: routeColor(segment.mode),
          strokeWeight: segment.hasRoadGeometry ? 6 : 4,
          strokeOpacity: segment.hasRoadGeometry ? 0.9 : 0.65,
          strokeStyle: segment.hasRoadGeometry ? "solid" : "dashed",
          showDir: true,
        }),
      );
      routeLabels.push(
        new LabelMarkerConstructor({
          position: pathMidpoint(segment.path),
          rank: 200 + segment.sequence,
          zooms: ROUTE_LABEL_ZOOMS,
          text: {
            content: segment.label,
            direction:
              segment.sequence % 2 === 0 ? "top" : "bottom",
            offset: [0, 10],
            style: {
              fontSize: 11,
              fontWeight: "600",
              fillColor: "#c55331",
              strokeColor: "#ffffff",
              strokeWidth: 3,
              padding: [5, 9],
              backgroundColor: "rgba(255, 249, 245, 0.96)",
              borderColor: "rgba(197, 83, 49, 0.25)",
              borderWidth: 1,
            },
          },
        }),
      );
    });
  }
  const LabelsLayerConstructor = amap.LabelsLayer;
  const pointLabelsLayer = new LabelsLayerConstructor({
    collision: true,
    allowCollision: true,
    zooms: [3, 20],
    zIndex: 200,
  }) as {
    add: (markers: unknown[] | unknown) => void;
  };
  pointLabelsLayer.add(pointLabels);
  overlays.push(pointLabelsLayer);
  if (routeLabels.length > 0) {
    const routeLabelsLayer = new LabelsLayerConstructor(
      routeLabelLayerOptions(),
    ) as {
      add: (markers: unknown[] | unknown) => void;
    };
    routeLabelsLayer.add(routeLabels);
    overlays.push(routeLabelsLayer);
  }
  map.add(overlays);
  fitMarkers = markers;
  map.setFitView(markers, false, [80, 80, 100, 80]);
}

function centerPlanningDestination(): void {
  if (!map || !amap || !props.destinationCity) return;
  const GeocoderConstructor = amap.Geocoder;
  const geocoder = new GeocoderConstructor({
    city: props.destinationCity,
  }) as {
    getLocation: (
      address: string,
      callback: (status: string, result: unknown) => void,
    ) => void;
  };
  geocoder.getLocation(props.destinationCity, (status, rawResult) => {
    if (!map || status !== "complete" || !rawResult) return;
    const result = rawResult as {
      geocodes?: Array<{ location?: unknown }>;
    };
    const center = normalizeAmapPosition(
      result.geocodes?.[0]?.location,
    );
    if (!center) return;
    map.setCenter(center);
    map.setZoom(11);
    const MarkerConstructor = amap!.Marker;
    const cityLabel = escapeHtml(props.destinationCity);
    const marker = new MarkerConstructor({
      position: center,
      title: props.destinationCity,
      content: `
        <div class="amap-planning-point">
          <span></span><b>${cityLabel}</b>
        </div>
      `,
      offset: [-12, -12],
      zIndex: 120,
    });
    map.add(marker);
  });
}

function fitMap(): void {
  if (!map || fitMarkers.length === 0) return;
  map.setFitView(fitMarkers, false, [80, 80, 100, 80]);
}

function zoomInMap(): void {
  map?.zoomIn();
}

function zoomOutMap(): void {
  map?.zoomOut();
}

watch(
  () => [props.locations, props.routes, props.routesStale],
  async () => {
    await nextTick();
    renderOverlays();
  },
  { deep: true },
);

onMounted(renderRealMap);
onBeforeUnmount(() => map?.destroy());
</script>

<template>
  <section class="trip-map" :class="{ 'trip-map--planning': loading }">
    <header>
      <div class="map-heading">
        <span>{{ loading ? "行程地图同步中" : "景点路线地图" }}</span>
        <small>{{ dayLabel }}</small>
      </div>
      <div class="map-actions">
        <button
          type="button"
          aria-label="缩小地图"
          :disabled="!mapReady"
          @click="zoomOutMap"
        >
          <MinusOutlined />
        </button>
        <button
          type="button"
          aria-label="放大地图"
          :disabled="!mapReady"
          @click="zoomInMap"
        >
          <PlusOutlined />
        </button>
        <button
          type="button"
          aria-label="适应地图视野"
          :disabled="!mapReady"
          @click="fitMap"
        >
          <AimOutlined />
        </button>
      </div>
    </header>

    <div class="map-canvas">
      <div
        v-show="mapReady && !mapError"
        ref="mapContainer"
        class="real-map"
      />

      <div
        v-if="!mapReady || mapError"
        class="map-fallback"
        :class="{ 'map-fallback--error': mapError }"
      >
      <span class="map-road road-1" />
      <span class="map-road road-2" />
      <span class="map-road road-3" />
      <span class="map-water" />
      <div class="map-district district-a">东城区</div>
      <div class="map-district district-b">西城区</div>
      <span
        v-for="segment in fallbackSegments"
        :key="segment.id"
        class="fallback-route"
        :style="{
          left: `${segment.left}%`,
          top: `${segment.top}%`,
          width: `${segment.width}%`,
          transform: `rotate(${segment.angle}deg)`,
        }"
      />
      <span
        v-for="segment in fallbackSegments"
        :key="`${segment.id}-info`"
        class="fallback-route-info"
        :style="{
          left: `${segment.labelLeft}%`,
          top: `${segment.labelTop}%`,
        }"
      >
        {{ segment.label }}
      </span>

      <div
        v-for="point in pointPositions.filter(
          (item) => item.kind === 'attraction',
        )"
        :key="point.id"
        class="fallback-marker"
        :style="{ left: `${point.left}%`, top: `${point.top}%` }"
        :title="point.name"
      >
        {{ point.order }}
        <span :class="`fallback-name--${point.labelDirection}`">
          {{ point.name }}
        </span>
      </div>
      <div
        v-for="point in pointPositions.filter(
          (item) => item.kind === 'hotel',
        )"
        :key="point.id"
        class="hotel-marker"
        :style="{ left: `${point.left}%`, top: `${point.top}%` }"
        :title="point.name"
      >
        <EnvironmentFilled />
        <span>{{ point.name }}</span>
      </div>
        <p
          v-if="pointPositions.length === 0 && !loading"
          class="map-empty-copy"
        >
          暂未获得景点坐标，请重新规划后查看地图
        </p>
        <p v-if="mapError" class="map-error-copy">
          地图暂不可用，行程列表仍可正常查看
        </p>
        <p v-else-if="hasAmapKey" class="map-demo-copy">
          正在加载高德地图…
        </p>
        <p v-else class="map-demo-copy">
          配置高德 Web 端 Key 后加载实时地图
        </p>
      </div>

      <div v-if="loading" class="map-planning-overlay">
        <span class="map-planning-pulse"><i /></span>
        <div>
          <strong>正在定位 {{ destinationCity || "目的地" }}</strong>
          <small>景点筛选完成后，将自动绘制访问顺序与分段距离</small>
        </div>
      </div>
    </div>

    <footer>
      <span><i class="legend-hotel" /> 推荐酒店</span>
      <span><i class="legend-attraction" /> 当日景点</span>
      <span><i class="legend-route" /> 高德推荐路线与距离</span>
    </footer>
  </section>
</template>
