import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DevicePanel } from "../features/device/DevicePanel";

const sources = [
  { kind: "device" as const, device_id: "/Volumes/NO NAME", label: "DJI Mic 3", root_path: "/Volumes/NO NAME", audio_count: 12 },
  { kind: "known" as const, device_id: "/lib", label: "samples", root_path: "/lib", audio_count: 30 }
];

describe("DevicePanel", () => {
  it("shows detected device + known source and imports the chosen root", async () => {
    const onImport = vi.fn();
    render(<DevicePanel sources={sources} onImport={onImport} onRefresh={() => undefined} />);
    expect(screen.getByText("DJI Mic 3")).toBeInTheDocument();
    expect(screen.getByText(/12 个新录音/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "导入" })[0]);
    expect(onImport).toHaveBeenCalledWith("/Volumes/NO NAME");
  });
});
