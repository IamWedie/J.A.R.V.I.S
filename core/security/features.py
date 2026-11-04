from dataclasses import dataclass, fields

@dataclass(frozen=True)
class Features:
    web_search: bool = True
    voice_enrollment: bool = True
    gaming: bool = True
    phone: bool = False
    cast: bool = False
    lan_messaging: bool = False
    telegram: bool = False
    license_server: bool = False
    auto_update: bool = False

    def enabled(self, name: str) -> bool:
        if name not in _FEATURE_NAMES:
            return False
        return bool(getattr(self, name))


_FEATURE_NAMES = frozenset(f.name for f in fields(Features))

DEFAULT_FEATURES = Features()
