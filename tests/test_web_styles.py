from pathlib import Path


def test_web_styles_define_responsive_breakpoints() -> None:
    css = Path("web/src/styles.css").read_text(encoding="utf-8")

    # The tabbed workspace lays out each page with multi-column grids that must collapse to a
    # single column on narrow screens. Assert the responsive breakpoints + the collapse rule.
    assert "@media (max-width: 900px)" in css
    assert "@media (max-width: 700px)" in css
    assert "grid-template-columns: 1fr" in css


def test_voiceprint_select_toolbar_floats_without_resizing_stage() -> None:
    css = Path("web/src/styles.css").read_text(encoding="utf-8")

    assert "grid-template-rows: auto minmax(0, 1fr)" in css
    assert "grid-template-rows: auto auto minmax(0, 1fr)" not in css
    assert ".voiceprint-map.has-select-toolbar .vmap-stage" not in css
    assert ".vmap-select-toolbar .ui-select" in css
    assert ".vmap-select-toolbar {\n  position: absolute;" in css
    assert ".speakers-main-proj > .speakers-map .vmap-stage { height: auto; min-height: 0; }" in css


def test_voiceprint_compact_workflow_keeps_touch_height() -> None:
    css = Path("web/src/styles.css").read_text(encoding="utf-8")

    assert ".voiceprint-workflow-rail" in css
    assert "min-height: calc(var(--control-h-sm) + var(--s3) * 2)" in css
    assert "overflow-x: auto; overflow-y: hidden" in css
