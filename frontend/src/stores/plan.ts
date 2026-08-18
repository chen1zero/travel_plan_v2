import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { createDemoPlan } from "../mocks/demo-plan";
import {
  getPlan,
  getPlanningSession,
  saveItinerary,
} from "../services/api";
import type {
  DailyItinerary,
  PlanEnvelope,
  RouteMode,
  RouteSegment,
  TravelRequest,
} from "../types/travel";

const PLAN_STORAGE_KEY = "travel-current-plan";
const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

function cloneDays(days: DailyItinerary[]): DailyItinerary[] {
  return JSON.parse(JSON.stringify(days)) as DailyItinerary[];
}

function loadPlan(): PlanEnvelope | null {
  const value = localStorage.getItem(PLAN_STORAGE_KEY);
  if (!value) return null;
  try {
    return JSON.parse(value) as PlanEnvelope;
  } catch {
    return null;
  }
}

function distanceKm(
  a: { longitude: number; latitude: number },
  b: { longitude: number; latitude: number },
): number {
  const earthRadius = 6371;
  const toRadians = (degrees: number) => (degrees * Math.PI) / 180;
  const latitudeDelta = toRadians(b.latitude - a.latitude);
  const longitudeDelta = toRadians(b.longitude - a.longitude);
  const latitude1 = toRadians(a.latitude);
  const latitude2 = toRadians(b.latitude);
  const value =
    Math.sin(latitudeDelta / 2) ** 2 +
    Math.cos(latitude1) *
      Math.cos(latitude2) *
      Math.sin(longitudeDelta / 2) ** 2;
  return earthRadius * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
}

function availableMode(
  distance: number,
  speedKmH: number,
): RouteMode {
  return {
    available: true,
    distance_km: Number(distance.toFixed(1)),
    duration_minutes: Math.max(
      4,
      Math.round((distance / speedKmH) * 60),
    ),
    error: null,
  };
}

export const usePlanStore = defineStore("plan", () => {
  const envelope = ref<PlanEnvelope | null>(loadPlan());
  const selectedDay = ref(1);
  const editing = ref(false);
  const saving = ref(false);
  const draftDays = ref<DailyItinerary[]>([]);
  const history = ref<DailyItinerary[][]>([]);
  const routesStale = ref(false);

  const plan = computed(() => envelope.value?.plan ?? null);
  const visibleDays = computed(() =>
    editing.value ? draftDays.value : plan.value?.daily_itinerary ?? [],
  );
  const currentDay = computed(
    () =>
      visibleDays.value.find((day) => day.day === selectedDay.value) ??
      visibleDays.value[0] ??
      null,
  );

  function persist(): void {
    if (envelope.value) {
      localStorage.setItem(
        PLAN_STORAGE_KEY,
        JSON.stringify(envelope.value),
      );
    }
  }

  function setEnvelope(nextEnvelope: PlanEnvelope): void {
    envelope.value = nextEnvelope;
    selectedDay.value = nextEnvelope.plan.daily_itinerary[0]?.day ?? 1;
    persist();
  }

  function clear(): void {
    envelope.value = null;
    selectedDay.value = 1;
    cancelEdit();
    localStorage.removeItem(PLAN_STORAGE_KEY);
  }

  async function load(
    planId: string,
    request?: TravelRequest | null,
  ): Promise<void> {
    if (USE_MOCK) {
      const demo = createDemoPlan(request ?? undefined);
      if (request?.previous_plan_id && envelope.value) {
        demo.plan_id = `plan_demo_${Date.now()}`;
        demo.session_id = envelope.value.session_id;
        demo.previous_plan_id = request.previous_plan_id;
        demo.revision = envelope.value.revision + 1;
      }
      setEnvelope(demo);
      return;
    }
    setEnvelope(await getPlan(planId));
  }

  async function syncLatestSession(): Promise<boolean> {
    if (USE_MOCK || !envelope.value) return false;
    const session = await getPlanningSession(
      envelope.value.session_id,
    );
    const latestPlanId = session.current_plan_id;
    if (!latestPlanId || latestPlanId === envelope.value.plan_id) {
      return false;
    }
    setEnvelope(await getPlan(latestPlanId));
    return true;
  }

  function enterEdit(): void {
    if (!plan.value) return;
    draftDays.value = cloneDays(plan.value.daily_itinerary);
    history.value = [];
    routesStale.value = false;
    editing.value = true;
  }

  function pushHistory(): void {
    history.value.push(cloneDays(draftDays.value));
    if (history.value.length > 20) history.value.shift();
  }

  function normalizeOrder(day: DailyItinerary): void {
    day.schedule.forEach((item, index) => {
      item.order = index + 1;
    });
  }

  function moveItem(dayNumber: number, from: number, to: number): void {
    const day = draftDays.value.find((item) => item.day === dayNumber);
    if (!day || to < 0 || to >= day.schedule.length || from === to) return;
    pushHistory();
    const [moved] = day.schedule.splice(from, 1);
    if (!moved) return;
    day.schedule.splice(to, 0, moved);
    normalizeOrder(day);
    routesStale.value = true;
  }

  function removeItem(dayNumber: number, itemId: string): void {
    const day = draftDays.value.find((item) => item.day === dayNumber);
    if (!day || day.schedule.length <= 1) return;
    pushHistory();
    day.schedule = day.schedule.filter(
      (item) => item.schedule_item_id !== itemId,
    );
    normalizeOrder(day);
    routesStale.value = true;
  }

  function undo(): void {
    const previous = history.value.pop();
    if (!previous) return;
    draftDays.value = cloneDays(previous);
    routesStale.value = history.value.length > 0;
  }

  function cancelEdit(): void {
    editing.value = false;
    draftDays.value = [];
    history.value = [];
    routesStale.value = false;
  }

  function rebuildRoutes(day: DailyItinerary): RouteSegment[] {
    if (!envelope.value) return [];
    const hotel = envelope.value.plan.selected_hotel;
    const points = [
      {
        name: hotel.name,
        address: hotel.address,
        location: hotel.location,
      },
      ...day.schedule.map((item) => ({
        name: item.place_name,
        address: item.address,
        location: item.location,
      })),
    ];

    return points.slice(0, -1).map((origin, index) => {
      const destination = points[index + 1];
      const distance = Math.max(
        0.8,
        distanceKm(origin.location, destination.location) * 1.2,
      );
      const walking = availableMode(distance, 4.6);
      const driving = availableMode(distance * 1.18, 20);
      const transit: RouteMode = {
        ...availableMode(distance * 1.08, 13),
        walking_distance_km: 0.4,
        transfer_count: 0,
        transit_type: distance > 3 ? "subway" : "bus",
        line_names: [],
      };
      const recommended: RouteSegment["recommended_mode"] =
        distance <= 1 ? "walking" : "public_transit";
      return {
        route_id: `edited-${day.day}-${index + 1}`,
        sequence: index + 1,
        origin: {
          name: origin.name,
          address: origin.address,
          city: envelope.value!.plan.request_summary.destination_city,
        },
        destination: {
          name: destination.name,
          address: destination.address,
          city: envelope.value!.plan.request_summary.destination_city,
        },
        walking,
        driving,
        public_transit: transit,
        recommended_mode: recommended,
        recommendation_reason:
          recommended === "walking"
            ? "距离不超过1公里，推荐步行"
            : distance <= 10
              ? "1至10公里，公共交通直达且接驳步行不超过600米"
              : "距离超过10公里，地铁直达且接驳近，优先选择地铁",
      };
    });
  }

  async function saveEdits(): Promise<void> {
    if (!envelope.value) return;
    saving.value = true;
    try {
      const nextDays = cloneDays(draftDays.value);
      if (USE_MOCK) {
        await new Promise((resolve) => window.setTimeout(resolve, 850));
        nextDays.forEach((day) => {
          day.routes = rebuildRoutes(day);
        });
        const previousPlanId = envelope.value.plan_id;
        envelope.value.plan.daily_itinerary = nextDays;
        envelope.value.previous_plan_id = previousPlanId;
        envelope.value.plan_id = `plan_demo_edit_${Date.now()}`;
        envelope.value.task_id = `task_demo_edit_${Date.now()}`;
        envelope.value.revision += 1;
        envelope.value.source_type = "manual_edit";
        envelope.value.generated_at = new Date().toISOString();
        persist();
      } else {
        setEnvelope(
          await saveItinerary(
            envelope.value.plan_id,
            envelope.value.revision,
            nextDays,
          ),
        );
      }
      cancelEdit();
    } finally {
      saving.value = false;
    }
  }

  return {
    envelope,
    plan,
    selectedDay,
    editing,
    saving,
    draftDays,
    history,
    routesStale,
    visibleDays,
    currentDay,
    setEnvelope,
    clear,
    load,
    syncLatestSession,
    enterEdit,
    moveItem,
    removeItem,
    undo,
    cancelEdit,
    saveEdits,
  };
});
