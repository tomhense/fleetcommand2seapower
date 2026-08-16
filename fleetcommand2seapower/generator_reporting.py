"""Warnings and conversion quality reporting."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

def warning_category(message: str) -> str:
    if message.startswith("no Sea Power definition"):
        return "unmapped_unit"
    if message.startswith("ambiguous mapping"):
        return "ambiguous_mapping"
    if message.startswith("attached aircraft"):
        return "aircraft_attachment"
    if message.startswith("goal "):
        return "goal_approximation"
    if message.startswith("Fleet Command alliance"):
        return "alliance_declaration"
    if "air corridor" in message:
        return "air_corridor"
    if "fallback" in message:
        return "fallback_mapping"
    return "other"


def quality_entry(source: Path, output: Path, data: dict[str, Any], rendered: str,
                  warnings: list[str]) -> dict[str, Any]:
    source_counts = {
        key: len(value) if isinstance(value, list) else 0
        for key, value in data.items() if key in {"aircraft", "vessels", "land_units", "air_stations", "air_corridors", "triggers_goals"}
    }
    generated_sections = re.findall(r"^\[((?:Taskforce|Neutral)[^]]+)\]$", rendered, re.MULTILINE)
    generated_units = [section for section in generated_sections
                       if any(token in section for token in ("Vessel", "Submarine", "Aircraft", "Helicopter", "LandUnit", "Biologic"))]
    categories: dict[str, int] = {}
    for warning in warnings:
        category = warning_category(warning)
        categories[category] = categories.get(category, 0) + 1
    return {
        "source": source.name,
        "output": output.name,
        "status": "generated",
        "source_units": source_counts,
        "generated_units": len(generated_units),
        "warnings": warnings,
        "warning_categories": categories,
    }
