import { useState } from "react";
import type { Person } from "../../api/types";

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
    <section>
      <h2>Speakers</h2>
      {speakers.map((speaker) => (
        <div key={speaker}>
          <label>
            {`Person for ${speaker}`}
            <select
              aria-label={`Person for ${speaker}`}
              defaultValue=""
              onChange={(event) => event.target.value && onAssign(speaker, event.target.value)}
            >
              <option value="" disabled>Assign person…</option>
              {persons.map((person) => (
                <option key={person.person_id} value={person.person_id}>{person.display_name}</option>
              ))}
            </select>
          </label>
        </div>
      ))}
      <div>
        <input aria-label="New person name" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="New person" />
        <button onClick={() => newName && onCreatePerson(newName)}>Add person</button>
      </div>
    </section>
  );
}
