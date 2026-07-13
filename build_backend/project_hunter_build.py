"""Project Hunter build backend."""

from __future__ import annotations

from typing import Any

from hatchling import build as _hatchling_build

build_sdist = _hatchling_build.build_sdist
build_wheel = _hatchling_build.build_wheel


def get_requires_for_build_sdist(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def get_requires_for_build_editable(config_settings: dict[str, Any] | None = None) -> list[str]:
    return []


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return _hatchling_build.build_wheel(wheel_directory, config_settings, metadata_directory)
