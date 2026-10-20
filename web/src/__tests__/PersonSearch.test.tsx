import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Select } from "../components/ui/Select";
import { personOptions } from "../lib/personSearch";
import type { PersonRow } from "../api/types";

it("sorts names by pinyin and searches Chinese, full pinyin and initials with keyboard selection", async () => {
  const people = ["张清理", "丁伟斌", "王方平"].map((display_name, i) => ({
    person_id: String(i), display_name, person_type: "contact",
  })) as PersonRow[];
  const options = personOptions(people);
  expect(options.map((p) => p.label)).toEqual(["丁伟斌", "王方平", "张清理"]);
  const onChange = vi.fn();
  render(<Select value="" onChange={onChange} options={options} searchable ariaLabel="标注为" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox"));
  const search = screen.getByRole("textbox", { name: "标注为搜索" });
  for (const query of ["方平", "wangfang", "wfp"]) {
    await user.clear(search);
    await user.type(search, query);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["王方平"]);
  }
  await user.keyboard("{Enter}");
  expect(onChange).toHaveBeenCalledWith("2");
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
