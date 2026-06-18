import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Icon } from "../components/Icon";

describe("Icon", () => {
  it("renders system icons used by workflow controls", () => {
    const { container } = render(
      <>
        <Icon name="volume" />
        <Icon name="noise" />
        <Icon name="map" />
        <Icon name="search" />
      </>
    );

    expect(container.querySelectorAll("svg .icon-missing")).toHaveLength(0);
    expect(container.querySelectorAll("svg path, svg circle, svg rect, svg line, svg polyline")).not.toHaveLength(0);
  });
});
