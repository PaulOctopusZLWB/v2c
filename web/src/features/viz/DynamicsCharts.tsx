import { lazy, Suspense, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { SessionDynamics } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { clock } from "../../lib/format";

// recharts is the only heavy dependency in this bundle; lazy-import the chart wrappers so they
// code-split into their own chunk and don't weigh down the initial load. The textual legend,
// timeline, and transitions render without recharts (and stay assertable in jsdom).
const DonutChart = lazy(() => import("./DynamicsRecharts").then((m) => ({ default: m.DonutChart })));
const TurnsBar = lazy(() => import("./DynamicsRecharts").then((m) => ({ default: m.TurnsBar })));

/** "M分钟" or "M.m分钟" for a ms span (one decimal when not whole). */
function minutesLabel(ms: number): string {
  const m = ms / 60000;
  const rounded = Math.round(m * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}分钟`;
}

/** A label that is a 非发言人 (噪音/多人) bucket, not a real speaker — drop it from analytics so
 *  noise can't masquerade as a real speaker. Resolved by the parent-supplied set, with a literal
 *  "噪音/多人" name as a pragmatic fallback when no set is wired. */
function isNoiseLabel(label: string, nonSpeakerLabels?: Set<string>): boolean {
  return label === "噪音/多人" || (nonSpeakerLabels?.has(label) ?? false);
}

/**
 * 对话动态 — per-session conversation-dynamics dashboard: a talk-time donut, a turns bar, a
 * who-spoke-when timeline, and a turn-taking (接力) list. Fetches /api/sessions/{id}/dynamics
 * on session change; recharts charts are lazy-loaded so they code-split.
 */
export function DynamicsCharts({
  sessionId,
  nonSpeakerLabels
}: {
  sessionId: string | null;
  /** Labels that are 非发言人 (噪音/多人) buckets — filtered out so noise isn't a "speaker". */
  nonSpeakerLabels?: Set<string>;
}) {
  const [data, setData] = useState<SessionDynamics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setData(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .sessionDynamics(sessionId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "加载对话动态失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (!sessionId) return null;

  // Drop 非发言人 (噪音/多人) labels from every facet so noise never appears as a real speaker.
  const cleaned = data
    ? {
        ...data,
        speakers: (data.speakers ?? []).filter((s) => !isNoiseLabel(s.label, nonSpeakerLabels)),
        transitions: (data.transitions ?? []).filter(
          (t) => !isNoiseLabel(t.from, nonSpeakerLabels) && !isNoiseLabel(t.to, nonSpeakerLabels)
        ),
        timeline: (data.timeline ?? []).filter((t) => !isNoiseLabel(t.label, nonSpeakerLabels))
      }
    : null;

  return (
    <section className="dynamics">
      <div className="section-title">对话动态</div>
      {loading && !data ? (
        <div className="dyn-card dyn-overlay">正在分析对话动态…</div>
      ) : error ? (
        <div className="dyn-card dyn-overlay error" role="alert">
          对话动态加载失败：{error}
        </div>
      ) : !cleaned || !cleaned.speakers || cleaned.speakers.length === 0 ? (
        <div className="dyn-card dyn-overlay">该会话还没有对话动态可分析。</div>
      ) : (
        <DynamicsBody data={cleaned} />
      )}
    </section>
  );
}

function DynamicsBody({ data }: { data: SessionDynamics }) {
  const { speakers, total_ms } = data;
  const transitions = data.transitions ?? [];
  const timeline = data.timeline ?? [];
  const lanes = speakers.map((s) => s.label);

  return (
    <div className="dyn-grid">
      {/* 1. 发言占比 — talk-share donut + legend with percentages, center "N人 · M分钟". */}
      <div className="dyn-card">
        <h4 className="dyn-title">发言占比</h4>
        <div className="dyn-donut-row">
          <div className="dyn-donut">
            <Suspense fallback={<div className="dyn-chart-fallback" />}>
              <DonutChart speakers={speakers} />
            </Suspense>
            <div className="dyn-donut-center">
              <strong>{speakers.length}人</strong>
              <span>{minutesLabel(total_ms)}</span>
            </div>
          </div>
          <ul className="dyn-legend" aria-label="发言占比图例">
            {speakers.map((s) => (
              <li key={s.label} className="dyn-legend-item">
                <span className="dyn-swatch" style={{ background: speakerColor(s.label), color: speakerColor(s.label) }} />
                <span className="dyn-legend-label">{s.label}</span>
                <span className="dyn-legend-pct">{Math.round(s.talk_share * 100)}%</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* 2. 话轮 — horizontal bar of turns per speaker; tooltip carries talk-time + avg seg. */}
      <div className="dyn-card">
        <h4 className="dyn-title">话轮</h4>
        <Suspense fallback={<div className="dyn-chart-fallback" />}>
          <TurnsBar speakers={speakers} />
        </Suspense>
      </div>

      {/* 3. 对话时间线 — one lane per speaker, colored blocks positioned by start/width %. */}
      <div className="dyn-card dyn-card-wide">
        <h4 className="dyn-title">对话时间线</h4>
        <div className="dyn-timeline">
          {lanes.map((label) => (
            <div key={label} className="dyn-lane" data-speaker={label}>
              <span className="dyn-lane-label">{label}</span>
              <div className="dyn-lane-track">
                {timeline
                  .filter((turn) => turn.label === label)
                  .map((turn, i) => {
                    const left = total_ms > 0 ? (turn.start_ms_rel / total_ms) * 100 : 0;
                    const width = total_ms > 0 ? ((turn.end_ms_rel - turn.start_ms_rel) / total_ms) * 100 : 0;
                    return (
                      <span
                        key={`${label}-${i}`}
                        className="dyn-block"
                        style={{ left: `${left}%`, width: `${width}%`, background: speakerColor(label) }}
                        title={`${label} · ${clock(turn.start_ms_rel)}–${clock(turn.end_ms_rel)}`}
                      />
                    );
                  })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 4. 话轮接力 — top turn-taking transitions ("A → B ×8"). */}
      <div className="dyn-card">
        <h4 className="dyn-title">话轮接力</h4>
        {transitions.length === 0 ? (
          <p className="dim dyn-empty-note">没有可统计的话轮切换。</p>
        ) : (
          <ul className="dyn-transitions" aria-label="话轮接力">
            {transitions.slice(0, 6).map((tr) => (
              <li key={`${tr.from}->${tr.to}`} className="dyn-transition">
                <span className="dyn-swatch" style={{ background: speakerColor(tr.from), color: speakerColor(tr.from) }} />
                <span className="dyn-transition-text">
                  {tr.from} <span className="dyn-arrow">→</span> {tr.to}
                </span>
                <span className="dyn-transition-count">×{tr.count}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
