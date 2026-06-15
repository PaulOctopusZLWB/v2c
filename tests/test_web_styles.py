from pathlib import Path


def test_web_styles_define_responsive_breakpoints() -> None:
    css = Path("web/src/styles.css").read_text(encoding="utf-8")

    assert "@media (max-width: 1100px)" in css
    assert "grid-template-areas" in css
    assert "@media (max-width: 700px)" in css
