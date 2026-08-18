import { describe, expect, it } from "vitest";
import { API_BASE_URL, resolveApiUrl } from "./api-base";

describe("API base URL", () => {
  it("uses the same configured prefix for HTTP and SSE resources", () => {
    expect(resolveApiUrl("travel-plans/task-1/events")).toBe(
      `${API_BASE_URL}/travel-plans/task-1/events`,
    );
  });

  it("keeps absolute event URLs unchanged", () => {
    expect(resolveApiUrl("https://example.com/api/events")).toBe(
      "https://example.com/api/events",
    );
  });
});
