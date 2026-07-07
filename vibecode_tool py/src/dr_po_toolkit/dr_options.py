from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DrFileOption:
    key: str
    name: str
    description: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.description})" if self.description else self.name


DR_FILE_OPTIONS: tuple[DrFileOption, ...] = (
    DrFileOption("e01", "E01", "Chapter 1"),
    DrFileOption("e02", "E02", "Chapter 2"),
    DrFileOption("e03", "E03", "Chapter 3"),
    DrFileOption("e04", "E04", "Chapter 4"),
    DrFileOption("e05", "E05", "Chapter 5"),
    DrFileOption("e06", "E06", "Chapter 6"),
    DrFileOption("e08", "E08", "Freetime"),
    DrFileOption("script_pak", "Script_pak", ""),
    DrFileOption("mtb", "mtb", ""),
    DrFileOption("system", "system", ""),
    DrFileOption("tga", "tga", ""),
)

DR_FILE_OPTION_KEYS: tuple[str, ...] = tuple(option.key for option in DR_FILE_OPTIONS)
DR_FILE_OPTION_BY_KEY: dict[str, DrFileOption] = {option.key: option for option in DR_FILE_OPTIONS}


def option_name(key: str) -> str:
    option = DR_FILE_OPTION_BY_KEY.get(key)
    return option.name if option is not None else key


def option_label(key: str) -> str:
    option = DR_FILE_OPTION_BY_KEY.get(key)
    return option.label if option is not None else key


def default_selected_options() -> list[str]:
    return list(DR_FILE_OPTION_KEYS)
