import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { PeoplePanel } from "../features/people/PeoplePanel";
import { IdentityReviewPanel } from "../features/speakers/IdentityReviewPanel";
import { Select } from "../components/ui/Select";
import { personOptions } from "../lib/personSearch";
import type { PersonRow } from "../api/types";

const people: PersonRow[] = [
  { person_id: "p", display_name: "李雷", person_type: "contact", is_self: 0, enrolled: true, manual_count: 2, attributed_count: 4 },
];
const noop = () => {};
const props = { onChanged: noop, push: noop, pushAction: noop, confirm: async () => true };
const review = (id: string, name: string) => ({
  session_id: id, can_summarize: true, participants: [{ person_id: id, display_name: name, status: "present" }],
  candidates: [], new_person_candidates: [], negative_feedback_count: 0,
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("uses name, pinyin and initials in the roster and refreshes counts after external labeling", async () => {
  vi.spyOn(api, "people").mockResolvedValueOnce({ people })
    .mockResolvedValue({ people: [{ ...people[0], manual_count: 99 }] });
  const { rerender } = render(<PeoplePanel {...props} refreshVersion={0} />);
  await screen.findByText("李雷");
  const user = userEvent.setup();
  for (const query of ["李", "LILEI", "ll"]) {
    await user.clear(screen.getByRole("searchbox"));
    await user.type(screen.getByRole("searchbox"), query);
    expect(screen.getByText("李雷")).toBeVisible();
  }
  rerender(<PeoplePanel {...props} refreshVersion={1} />);
  expect(await screen.findByText("99")).toBeInTheDocument();
});

it("keeps the entered name and reports a creation failure", async () => {
  vi.spyOn(api, "people").mockResolvedValue({ people });
  vi.spyOn(api, "createPerson").mockRejectedValue(new Error("连接中断"));
  const push = vi.fn();
  const { container } = render(<PeoplePanel {...props} push={push} />);
  await screen.findByText("李雷");
  await userEvent.click(container.querySelector(".people-management > summary")!);
  await userEvent.type(screen.getByRole("textbox", { name: "新建人物" }), "新人");
  await userEvent.click(screen.getByRole("button", { name: "新建人物" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("新建人物失败", "连接中断", "error"));
  expect(screen.getByRole("textbox", { name: "新建人物" })).toHaveValue("新人");
});

it("ignores a late response from the previous meeting", async () => {
  let finishFirst!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishFirst = resolve; }))
    .mockResolvedValue(new Response(JSON.stringify(review("new", "新会话人物")))));
  const onReviewChange = vi.fn();
  const { rerender } = render(<IdentityReviewPanel sessionId="old" onChanged={noop} push={noop} onReviewChange={onReviewChange} />);
  rerender(<IdentityReviewPanel sessionId="new" onChanged={noop} push={noop} onReviewChange={onReviewChange} />);
  await screen.findByText("新会话人物");
  await act(async () => { finishFirst(new Response(JSON.stringify(review("old", "旧会话人物")))); });
  expect(screen.queryByText("旧会话人物")).not.toBeInTheDocument();
  expect(onReviewChange.mock.calls[onReviewChange.mock.calls.length - 1]?.[0].session_id).toBe("new");
});

it("shows identity load errors and supports retry", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new Error("读取失败"))
    .mockResolvedValue(new Response(JSON.stringify(review("new", "新会话人物")))));
  render(<IdentityReviewPanel sessionId="new" onChanged={noop} push={noop} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
  await userEvent.click(screen.getByRole("button", { name: "重新读取身份" }));
  expect(await screen.findByText("新会话人物")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("handles IME, empty search, Tab dismissal and disabling an open picker", async () => {
  const change = vi.fn();
  const options = personOptions(people);
  const { rerender } = render(<Select options={options} searchable value="" onChange={change} ariaLabel="人物" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox"));
  const input = screen.getByRole("textbox");
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(change).not.toHaveBeenCalled();
  await user.type(input, "nobody");
  await user.keyboard("{Enter}");
  expect(change).not.toHaveBeenCalled();
  await user.tab();
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  await user.click(screen.getByRole("combobox"));
  rerender(<Select options={options} searchable value="" onChange={change} ariaLabel="人物" disabled />);
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
