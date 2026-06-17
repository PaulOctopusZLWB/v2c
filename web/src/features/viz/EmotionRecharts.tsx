// Recharts donut for the overall emotion distribution, kept in a separate module so
// EmotionCharts can lazy-import it (it pulls in recharts, the heavy chart dependency — this
// lets it code-split into its own chunk). The textual legend renders without recharts.
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { emotionColor, emotionMeta } from "../../lib/emotionColors";

/** One overall-emotion slice for the donut. */
export interface EmotionSlice {
  label: string;
  count: number;
}

/** Emotion-distribution donut coloured per class via emotionColor. */
export function EmotionDonut({ slices }: { slices: EmotionSlice[] }) {
  const data = slices.map((s) => ({ name: s.label, value: s.count }));
  return (
    <ResponsiveContainer width="100%" height={180}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={52} outerRadius={78} paddingAngle={2} stroke="none">
          {data.map((d) => (
            <Cell key={d.name} fill={emotionColor(d.name)} />
          ))}
        </Pie>
        <Tooltip
          formatter={(value, _name, item) => {
            const payload = (item?.payload ?? {}) as { name?: string };
            const meta = emotionMeta(payload.name ?? "");
            return [`${value} 段`, `${meta.emoji} ${meta.zh}`];
          }}
          contentStyle={TOOLTIP_STYLE}
        />
      </PieChart>
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
