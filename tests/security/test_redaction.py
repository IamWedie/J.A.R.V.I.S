"""Tests for log redaction."""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def test_api_key_is_redacted():
    from core.security.redaction import redact

    out = redact("key sk-abcdefgh12345")
    assert "sk-abcdefgh12345" not in out
    assert "[REDACTED]" in out


def test_license_key_is_redacted():
    from core.security.redaction import redact

    out = redact("license JARV-ABCD-1234-EFGH")
    assert "JARV-ABCD-1234-EFGH" not in out
    assert "[REDACTED]" in out


def test_pin_assignment_is_redacted():
    from core.security.redaction import redact

    out1 = redact("JARVIS_PIN=123456")
    assert "[REDACTED]" in out1
    assert "123456" not in out1
    out2 = redact('"pin": "1234"')
    assert "[REDACTED]" in out2
    assert "1234" not in out2
    assert redact("spin the wheel") == "spin the wheel"


def test_log_file_does_not_contain_secret(tmp_path):
    import core.logging_setup as logging_setup

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        logging_setup._configured = False
        logging_setup.setup_logging(log_dir=str(tmp_path))
        logging.getLogger("jarvis.test").info("token sk-supersecret12345 done")
        for handler in root.handlers:
            try:
                handler.flush()
            except Exception:
                pass
        log_path = tmp_path / "jarvis.log"
        content = log_path.read_text(encoding="utf-8")
        assert "sk-supersecret12345" not in content
        assert "[REDACTED]" in content
    finally:
        logging_setup._configured = False
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
