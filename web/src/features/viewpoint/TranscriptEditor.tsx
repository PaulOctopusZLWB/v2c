import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PersonRow, ViewpointSegment } from "../../api/types";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { speakerColor } from "../../lib/speakerColors";
import { Icon } from "../../components/Icon";

/**
 * The left pane of the 观点 workspace: the session's transcript as editable turns. Each turn
 * plays on click (the shared singleton player), its text edits inline (PATCH), and its speaker
 * reassigns by labelling the segment to a person. Any mutation calls `onChanged` so the parent
 * refetches the viewpoint state (so `stale`/`status` stay correct). A banner warns when edits
 * have made the generated 观点 stale.
 */
export function TranscriptEditor({
  segments,
  stale,
  onChanged,
  onPlaybackError
}: {
  segments: ViewpointSegment[];
  stale: boolean;
  onChanged: () => void;
  onPlaybackError?: (message: string) => void;
}) {
  const [people, setPeople] = useState<PersonRow[]>([]);
  const audio = useSegmentAudio();

  useEffect(() => {
    let cancelled = false;
    api
      .people()
      .then((r) => { if (!cancelled) setPeople(r.people ?? []); })
      .catch(() => { if (!cancelled) setPeople([]); });
    return () => { cancelled = true; };
  }, []);

  const play = (id: string) => {
    void audio
      .play(id)
      .catch((err) => onPlaybackError?.(err instanceof Error ? err.message : "音频播放失败"));
  };

  return (
    <section className="vp-transcript card">
      <div className="section-title">
        <Icon name="viewpoint" /> 转写
      </div>
      {stale ? (
        <div className="vp-stale" role="alert">
          <Icon name="flag" /> 转写已改动,观点可能已过期 — 请「重新生成」
        </div>
      ) : null}
      <div className="vp-turns">
        {segments.map((seg) => (
          <TurnRow
            key={seg.segment_id}
            seg={seg}
            people={people}
            playing={audio.playing === seg.segment_id}
            onPlay={() => play(seg.segment_id)}
            onChanged={onChanged}
          />
        ))}
      </div>
    </section>
  );
}

function TurnRow({
  seg,
  people,
  playing,
  onPlay,
  onChanged
}: {
  seg: ViewpointSegment;
  people: PersonRow[];
  playing: boolean;
  onPlay: () => void;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(seg.text);
  const [text, setText] = useState(seg.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = seg.person_label?.trim() || seg.speaker;

  const save = async () => {
    const next = draft.trim();
    if (!next || next === text) {
      setEditing(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.editSegmentText(seg.segment_id, next);
      setText(next);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const reassign = async (personId: string) => {
    if (!personId) return;
    setBusy(true);
    setError(null);
    try {
      await api.labelSegments(personId, [seg.segment_id]);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "改人失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="vp-turn" style={{ borderLeftColor: speakerColor(seg.speaker) }}>
      <div className="vp-turn-head">
        <span className="chip" style={{ background: speakerColor(seg.speaker) }}>
          <Icon name="person" /> {label}
        </span>
        <button
          type="button"
          className={`icon-btn ghost${playing ? " playing" : ""}`}
          aria-label="播放"
          title="播放"
          onClick={onPlay}
        >
          <Icon name="play" />
        </button>
        {!editing ? (
          <button type="button" className="ghost ghost-sm" aria-label="编辑" onClick={() => { setDraft(text); setEditing(true); }}>
            ✎ 编辑
          </button>
        ) : null}
        <select
          className="vp-turn-speaker"
          aria-label={`改人 ${seg.segment_id}`}
          defaultValue=""
          disabled={busy}
          onChange={(e) => e.target.value && void reassign(e.target.value)}
        >
          <option value="" disabled>改人…</option>
          {people.map((p) => (
            <option key={p.person_id} value={p.person_id}>{p.display_name}</option>
          ))}
        </select>
      </div>

      {editing ? (
        <div className="vp-turn-edit">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy} rows={2} />
          <div className="vp-turn-edit-actions">
            <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
              {busy ? <span className="spinner" aria-hidden /> : null}保存
            </button>
            <button type="button" className="ghost" disabled={busy} onClick={() => setEditing(false)}>取消</button>
          </div>
        </div>
      ) : (
        <p className="vp-turn-text" onClick={onPlay}>{text}</p>
      )}
      {error ? <p className="vp-error" role="alert">{error}</p> : null}
    </article>
  );
}
