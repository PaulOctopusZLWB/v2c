import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { EmotionDistribution } from "../../api/types";
import { emotionColor, emotionMeta } from "../../lib/emotionColors";

// recharts is the heavy dependency; lazy-import the donut so it code-splits into its own chunk.
// The textual legend + per-speaker chips render without recharts (and stay assertable in jsdom).
const EmotionDonut = lazy(() => import("./EmotionRecharts").then((m) => ({ default: m.EmotionDonut })));

/** Sort emotion entries by count desc (ties by label) for stable legend/chip order. */
function sortedEntries(counts: Record<string, number>): Array<[string, number]> {
  return Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** A label that is a 非发言人 (噪音/多人) bucket, not a real speaker — excluded from emotion stats
 *  so noise can't masquerade as a speaker. Literal "噪音/多人" is a fallback when no set is wired. */
function isNoiseLabel(label: string, nonSpeakerLabels?: Set<string>): boolean {
  return label === "噪音/多人" || (nonSpeakerLabels?.has(label) ?? false);
}

/** Drop 非发言人 rows from per_speaker and recompute overall+n from the survivors, so the donut
 *  reflects only real speakers. Returns the distribution unchanged when nothing is filtered. */
function withoutNoise(data: EmotionDistribution, nonSpeakerLabels?: Set<string>): EmotionDistribution {
  const perSpeaker = data.per_speaker ?? [];
  const kept = perSpeaker.filter((sp) => !isNoiseLabel(sp.label, nonSpeakerLabels));
  if (kept.length === perSpeaker.length) return data;
  const overall: Record<string, number> = {};
  let n = 0;
  for (const sp of kept) {
    for (const [emo, count] of Object.entries(sp.emotions)) {
      overall[emo] = (overall[emo] ?? 0) + count;
      n += count;
    }
  }
  return { overall, per_speaker: kept, n };
}

/**
 * 情绪 — per-segment acoustic-emotion dashboard for a session: an overall emotion-distribution
 * donut + legend, and per-speaker emotion profiles (mix + dominant). Fetches
 * /api/emotions/distribution on session change. When nothing is extracted yet, surfaces an
 * "提取情绪" trigger that kicks off the background pass and polls status until it completes.
 */
export function EmotionCharts({
  sessionId,
  nonSpeakerLabels
}: {
  sessionId: string | null;
  /** Labels that are 非发言人 (噪音/多人) buckets — excluded so noise isn't an emotion "speaker". */
  nonSpeakerLabels?: Set<string>;
}) {
  const [data, setData] = useState<EmotionDistribution | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);

  const load = useCallback((id: string, signal: { cancelled: boolean }) => {
    setLoading(true);
    setError(null);
    return api
      .emotionDistribution({ session_id: id })
      .then((d) => {
        if (!signal.cancelled) setData(d);
      })
      .catch((e) => {
        if (!signal.cancelled) setError(e instanceof Error ? e.message : "加载情绪分布失败");
      })
      .finally(() => {
        if (!signal.cancelled) setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setData(null);
      setError(null);
      return;
    }
    const signal = { cancelled: false };
    void load(sessionId, signal);
    return () => {
      signal.cancelled = true;
    };
  }, [sessionId, load]);

  // Kick off extraction, poll status until it finishes, then refetch the distribution.
  const extract = useCallback(async () => {
    if (!sessionId || extracting) return;
    setExtracting(true);
    setError(null);
    try {
      await api.extractEmotions({ session_id: sessionId });
      // Poll status until no segments remain pending (or we give up after a bounded number of ticks).
      for (let i = 0; i < 600; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const status = await api.emotionStatus({ session_id: sessionId });
        if (status.pending === 0) break;
      }
      await load(sessionId, { cancelled: false });
    } catch (e) {
      setError(e instanceof Error ? e.message : "提取情绪失败");
    } finally {
      setExtracting(false);
    }
  }, [sessionId, extracting, load]);

  if (!sessionId) return null;

  // Exclude 非发言人 (噪音/多人) from the donut + per-speaker rows so noise isn't a "speaker".
  const cleaned = data ? withoutNoise(data, nonSpeakerLabels) : null;

  return (
    <section className="emotion">
      <div className="section-title">情绪</div>
      {loading && !data ? (
        <div className="emo-card emo-overlay">正在分析情绪…</div>
      ) : error ? (
        <div className="emo-card emo-overlay error" role="alert">
          情绪加载失败：{error}
        </div>
      ) : !cleaned || !cleaned.n || !cleaned.overall ? (
        <div className="emo-card emo-overlay emo-empty">
          <p>未提取情绪 — 在上方点「提取情绪」</p>
          <button type="button" className="primary" onClick={() => void extract()} disabled={extracting} aria-busy={extracting}>
            {extracting ? <span className="spinner" aria-hidden /> : null}
            {extracting ? "提取中…" : "提取情绪"}
          </button>
        </div>
      ) : (
        <EmotionBody data={cleaned} />
      )}
    </section>
  );
}

function EmotionBody({ data }: { data: EmotionDistribution }) {
  const overall = sortedEntries(data.overall);
  const slices = overall.map(([label, count]) => ({ label, count }));

  return (
    <div className="emo-grid">
      {/* 1. 情绪分布 — overall donut + legend with emoji short labels + counts. */}
      <div className="emo-card">
        <h4 className="emo-title">情绪分布</h4>
        <div className="emo-donut-row">
          <div className="emo-donut">
            <Suspense fallback={<div className="emo-chart-fallback" />}>
              <EmotionDonut slices={slices} />
            </Suspense>
            <div className="emo-donut-center">
              <strong className="num">{data.n}</strong>
              <span>段</span>
            </div>
          </div>
          <ul className="emo-legend" aria-label="情绪分布图例">
            {overall.map(([label, count]) => {
              const meta = emotionMeta(label);
              return (
                <li key={label} className="emo-legend-item">
                  <span className="emo-swatch" style={{ background: meta.color }} />
                  <span className="emo-legend-emoji" aria-hidden>{meta.emoji}</span>
                  <span className="emo-legend-label">{meta.zh}</span>
                  <span className="emo-legend-count num">{count}</span>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      {/* 2. 各发言人情绪 — per speaker: a stacked emotion bar + their dominant emotion. */}
      <div className="emo-card emo-card-wide">
        <h4 className="emo-title">各发言人情绪</h4>
        <ul className="emo-speakers" aria-label="各发言人情绪">
          {data.per_speaker.map((sp) => {
            const dom = emotionMeta(sp.dominant);
            const mix = sortedEntries(sp.emotions);
            return (
              <li key={sp.label} className="emo-speaker-row">
                <span className="emo-speaker-name">{sp.label}</span>
                <div className="emo-bar" role="img" aria-label={`${sp.label} 情绪占比`}>
                  {mix.map(([label, count]) => (
                    <span
                      key={label}
                      className="emo-bar-seg"
                      style={{ background: emotionColor(label), width: `${(count / sp.total) * 100}%` }}
                      title={`${emotionMeta(label).zh} ×${count}`}
                    />
                  ))}
                </div>
                <span className="emo-chip" style={{ borderColor: dom.color }} title="主要情绪">
                  <span aria-hidden>{dom.emoji}</span> {dom.zh}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
