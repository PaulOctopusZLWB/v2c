from __future__ import annotations

from pathlib import Path

from personal_context_node.adapters.file_import.local_directory import LocalDirectoryFileImportAdapter
from personal_context_node.config import AppConfig
from personal_context_node.ingest import imported_source_snapshots_in_conn
from personal_context_node.storage.sqlite import connect, initialize


def _adapter(config: AppConfig) -> LocalDirectoryFileImportAdapter:
    return LocalDirectoryFileImportAdapter(
        device_roots=[config.dji_mic_3.root_path] if config.dji_mic_3.root_path else [],
        device_label=config.source_device,
        volume_name_patterns=config.dji_mic_3.volume_name_patterns,
        volume_root=config.dji_mic_3.volume_root,
    )


def discover_import_sources(*, config: AppConfig) -> list[dict[str, object]]:
    """Detected recorder volumes (kind='device') plus configured known sources
    (kind='known'), each with an audio_count. The frontend renders these as
    one-click import cards — no typed paths."""
    adapter = _adapter(config)
    sources: list[dict[str, object]] = []
    seen: set[str] = set()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        # initialize() may apply migrations on the first direct discovery call.
        conn.commit()
        imported = imported_source_snapshots_in_conn(conn)
        for device in adapter.discover_devices():
            root = str(device.root_path)
            seen.add(root)
            device_files = adapter.discover_audio_files(device)
            sources.append(
                {
                    "kind": "device",
                    "device_id": device.device_id,
                    "label": device.label,
                    "root_path": root,
                    "audio_count": sum(
                        1
                        for source in device_files
                        if (str(source.source_path), source.size_bytes, source.mtime_ns) not in imported
                    ),
                }
            )
        # Known source: the configured device root (e.g. dji_mic_3.root_path), shown even
        # when not currently mounted as a matching volume, so there is always something to pick.
        known_root = config.dji_mic_3.root_path
        if known_root is not None and str(known_root) not in seen and known_root.exists():
            from personal_context_node.ingest import scan_audio_files

            known_files = scan_audio_files(source_dir=known_root).files
            sources.append(
                {
                    "kind": "known",
                    "device_id": str(known_root),
                    "label": config.source_device,
                    "root_path": str(known_root),
                    "audio_count": sum(
                        1
                        for path in known_files
                        if _path_snapshot(path) not in imported
                    ),
                }
            )
    finally:
        conn.close()
    return sources


def _path_snapshot(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns)
