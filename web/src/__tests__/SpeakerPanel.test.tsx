import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SpeakerPanel } from "../features/speakers/SpeakerPanel";

describe("SpeakerPanel", () => {
  it("assigns a speaker to a chosen person", async () => {
    const onAssign = vi.fn();
    render(
      <SpeakerPanel
        speakers={["spk_1"]}
        persons={[{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }]}
        onAssign={onAssign}
        onCreatePerson={async () => undefined}
      />
    );
    await userEvent.selectOptions(screen.getByLabelText("指派发言人 spk_1"), "per_paul");
    expect(onAssign).toHaveBeenCalledWith("spk_1", "per_paul");
  });
});
