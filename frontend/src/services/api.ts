import axios from "axios";
import { API_BASE_URL } from "./api-base";
import type {
  PlanEnvelope,
  PlanningStage,
  TravelRequest,
} from "../types/travel";

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15_000,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

api.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      if (
        error.response?.status === 401 &&
        !error.config?.url?.startsWith("/auth/")
      ) {
        window.dispatchEvent(new Event("travel-auth-expired"));
      }
      const message =
        error.response?.data?.message ||
        error.response?.data?.detail ||
        error.message ||
        "服务暂时不可用，请稍后重试";
      return Promise.reject(new Error(message));
    }
    return Promise.reject(error);
  },
);

export interface AuthUser {
  user_id: string;
  username: string;
  created_at: string;
}

export async function registerUser(
  username: string,
  password: string,
): Promise<AuthUser> {
  const response = await api.post<AuthUser>("/auth/register", {
    username,
    password,
  });
  return response.data;
}

export async function loginUser(
  username: string,
  password: string,
): Promise<AuthUser> {
  const response = await api.post<AuthUser>("/auth/login", {
    username,
    password,
  });
  return response.data;
}

export async function getCurrentUser(): Promise<AuthUser> {
  const response = await api.get<AuthUser>("/auth/me");
  return response.data;
}

export async function logoutUser(): Promise<void> {
  await api.post("/auth/logout");
}

export interface PlanningTask {
  task_id: string;
  session_id: string;
  status: "queued" | "running" | "completed" | "failed";
  events_url: string;
  plan_id?: string;
  previous_plan_id?: string;
  current_stage?: PlanningStage;
  error_message?: string;
  error_code?: string;
  retryable?: boolean;
  error_id?: string;
}

export interface PlanningSession {
  session_id: string;
  current_plan_id?: string;
  title?: string;
  archived_at?: string;
  created_at: string;
  updated_at: string;
  plans: Array<{
    plan_id: string;
    task_id: string;
    previous_plan_id?: string;
    revision: number;
    source_type: "agent" | "manual_edit" | "fork";
    generated_at: string;
  }>;
}

export interface PlanningSessionSummary {
  session_id: string;
  current_plan_id: string;
  title: string;
  destination_city?: string;
  start_date?: string;
  end_date?: string;
  budget_cny?: number;
  accommodation_type?: string;
  latest_requirement?: string;
  current_revision: number;
  revision_count: number;
  archived_at?: string;
  created_at: string;
  updated_at: string;
}

export interface PlanningSessionList {
  items: PlanningSessionSummary[];
  next_cursor?: string;
}

export interface PlanningSessionTurn {
  task_id: string;
  status: "queued" | "running" | "completed" | "failed";
  user_text: string;
  request: TravelRequest;
  plan_id?: string;
  revision?: number;
  source_type?: "agent" | "manual_edit" | "fork";
  error_message?: string;
  error_code?: string;
  retryable?: boolean;
  error_id?: string;
  created_at: string;
  updated_at: string;
  events: Array<{
    event_id: string;
    type: string;
    timestamp: string;
    stage?: PlanningStage;
    message: string;
    plan_id?: string;
  }>;
}

export async function createTravelPlan(
  request: TravelRequest,
): Promise<PlanningTask> {
  const response = await api.post<PlanningTask>(
    "/travel-plans",
    request,
    {
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
      },
    },
  );
  return response.data;
}

export async function getPlanningTask(
  taskId: string,
): Promise<PlanningTask> {
  const response = await api.get<PlanningTask>(
    `/travel-plans/${taskId}`,
  );
  return response.data;
}

export async function getPlan(
  planId: string,
): Promise<PlanEnvelope> {
  const response = await api.get<PlanEnvelope>(`/plans/${planId}`);
  return response.data;
}

export async function getPlanningSession(
  sessionId: string,
): Promise<PlanningSession> {
  const response = await api.get<PlanningSession>(
    `/sessions/${sessionId}`,
  );
  return response.data;
}

export async function listPlanningSessions(options?: {
  cursor?: string;
  query?: string;
  limit?: number;
}): Promise<PlanningSessionList> {
  const response = await api.get<PlanningSessionList>("/sessions", {
    params: options,
  });
  return response.data;
}

export async function getPlanningSessionTurns(
  sessionId: string,
): Promise<PlanningSessionTurn[]> {
  const response = await api.get<{
    session_id: string;
    turns: PlanningSessionTurn[];
  }>(`/sessions/${sessionId}/turns`);
  return response.data.turns;
}

export async function forkPlanningSession(
  sessionId: string,
  sourcePlanId: string,
): Promise<PlanEnvelope> {
  const response = await api.post<PlanEnvelope>(
    `/sessions/${sessionId}/fork`,
    { source_plan_id: sourcePlanId },
  );
  return response.data;
}

export async function saveItinerary(
  planId: string,
  baseRevision: number,
  dailyItinerary: PlanEnvelope["plan"]["daily_itinerary"],
): Promise<PlanEnvelope> {
  const response = await api.patch<PlanEnvelope>(
    `/plans/${planId}/itinerary`,
    {
      base_revision: baseRevision,
      daily_itinerary: dailyItinerary,
    },
  );
  return response.data;
}
