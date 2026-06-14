from __future__ import annotations

from personal_context_node.adapters.file_import.local_directory import LocalDirectoryFileImportAdapter
from personal_context_node.config import AppConfig


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
    for device in adapter.discover_devices():
        root = str(device.root_path)
        seen.add(root)
        sources.append(
            {
                "kind": "device",
                "device_id": device.device_id,
                "label": device.label,
                "root_path": root,
                "audio_count": len(adapter.discover_audio_files(device)),
            }
        )
    # Known source: the configured device root (e.g. dji_mic_3.root_path), shown even
    # when not currently mounted as a matching volume, so there is always something to pick.
    known_root = config.dji_mic_3.root_path
    if known_root is not None and str(known_root) not in seen and known_root.exists():
        from personal_context_node.ingest import scan_audio_files

        sources.append(
            {
                "kind": "known",
                "device_id": str(known_root),
                "label": config.source_device,
                "root_path": str(known_root),
                "audio_count": len(scan_audio_files(source_dir=known_root).files),
            }
        )
    return sources
