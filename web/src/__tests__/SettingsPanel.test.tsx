import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPanel } from "../components/SettingsPanel";

const initialSettings = {
  asr_mode: "chunk" as const,
  asr_preset_spk_num: null,
  glm_model: "glm-5.1",
  glm_base_url: "https://open.bigmodel.cn/api/paas/v4",
  glm_thinking: true
};

function mockFetch(impl: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("SettingsPanel", () => {
  beforeEach(() => {
    mockFetch(async (url) => {
      if (url === "/api/settings") return new Response(JSON.stringify(initialSettings), { status: 200 });
      return new Response("{}", { status: 200 });
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the current settings loaded from GET /api/settings", async () => {
    render(<SettingsPanel />);
    // ASR mode select (portalled combobox) reflects the current value via its trigger label.
    await waitFor(() => expect(screen.getByRole("combobox", { name: /ASR 模式/ }).textContent).toContain("一次性"));
    expect(screen.getByLabelText(/LLM 模型/)).toHaveValue("glm-5.1");
    expect(screen.getByLabelText(/GLM Base URL/)).toHaveValue("https://open.bigmodel.cn/api/paas/v4");
    expect((screen.getByLabelText(/深度思考/) as HTMLInputElement).checked).toBe(true);
    // The "takes effect next run" hint is shown.
    expect(screen.getByText(/下次运行生效/)).toBeInTheDocument();
  });

  it("saves only the changed ASR mode via PUT /api/settings", async () => {
    render(<SettingsPanel />);
    const trigger = await screen.findByRole("combobox", { name: /ASR 模式/ });
    await waitFor(() => expect(trigger.textContent).toContain("一次性"));

    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole("option", { name: "声纹" }));
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));

    const put = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
      (c) => c[0] === "/api/settings" && (c[1] as RequestInit | undefined)?.method === "PUT"
    );
    expect(put).toBeTruthy();
    const body = JSON.parse((put![1] as RequestInit).body as string);
    expect(body).toEqual({ asr_mode: "diarize" });
  });
});
