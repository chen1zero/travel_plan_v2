export type AccommodationType = "经济型" | "舒适型" | "豪华型" | "不限";

export interface TravelRequest {
  destination_city: string;
  destination_adcode?: string;
  start_date: string;
  end_date: string;
  preferences: string[];
  budget_cny: number;
  accommodation_type: AccommodationType;
  additional_requirements?: string;
  session_id?: string;
  previous_plan_id?: string;
}

export type PlanningStage =
  | "attraction"
  | "weather"
  | "hotel"
  | "planner";

export type PlanningNodeStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export type PlanningTraceKind =
  | "thought"
  | "action"
  | "observation"
  | "conclusion"
  | "system";

export interface PlanningLog {
  id: string;
  timestamp: string;
  stage: PlanningStage | "harness";
  message: string;
  detail?: string;
  kind?: PlanningTraceKind;
}

export interface MapLocation {
  id: string;
  name: string;
  address: string;
  longitude: number;
  latitude: number;
  kind: "hotel" | "attraction";
  order?: number;
}

export interface RouteMode {
  available: boolean;
  distance_km: number | null;
  duration_minutes: number | null;
  error: string | null;
  walking_distance_km?: number | null;
  transfer_count?: number | null;
  transit_type?:
    | "subway"
    | "bus"
    | "mixed"
    | "rail"
    | "unknown";
  line_names?: string[];
  polyline?: Array<[number, number]>;
}

export interface RouteSegment {
  route_id: string;
  sequence: number;
  origin: {
    name: string;
    address: string;
    city: string;
  };
  destination: {
    name: string;
    address: string;
    city: string;
  };
  walking: RouteMode;
  driving: RouteMode;
  public_transit: RouteMode;
  recommended_mode: "walking" | "driving" | "public_transit";
  recommendation_reason: string;
  geometry_source?: "amap";
}

export interface ScheduleItem {
  schedule_item_id: string;
  order: number;
  time_slot: string;
  place_name: string;
  address: string;
  activity: string;
  duration_minutes: number | null;
  notes: string[];
  location: {
    longitude: number;
    latitude: number;
  };
}

export interface DailyItinerary {
  day: number;
  date: string;
  theme: string;
  weather_advice: string;
  schedule: ScheduleItem[];
  routes: RouteSegment[];
  estimated_cost_cny: {
    transport: number | null;
    tickets: number | null;
    food: number | null;
    hotel: number | null;
    subtotal: number | null;
    notes: string[];
  };
}

export interface WeatherDay {
  date: string;
  day_weather: string | null;
  night_weather: string | null;
  min_temperature_c: number | null;
  max_temperature_c: number | null;
  advice: string[];
}

export interface TravelPlan {
  plan_version: string;
  request_summary: {
    destination_city: string;
    start_date: string;
    end_date: string;
    days: number;
    budget_cny: number;
    preferences: string[];
    hotel_requirement: string;
    unresolved_fields: string[];
  };
  weather_summary: WeatherDay[];
  selected_hotel: {
    name: string;
    address: string;
    selection_reason: string;
    price_cny_per_night: number | null;
    booking_note: string;
    location: {
      longitude: number;
      latitude: number;
    };
  };
  daily_itinerary: DailyItinerary[];
  budget_summary: {
    currency: "CNY";
    total_budget: number;
    estimated_total: number | null;
    remaining: number | null;
    breakdown: {
      transport: number | null;
      tickets: number | null;
      food: number | null;
      hotel: number | null;
    };
    notes: string[];
  };
  booking_and_safety_tips: string[];
  data_notes: string[];
}

export interface PlanEnvelope {
  plan_id: string;
  task_id: string;
  session_id: string;
  previous_plan_id?: string | null;
  revision: number;
  source_type?: "agent" | "manual_edit" | "fork";
  status: "completed";
  generated_at: string;
  plan: TravelPlan;
}
