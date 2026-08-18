import Antd from "ant-design-vue";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../stores/auth";
import AuthPage from "./AuthPage.vue";

describe("AuthPage", () => {
  beforeEach(() => {
    const createStorage = () => {
      const values = new Map<string, string>();
      return {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
      };
    };
    vi.stubGlobal("localStorage", createStorage());
    vi.stubGlobal("sessionStorage", createStorage());
  });

  it("validates and submits a login", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const authStore = useAuthStore();
    const login = vi.spyOn(authStore, "login").mockResolvedValue();
    const wrapper = mount(AuthPage, {
      global: { plugins: [pinia, Antd] },
    });

    await wrapper.get("form").trigger("submit");
    expect(wrapper.text()).toContain("用户名需为 3–32 位");

    await wrapper.get('input[name="username"]').setValue("Alice_1");
    await wrapper.get('input[name="password"]').setValue("password-123");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(login).toHaveBeenCalledWith("Alice_1", "password-123");
  });

  it("switches to registration", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const authStore = useAuthStore();
    const register = vi
      .spyOn(authStore, "register")
      .mockResolvedValue();
    const wrapper = mount(AuthPage, {
      global: { plugins: [pinia, Antd] },
    });

    await wrapper.get("button.auth-switch").trigger("click");
    await wrapper.get('input[name="username"]').setValue("new_user");
    await wrapper.get('input[name="password"]').setValue("password-123");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("创建账号");
    expect(register).toHaveBeenCalledWith("new_user", "password-123");
  });
});
