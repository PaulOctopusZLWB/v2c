import type { ImportSource } from "../../api/types";
import { t } from "../../i18n";

export function DevicePanel({
  sources,
  onImport,
  onRefresh
}: {
  sources: ImportSource[];
  onImport: (rootPath: string) => void;
  onRefresh: () => void;
}) {
  return (
    <section className="device-panel">
      <header>
        <h3>{t.nav.device}</h3>
        <button onClick={onRefresh}>↻ {t.device.refresh}</button>
      </header>
      {sources.length === 0 ? <p className="dim">{t.device.none}</p> : null}
      {sources.map((s) => (
        <div className={`device-card ${s.kind}`} key={s.device_id}>
          <span className={s.kind === "device" ? "live" : "dim"}>
            {s.kind === "device" ? `● ${t.device.detected}` : `○ ${t.device.known}`}
          </span>
          <strong>{s.label}</strong>
          <code className="path">{s.root_path}</code>
          <span className="num">{s.audio_count} {t.device.newAudio}</span>
          <button onClick={() => onImport(s.root_path)}>{t.device.import}</button>
        </div>
      ))}
    </section>
  );
}
