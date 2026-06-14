import type { ImportSource } from "../../api/types";
import { t } from "../../i18n";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { Icon } from "../../components/Icon";

function DeviceCard({ source, onImport }: { source: ImportSource; onImport: (rootPath: string) => Promise<unknown> | void }) {
  const imp = useAsyncAction(async (root: string) => { await onImport(root); });
  const isDevice = source.kind === "device";
  return (
    <div className={`device-card ${source.kind}`}>
      <span className={`dev-head ${isDevice ? "live" : "dim"}`}>
        <Icon name={isDevice ? "device" : "person"} />
        {isDevice ? t.device.detected : t.device.known}
      </span>
      <strong>{source.label}</strong>
      <code className="path">{source.root_path}</code>
      <span className="num dim">{source.audio_count} {t.device.newAudio}</span>
      <button
        className="primary"
        onClick={() => void imp.run(source.root_path)}
        disabled={imp.pending}
        aria-busy={imp.pending}
      >
        {imp.pending ? <span className="spinner" aria-hidden /> : <Icon name="import" />}
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
      <div className="section-title">
        <Icon name="device" /> {t.nav.device}
        <button className="icon-btn ghost" aria-label={t.device.refresh} title={t.device.refresh} onClick={onRefresh}>
          <Icon name="refresh" />
        </button>
      </div>
      {sources.length === 0 ? (
        <div className="empty">
          <Icon name="device" className="empty-icon" />
          <p>{t.device.none}</p>
        </div>
      ) : null}
      {sources.map((s) => (
        <DeviceCard key={s.device_id} source={s} onImport={onImport} />
      ))}
    </section>
  );
}
