"""Liebherr HomeAPI models."""

from dataclasses import dataclass


@dataclass
class TemperatureControlRequest:
    zoneId: int
    target: int
    unit: str  # '°C' or '°F'


@dataclass
class ZoneToggleControlRequest:
    zoneId: int
    value: bool


@dataclass
class BaseToggleControlRequest:
    value: bool


@dataclass
class ModeZoneControlRequest:
    zoneId: int
    mode: str


@dataclass
class ModeControlRequest:
    mode: str
