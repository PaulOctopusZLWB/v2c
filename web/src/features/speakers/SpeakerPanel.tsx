import { useState } from "react";
import type { Person } from "../../api/types";
import { t } from "../../i18n";
import { speakerColor } from "../../lib/speakerColors";

export function SpeakerPanel({
  speakers,
  persons,
  onAssign,
  onCreatePerson
}: {
  speakers: string[];
  persons: Person[];
  onAssign: (speaker: string, personId: string) => void;
  onCreatePerson: (displayName: string) => Promise<void>;
}) {
  const [newName, setNewName] = useState("");
  return (
    <section className="speaker-panel">
      <h2>{t.speaker.speaker}</h2>
      {speakers.map((speaker) => (
        <div className="speaker-row" key={speaker}>
          <span className="chip" style={{ background: speakerColor(speaker) }}>{speaker}</span>
          <select
            aria-label={`${t.speaker.assign} ${speaker}`}
            defaultValue=""
            onChange={(event) => event.target.value && onAssign(speaker, event.target.value)}
          >
            <option value="" disabled>{t.speaker.assign}…</option>
            {persons.map((person) => (
              <option key={person.person_id} value={person.person_id}>{person.display_name}</option>
            ))}
          </select>
        </div>
      ))}
      <div className="speaker-add">
        <input
          aria-label={t.speaker.newPerson}
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder={t.speaker.newPerson}
        />
        <button onClick={() => newName && onCreatePerson(newName)}>{t.speaker.newPerson}</button>
      </div>
    </section>
  );
}
