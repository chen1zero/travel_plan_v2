import type {
  PlanningStage,
} from "../types/travel";
import { resolveApiUrl } from "./api-base";

export interface PlanningEvent {
  event_id: string;
  type:
    | "task.queued"
    | "task.started"
    | "harness.started"
    | "node.started"
    | "node.completed"
    | "node.skipped"
    | "node.degraded"
    | "node.failed"
    | "change.analysis"
    | "plan.validation"
    | "revision.validation"
    | "harness.completed"
    | "stage.started"
    | "agent.iteration"
    | "agent.recovering"
    | "tool.started"
    | "tool.retrying"
    | "tool.completed"
    | "tool.failed"
    | "stage.completed"
    | "plan.completed"
    | "task.failed";
  timestamp: string;
  stage?: PlanningStage;
  message: string;
  plan_id?: string;
  tool?: string;
  tool_call_id?: string;
  attempt?: number;
  max_attempts?: number;
  duration_ms?: number;
  error_code?: string;
  retryable?: boolean;
  error_id?: string;
  degraded?: boolean;
}

export interface PlanningEventHandlers {
  onEvent: (event: PlanningEvent) => void;
  onError: () => void;
  onOpen?: () => void;
}

export function subscribePlanningEvents(
  eventsUrl: string,
  handlers: PlanningEventHandlers,
): () => void {
  const url = resolveApiUrl(eventsUrl);
  const source = new EventSource(url, { withCredentials: true });
  source.onopen = () => handlers.onOpen?.();

  const eventTypes: PlanningEvent["type"][] = [
    "task.queued",
    "task.started",
    "harness.started",
    "node.started",
    "node.completed",
    "node.skipped",
    "node.degraded",
    "node.failed",
    "change.analysis",
    "plan.validation",
    "revision.validation",
    "harness.completed",
    "stage.started",
    "agent.iteration",
    "agent.recovering",
    "tool.started",
    "tool.retrying",
    "tool.completed",
    "tool.failed",
    "stage.completed",
    "plan.completed",
    "task.failed",
  ];

  eventTypes.forEach((type) => {
    source.addEventListener(type, (rawEvent) => {
      try {
        const parsed = JSON.parse(
          (rawEvent as MessageEvent<string>).data,
        ) as PlanningEvent;
        handlers.onEvent({ ...parsed, type });
      } catch {
        handlers.onError();
      }
    });
  });
  source.onerror = handlers.onError;

  return () => source.close();
}
