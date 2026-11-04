import dataclasses

import pytest


def test_phone_cast_lan_telegram_license_and_updater_disabled():
    from core.security.features import DEFAULT_FEATURES
    for name in ("phone", "cast", "lan_messaging", "telegram",
                 "license_server", "auto_update"):
        assert DEFAULT_FEATURES.enabled(name) is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_FEATURES.phone = True

def test_study_work_tools_enabled():
    from core.security.features import DEFAULT_FEATURES
    for name in ("web_search", "voice_enrollment", "gaming"):
        assert DEFAULT_FEATURES.enabled(name) is True

def test_unknown_feature_is_disabled():
    from core.security.features import DEFAULT_FEATURES
    assert DEFAULT_FEATURES.enabled("does_not_exist") is False
    assert DEFAULT_FEATURES.enabled("enabled") is False
    assert DEFAULT_FEATURES.enabled("__class__") is False
    assert DEFAULT_FEATURES.enabled("__dataclass_fields__") is False

def test_test_mode_is_on_in_tests():
    import core.config as config
    assert config.TEST_MODE is True
