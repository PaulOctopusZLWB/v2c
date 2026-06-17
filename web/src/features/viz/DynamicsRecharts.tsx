// Recharts wrappers, kept in a separate module so DynamicsCharts can lazy-import them (they
// pull in recharts, the only heavy chart dependency — this lets it code-split into its own
// chunk). Each export is a small, self-contained chart over the dynamics aggregates.
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { DynamicsSpeaker } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { clock } from "../../lib/format";

/** Talk-share donut (PieChart) coloured per speaker via speakerColor. */
export function DonutChart({ speakers }: { speakers: DynamicsSpeaker[] }) {
  const data = speakers.map((s) => ({ name: s.label, value: s.talk_ms, share: s.talk_share }));
  return (
    <ResponsiveContainer width="100%" height={180}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={52} outerRadius={78} paddingAngle={2} stroke="none">
          {data.map((d) => (
            <Cell key={d.name} fill={speakerColor(d.name)} />
          ))}
        </Pie>
        <Tooltip
          formatter={(value, _name, item) => {
            const payload = (item?.payload ?? {}) as { name?: string; share?: number };
            return [`${clock(Number(value))} · ${Math.round((payload.share ?? 0) * 100)}%`, payload.name ?? ""];
          }}
          contentStyle={TOOLTIP_STYLE}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

/** Horizontal bar of turns per speaker; tooltip surfaces talk-time + avg segment length. */
export function TurnsBar({ speakers }: { speakers: DynamicsSpeaker[] }) {
  const data = speakers.map((s) => ({
    name: s.label,
    turns: s.turns,
    talk: s.talk_ms,
    avg: s.avg_segment_ms
  }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(120, data.length * 46)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
        <XAxis type="number" allowDecimals={false} tick={AXIS_TICK} stroke="#2b3b52" />
        <YAxis type="category" dataKey="name" width={64} tick={AXIS_TICK} stroke="#2b3b52" />
        <Tooltip
          cursor={{ fill: "rgba(45,212,238,0.06)" }}
          formatter={(_value, _name, item) => {
            const p = (item?.payload ?? {}) as { turns?: number; talk?: number; avg?: number };
            return [`${p.turns ?? 0} 轮 · ${clock(p.talk ?? 0)} · 均段 ${clock(p.avg ?? 0)}`, ""];
          }}
          contentStyle={TOOLTIP_STYLE}
        />
        <Bar dataKey="turns" radius={[0, 4, 4, 0]} barSize={18}>
          {data.map((d) => (
            <Cell key={d.name} fill={speakerColor(d.name)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

const TOOLTIP_STYLE = {
  background: "#131c2b",
  border: "1px solid #2b3b52",
  borderRadius: 8,
  color: "#e6edf6",
  fontSize: 12
} as const;

const AXIS_TICK = { fill: "#aab8c9", fontSize: 11 } as const;
