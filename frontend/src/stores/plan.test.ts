import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePlanStore } from "./plan";

const values = new Map<string, string>();
const memoryStorage: Storage = {
  get length() {
    return values.size;
  },
  clear() {
    values.clear();
  },
  getItem(key) {
    return values.get(key) ?? null;
  },
  key(index) {
    return [...values.keys()][index] ?? null;
  },
  removeItem(key) {
    values.delete(key);
  },
  setItem(key, value) {
    values.set(key, value);
  },
};

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: memoryStorage,
});

describe("plan store itinerary editing", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it("reorders, removes and undoes schedule changes", async () => {
    const store = usePlanStore();
    await store.load("plan_demo");
    store.enterEdit();

    const originalFirst =
      store.currentDay?.schedule[0]?.schedule_item_id;
    const originalSecond =
      store.currentDay?.schedule[1]?.schedule_item_id;

    store.moveItem(1, 0, 1);
    expect(store.currentDay?.schedule[0]?.schedule_item_id).toBe(
      originalSecond,
    );
    expect(store.routesStale).toBe(true);

    store.removeItem(1, originalFirst!);
    expect(store.currentDay?.schedule).toHaveLength(2);

    store.undo();
    expect(store.currentDay?.schedule).toHaveLength(3);
    expect(store.currentDay?.schedule[0]?.schedule_item_id).toBe(
      originalSecond,
    );
  });

  it("saves a new revision and rebuilds affected routes", async () => {
    vi.useFakeTimers();
    const store = usePlanStore();
    await store.load("plan_demo");
    const previousRevision = store.envelope?.revision ?? 0;
    store.enterEdit();
    store.moveItem(1, 0, 1);

    const saving = store.saveEdits();
    await vi.advanceTimersByTimeAsync(850);
    await saving;

    expect(store.envelope?.revision).toBe(previousRevision + 1);
    expect(store.editing).toBe(false);
    expect(store.plan?.daily_itinerary[0]?.routes).toHaveLength(3);
    expect(
      store.plan?.daily_itinerary[0]?.routes[0]?.route_id,
    ).toBe("edited-1-1");
    vi.useRealTimers();
  });
});
