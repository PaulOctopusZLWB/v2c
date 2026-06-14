import { useState } from "react";
import type { Person } from "../../api/types";
import { t } from "../../i18n";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { Icon } from "../../components/Icon";

function SpeakerAssignRow({
  speaker,
  persons,
  onAssign
}: {
  speaker: string;
  persons: Person[];
  onAssign: (speaker: string, personId: string) => Promise<unknown> | void;
}) {
  const assign = useAsyncAction(async (spk: string, personId: string) => { await onAssign(spk, personId); });
  return (
    <div className="speaker-row">
      <span className="chip" style={{ background: speakerColor(speaker) }}>
        <Icon name="person" /> {speaker}
      </span>
      {assign.pending ? <span className="spinner" aria-hidden /> : null}
      <select
        aria-label={`${t.speaker.assign} ${speaker}`}
        defaultValue=""
        disabled={assign.pending}
        onChange={(event) => event.target.value && void assign.run(speaker, event.target.value)}
      >
        <option value="" disabled>{t.speaker.assign}…</option>
        {persons.map((person) => (
          <option key={person.person_id} value={person.person_id}>{person.display_name}</option>
        ))}
      </select>
    </div>
  );
}

export function SpeakerPanel({
  speakers,
  persons,
  onAssign,
  onCreatePerson
}: {
  speakers: string[];
  persons: Person[];
  onAssign: (speaker: string, personId: string) => Promise<unknown> | void;
  onCreatePerson: (displayName: string) => Promise<void>;
}) {
  const [newName, setNewName] = useState("");
  const create = useAsyncAction(async (name: string) => {
    await onCreatePerson(name);
    setNewName("");
  });
  return (
    <section className="speaker-panel card">
      <div className="section-title">
        <Icon name="person" /> {t.speaker.speaker}
      </div>
      {speakers.map((speaker) => (
        <SpeakerAssignRow key={speaker} speaker={speaker} persons={persons} onAssign={onAssign} />
      ))}
      <div className="speaker-add">
        <input
          aria-label={t.speaker.newPerson}
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder={t.speaker.newPerson}
          disabled={create.pending}
        />
        <button
          className="ghost"
          onClick={() => newName && void create.run(newName)}
          disabled={create.pending || !newName}
          aria-busy={create.pending}
        >
          {create.pending ? <span className="spinner" aria-hidden /> : <Icon name="person" />}
          {create.pending ? "正在新建…" : t.speaker.newPerson}
        </button>
      </div>
    </section>
  );
}
