from pathlib import Path


def test_web_styles_define_responsive_breakpoints() -> None:
    css = Path("web/src/styles.css").read_text(encoding="utf-8")

    # The tabbed workspace lays out each page with multi-column grids that must collapse to a
    # single column on narrow screens. Assert the responsive breakpoints + the collapse rule.
    assert "@media (max-width: 900px)" in css
    assert "@media (max-width: 700px)" in css
    assert "grid-template-columns: 1fr" in css
