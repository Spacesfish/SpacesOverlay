import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, MutableMapping, Optional, Type, TypedDict, TypeVar

import toml

PLACEHOLDER_API_KEY = "insert-your-key-here"


logger = logging.getLogger()


class SettingsDict(TypedDict):
    """Complete dict of settings"""

    hypixel_api_key: str


# Generic type to allow subclassing Settings
DerivedSettings = TypeVar("DerivedSettings", bound="Settings")


@dataclass
class Settings:
    """Class holding user settings for the application"""

    hypixel_api_key: str
    path: Path

    @classmethod
    def from_dict(
        cls: Type[DerivedSettings], source: SettingsDict, path: Path
    ) -> DerivedSettings:
        return cls(hypixel_api_key=source["hypixel_api_key"], path=path)

    def to_dict(self) -> SettingsDict:
        return {"hypixel_api_key": self.hypixel_api_key}

    def flush_to_disk(self) -> None:
        with self.path.open("w") as f:
            toml.dump(self.to_dict(), f)


# Generic type for value_or_default
ValueType = TypeVar("ValueType")


def value_or_default(value: Optional[ValueType], *, default: ValueType) -> ValueType:
    return value if value is not None else default


def api_key_is_valid(key: str) -> bool:
    """Return True if given key is a valid Hypixel API key"""
    # Very permissive validity checks - no guarantee for validity
    return key != PLACEHOLDER_API_KEY and len(key) > 5


def read_settings(path: Path) -> MutableMapping[str, object]:
    return toml.load(path)


def fill_missing_settings(
    incomplete_settings: MutableMapping[str, object], get_api_key: Callable[[], str]
) -> SettingsDict:
    api_key = incomplete_settings.get("hypixel_api_key", None)
    if not isinstance(api_key, str) or not api_key_is_valid(api_key):
        api_key = get_api_key()

    return {"hypixel_api_key": api_key}


def get_settings(path: Path, get_api_key: Callable[[], str]) -> Settings:
    """
    Read the stored settings into a Settings object

    Calls get_api_key if it is missing
    NOTE: Will write to the path if the API key is missing
    """
    try:
        incomplete_settings = read_settings(path)
    except Exception as e:
        # Error either in reading or parsing file
        incomplete_settings = {}
        logger.warning(f"Error reading settings file, using all defaults. '{e}'")

    if "hypixel_api_key" not in incomplete_settings:
        # To help users in setting a valid key, add a placeholder and flush to disk
        incomplete_settings["hypixel_api_key"] = PLACEHOLDER_API_KEY
        with path.open("w") as f:
            toml.dump(incomplete_settings, f)

    settings_dict = fill_missing_settings(incomplete_settings, get_api_key)

    return Settings.from_dict(settings_dict, path=path)