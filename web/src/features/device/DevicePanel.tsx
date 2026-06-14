import type { ImportSource } from "../../api/types";
import { t } from "../../i18n";
import { useAsyncAction } from "../../hooks/useAsyncAction";

function DeviceCard({ source, onImport }: { source: ImportSource; onImport: (rootPath: string) => Promise<unknown> | void }) {
  const imp = useAsyncAction(async (root: string) => { await onImport(root); });
  return (
    <div className={`device-card ${source.kind}`}>
      <span className={source.kind === "device" ? "live" : "dim"}>
        {source.kind === "device" ? `● ${t.device.detected}` : `○ ${t.device.known}`}
      </span>
      <strong>{source.label}</strong>
      <code className="path">{source.root_path}</code>
      <span className="num">{source.audio_count} {t.device.newAudio}</span>
      <button onClick={() => void imp.run(source.root_path)} disabled={imp.pending} aria-busy={imp.pending}>
        {imp.pending ? "正在导入…" : t.device.import}
      </button>
    </div>
  );
}

export function DevicePanel({
  sources,
  onImport,
  onRefresh
}: {
  sources: ImportSource[];
  onImport: (rootPath: string) => Promise<unknown> | void;
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
        <DeviceCard key={s.device_id} source={s} onImport={onImport} />
      ))}
    </section>
  );
}
