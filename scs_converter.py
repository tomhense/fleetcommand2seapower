#!/usr/bin/env python3
"""Parse Jane's Fleet Command .scs scenario files into JSON.

The parser deliberately treats the format as a tolerant, line-oriented format:
unknown fields are retained instead of making newer/older scenarios fail.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import sys
from pathlib import Path
from typing import Any


NUMBER = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
ALERT = re.compile(r"^Alert(?P<time>\d+)$", re.IGNORECASE)
ENTITY_TYPES = {"AIRENTITY", "SURFENTITY", "BUILDINGENTITY", "SUBENTITY", "LANDENTITY"}


def atom(value: str) -> Any:
    if value == "":
        return None
    if NUMBER.match(value):
        number = float(value)
        return int(number) if number.is_integer() else number
    return value


def one_or_many(values: list[Any]) -> Any:
    return values[0] if len(values) == 1 else values


def null_empty(value: Any) -> Any:
    """Use JSON null for absent/empty attributes, including nested values."""
    if value == "":
        return None
    if isinstance(value, list):
        return [null_empty(item) for item in value]
    if isinstance(value, dict):
        return {key: null_empty(item) for key, item in value.items()}
    return value


def clean_text(value: str) -> str:
    """Convert Fleet Command's paragraph markers and layout whitespace to plain text."""
    value = re.sub(r"<R>", "", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def fields(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        # A few legacy scenarios contain truncated/malformed quoted text.
        # Keep the line parseable rather than rejecting the whole scenario.
        return line.split()


class Converter:
    def __init__(self, lat: float, lon: float, origin_x: float, origin_y: float,
                 units_per_degree: float):
        self.lat, self.lon = lat, lon
        self.origin_x, self.origin_y = origin_x, origin_y
        self.units_per_degree = units_per_degree

    def point(self, x: str | float, y: str | float, z: str | float = 0) -> dict[str, Any]:
        x, y, z = float(x), float(y), float(z)
        # Equirectangular approximation. The longitude scale is corrected for
        # latitude, while the original game coordinates remain in the result.
        lat = self.lat + (y - self.origin_y) / self.units_per_degree
        lon = self.lon + (x - self.origin_x) / (self.units_per_degree * math.cos(math.radians(self.lat)))
        return {"latitude": round(lat, 8), "longitude": round(lon, 8), "altitude": z}


def text_block(lines: list[str], index: int) -> tuple[str, int]:
    out: list[str] = []
    index += 1
    while index < len(lines) and lines[index].strip() != "ENDTEXT":
        out.append(lines[index])
        index += 1
    return "\n".join(out), min(index + 1, len(lines))


def parse_aircraft_attachment(tokens: list[str]) -> dict[str, Any]:
    values = [atom(v) for v in tokens[1:]]
    entry: dict[str, Any] = {
        "name": values[0] if len(values) > 0 else None,
        "variant": values[1] if len(values) > 1 else None,
        "class": values[2] if len(values) > 2 else None,
        "country": values[3] if len(values) > 3 else None,
        "quantity": values[4] if len(values) > 4 else None,
        "alert_status": []
    }
    flags: list[Any] = []
    j = 5
    while j < len(values):
        match = ALERT.match(str(values[j]))
        if match and j + 1 < len(values):
            entry["alert_status"].append({"alert": values[j], "value": values[j + 1]})
            j += 2
        elif values[j] != ":":
            flags.append(values[j])
            j += 1
        else:
            j += 1
    if flags:
        entry["alert_flags"] = flags
    return entry


def parse_corridor(lines: list[str], index: int, converter: Converter) -> tuple[dict[str, Any], int]:
    tokens = fields(lines[index].strip())
    corridor = {
        "start": converter.point(tokens[1], tokens[2], 0),
        "end": converter.point(tokens[3], tokens[4], 0),
        "speed": atom(tokens[5]), "mode": atom(tokens[6]),
        "minimum_altitude": atom(tokens[7]), "maximum_altitude": atom(tokens[8]),
        "name": tokens[9] if len(tokens) > 9 else None,
        "aircraft": []
    }
    index += 1
    while index < len(lines) and lines[index].strip() != "END":
        nested = fields(lines[index].strip())
        if nested and nested[0] == "AIRCRAFT":
            corridor["aircraft"].append(parse_aircraft_attachment(nested))
        index += 1
    return corridor, min(index + 1, len(lines))


def parse_record(lines: list[str], index: int, converter: Converter, kind: str) -> tuple[dict[str, Any], int]:
    first = fields(lines[index])
    record: dict[str, Any] = {}
    if kind in {"entity", "nonentity", "airstation"}:
        record.update({"name": first[1], "entity_type": first[2] if len(first) > 2 else ""})
        if kind == "nonentity":
            record["nonentity"] = True
    else:
        record["position"] = converter.point(first[1], first[2], first[3] if len(first) > 3 else 0)
    index += 1
    text_fields = {"INTELMESSAGE", "DESCRIPTION", "TASKINGMESSAGE", "MISSIONTITLE",
                   "SUCCESSMESSAGE", "FAILUREMESSAGE"}
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        tokens = fields(line)
        key = tokens[0]
        if key == "END":
            return record, index + 1
        if key in text_fields and index + 1 < len(lines) and lines[index + 1].strip() == "BEGINTEXT":
            value, index = text_block(lines, index + 1)
            record[key.lower()] = clean_text(value)
            continue
        if key == "BEGINTEXT":
            value, index = text_block(lines, index)
            record.setdefault("message", clean_text(value))
            continue
        if key in {"POS", "GOAL"} and len(tokens) >= 3:
            record["position"] = converter.point(tokens[1], tokens[2], tokens[3] if len(tokens) > 3 else 0)
        elif key == "WAYPOINT" and len(tokens) >= 3:
            record.setdefault("waypoints", []).append(converter.point(tokens[1], tokens[2], tokens[3] if len(tokens) > 3 else 0) | {"speed": atom(tokens[4]) if len(tokens) > 4 else None})
        elif key == "BOX" and len(tokens) >= 5:
            record["box"] = {"corners": [converter.point(tokens[1], tokens[2]), converter.point(tokens[3], tokens[4])]}
        elif key == "RANDOMSTARTBOX" and len(tokens) >= 5:
            record["random_start_box"] = {"corners": [converter.point(tokens[1], tokens[2]), converter.point(tokens[3], tokens[4])]}
        elif key == "AIRCRAFT":
            record.setdefault("aircraft", []).append(parse_aircraft_attachment(tokens))
        elif key == "AIRCORRIDOR" and len(tokens) >= 10:
            corridor, index = parse_corridor(lines, index, converter)
            record.setdefault("air_corridors", []).append(corridor)
            continue
        elif len(tokens) > 1:
            value = [atom(v) for v in tokens[1:]]
            record[key.lower()] = one_or_many(value)
        index += 1
    return record, index


def resolve_alliances(result: dict[str, Any]) -> None:
    """Attach scenario-local alliance meaning without pretending IDs are global country codes."""
    roles: dict[int, str] = {}
    own = result.get("metadata", {}).get("ownalliance")
    if isinstance(own, (int, float)):
        roles[int(own)] = "friendly"
    for declaration in result.get("formations", []):
        kind = str(declaration.get("type", ""))
        role = {"ThreatAlliance": "enemy", "NeutralAlliance": "neutral",
                "AlliedAlliance": "friendly"}.get(kind)
        if role:
            for alliance_id in declaration.get("alliance_ids", []):
                if isinstance(alliance_id, (int, float)):
                    # OWNALLIANCE is authoritative if a scenario also lists it
                    # in an AlliedAlliance declaration.
                    roles.setdefault(int(alliance_id), role)

    countries: dict[int, set[str]] = {}
    for collection_name in ("aircraft", "land_units", "vessels", "air_stations"):
        for item in result.get(collection_name, []):
            alliance_id = item.get("alliance")
            if not isinstance(alliance_id, (int, float)):
                continue
            values = countries.setdefault(int(alliance_id), set())
            country = item.get("country")
            if isinstance(country, str) and country and country.upper() not in {"NEUTRALS", "NEUTRAL"}:
                values.add(country)
            for aircraft in item.get("aircraft", []):
                country = aircraft.get("country")
                if isinstance(country, str) and country and country.upper() not in {"NEUTRALS", "NEUTRAL"}:
                    values.add(country)

    alliance_table = []
    for alliance_id in sorted(set(roles) | set(countries)):
        names = sorted(countries.get(alliance_id, set()))
        entry = {
            "id": alliance_id,
            "role": roles.get(alliance_id, "unknown"),
            "countries": names,
            "country": names[0] if len(names) == 1 else None,
        }
        alliance_table.append(entry)
    result["alliances"] = alliance_table

    by_id = {entry["id"]: entry for entry in alliance_table}
    for collection_name in ("aircraft", "land_units", "vessels", "air_stations"):
        for item in result.get(collection_name, []):
            alliance_id = item.get("alliance")
            if isinstance(alliance_id, (int, float)) and int(alliance_id) in by_id:
                entry = by_id[int(alliance_id)]
                item["alliance_id"] = int(alliance_id)
                item["alliance_role"] = entry["role"]
                item["alliance_country"] = entry["country"]
                item["alliance_countries"] = entry["countries"]


def parse(path: Path, origin_x: float = 555600, origin_y: float = 555600,
          units_per_degree: float = 111120) -> dict[str, Any]:
    lines = path.read_text(encoding="latin-1").replace("\r\n", "\n").splitlines()
    header: dict[str, Any] = {}
    lat = lon = 0.0
    header_keys = {"VERSION", "OWNALLIANCE", "DIFFICULTY", "TOTALNUMSIDES", "LATLONG",
                   "SEASTATE", "MONTH", "BOTTOMTYPE", "TIMEOFDAY", "WEATHER",
                   "CLOUDHEIGHT", "SSP"}
    i = 0
    while i < len(lines):
        t = fields(lines[i].strip()) if lines[i].strip() else []
        if t and t[0] in {"ENTITY", "GOAL", "GROUP", "AlliedAlliance", "ThreatAlliance", "NeutralAlliance", "BITMAP"}:
            break
        if t and t[0] in header_keys and len(t) > 1:
            header[t[0].lower()] = one_or_many([atom(v) for v in t[1:]])
            if t[0] == "LATLONG":
                lat, lon = float(t[1]), float(t[2])
                header[t[0].lower()] = [lat, lon]
        elif t and len(t) == 1 and i + 1 < len(lines) and lines[i + 1].strip() == "BEGINTEXT":
            value, i = text_block(lines, i + 1)
            header[t[0].lower()] = clean_text(value)
            continue
        i += 1
    converter = Converter(lat, lon, origin_x, origin_y, units_per_degree)
    result: dict[str, Any] = {
        "format": "fleet-command-scs-json",
        "source": path.name,
        "metadata": header,
        "coordinate_system": {
            "projection": "local equirectangular",
            "raw_units": "scenario map units",
            "origin_latitude": lat, "origin_longitude": lon,
            "units_per_degree": units_per_degree,
            "note": "Override conversion parameters when the scenario map origin is known."
        },
        "aircraft": [], "land_units": [], "vessels": [], "air_stations": [], "air_corridors": [],
        "formations": [], "groups": [], "triggers_goals": []
    }
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped == ";":
            i += 1; continue
        t = fields(stripped)
        if t[0] == "BITMAP":
            break  # bitmap and palette are intentionally omitted
        if t[0] in {"ENTITY", "NONENTITY"}:
            kind = "nonentity" if t[0] == "NONENTITY" else "entity"
            item, i = parse_record(lines, i, converter, kind)
            typ = item.get("entity_type")
            if typ == "AIRENTITY": target = "aircraft"
            elif typ in {"BUILDINGENTITY", "LANDENTITY"}: target = "land_units"
            else: target = "vessels"
            result[target].append(item); continue
        if t[0] == "AIRSTATION":
            item, i = parse_record(lines, i, converter, "airstation")
            result.setdefault("air_stations", []).append(item)
            continue
        if t[0] == "AIRCORRIDOR" and len(t) >= 10:
            item, i = parse_corridor(lines, i, converter)
            result["air_corridors"].append(item)
            continue
        if t[0] == "GOAL":
            item, i = parse_record(lines, i, converter, "goal")
            result["triggers_goals"].append(item); continue
        if t[0] == "GROUP":
            values = [atom(v) for v in t[2:]]
            # GROUP name id percentage alliance formation parent-group
            # (the final three fields are identifiers used by the scenario
            # engine, not coordinates).
            group = {"name": t[1]}
            names = ("group_id", "probability", "alliance_id", "formation_id", "parent_group_id")
            for name, value in zip(names, values):
                group[name] = value
            result["groups"].append(group); i += 1; continue
        if t[0] in {"AlliedAlliance", "ThreatAlliance", "NeutralAlliance"}:
            values = [atom(v) for v in t[1:]]
            result["formations"].append({
                "type": t[0],
                "member_count": values[0] if values else 0,
                "alliance_ids": values[1:]
            }); i += 1; continue
        if t[0] == "FORMATION":
            result["formations"].append({
                "type": "formation", "name": t[1] if len(t) > 1 else None,
                "leader": t[2] if len(t) > 2 else None
            }); i += 1; continue
        i += 1
    resolve_alliances(result)
    return null_empty(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Fleet Command .scs scenarios to JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="output JSON path (default: stdout)")
    parser.add_argument("--origin-x", type=float, default=555600)
    parser.add_argument("--origin-y", type=float, default=555600)
    parser.add_argument("--units-per-degree", type=float, default=111120)
    args = parser.parse_args()
    document = parse(args.input, args.origin_x, args.origin_y, args.units_per_degree)
    rendered = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
