def test_language_switch_reapplies_english():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "ui" / "app.js").read_text(encoding="utf-8")
    assert "applyTranslations" in js
    assert "currentLang = 'en'" in js or 'currentLang = "en"' in js
    assert "location.reload()" not in js.split("/* === LANGUAGE === */")[1].split("/* === INIT === */")[0]


def test_personal_wizard_has_no_license_step():
    from pathlib import Path
    html = (Path(__file__).parents[2] / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="wizStep0"' not in html
    assert "license_needed" not in html


def test_grants_panel_exists():
    from pathlib import Path
    html = (Path(__file__).parents[2] / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="grantsList"' in html
    assert 'id="recentAuditList"' in html
    assert 'id="auditResetBtn"' in html
