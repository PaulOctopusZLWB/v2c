import { pinyin } from "pinyin-pro";
import type { PersonRow } from "../api/types";

/** Keep the name (rather than the non-speaker prefix) as the sorting/search key. */
export function personOptions(people: Pick<PersonRow, "person_id" | "display_name" | "person_type">[]) {
  return people.map((person) => {
    const syllables = pinyin(person.display_name, { toneType: "none", type: "array" });
    const full = syllables.join("");
    const initials = syllables.map((s) => s[0]).join("");
    return {
      value: person.person_id,
      label: person.person_type === "non_speaker" ? `非发言人 · ${person.display_name}` : person.display_name,
      searchText: `${full} ${initials}`,
      sortKey: full,
    };
  }).sort((a, b) => a.sortKey.localeCompare(b.sortKey, "en") || a.value.localeCompare(b.value));
}

export function matchesPerson(option: { label: string; searchText?: string }, query: string) {
  const normalize = (text: string) => text.toLocaleLowerCase().replace(/\s+/g, "");
  return normalize(`${option.label} ${option.searchText ?? ""}`).includes(normalize(query));
}
