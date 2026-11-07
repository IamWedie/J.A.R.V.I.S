from pathlib import Path


def read(name):
    return (Path(__file__).parents[2] / "ui" / name).read_text(encoding="utf-8")


def test_approval_response_is_request_bound():
    js = read("app.js")
    assert "currentApproval" in js
    assert "request_id" in js
    assert "approval_response" in js
    assert "decision" in js
    assert "allow_always" in js
    assert "cmd: 'approval_response'" in js or 'cmd: "approval_response"' in js or "'cmd': 'approval_response'" in js or '"cmd": "approval_response"' in js
    assert "request_id: requestId" in js
    assert "currentApproval.request_id" in js
    assert "cmd: 'approval'" not in js
    assert 'cmd: "approval"' not in js


def test_approval_dialog_shows_risk_and_capabilities():
    html = read("index.html")
    assert 'id="approvalRisk"' in html
    assert 'id="approvalCaps"' in html
    assert 'id="approvalScope"' in html
    assert 'id="btnAllowAlways"' in html
    assert 'id="approvalRequestId"' in html
    assert 'role="alertdialog"' in html
    assert 'aria-modal="true"' in html
    assert 'id="approvalScope" class="approvalScope hidden"' in html
    assert 'id="btnAllowAlways" class="allowAlways hidden"' in html


def test_update_controls_are_removed():
    html = read("index.html")
    js = read("app.js")
    assert 'id="updateBtn"' not in html
    assert "update_download" not in js
    assert "update_available" not in js
    assert "update_progress" not in js
    assert "getElementById('updateBtn')" not in js
    assert 'getElementById("updateBtn")' not in js
