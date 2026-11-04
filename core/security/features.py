from dataclasses import dataclass

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
        return bool(getattr(self, name, False))


DEFAULT_FEATURES = Features()
