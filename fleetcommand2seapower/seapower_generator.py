#!/usr/bin/env python3
"""Generate a best-effort Sea Power mission from Fleet Command JSON.

Sea Power positions are local nautical miles relative to the mission map
centre.  Fleet Command JSON positions are geographic coordinates, so this
tool performs the inverse conversion and emits the native mission sections.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from .scs_converter import parse as parse_source


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def scalar(value: Any, default: Any = None) -> Any:
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default


def ini_text(value: Any) -> str:
    """Keep text on one INI line while preserving a readable value."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


class Resolver:
    def __init__(self, root: Path, mapping_path: Path, warn):
        self.root = root
        self.warn = warn
        self.mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        self.files = {
            category: {p.stem for p in (root / category).glob("*.ini")
                       if not p.stem.endswith("_variants") and not p.stem.endswith("_squadrons")}
            for category in ("aircraft", "vessels", "land_units", "biologic")
        }
        self.all_files = set().union(*self.files.values())

    def land_origin(self, item: dict[str, Any]) -> str:
        """Infer the operator of generic Fleet Command installations."""
        direct = str(item.get("country") or item.get("alliance_country") or "").lower()
        if direct:
            return direct
        countries = item.get("alliance_countries")
        if isinstance(countries, list) and len(countries) == 1:
            return str(countries[0]).lower()
        attachments = item.get("aircraft") if isinstance(item.get("aircraft"), list) else []
        attached_countries = [str(a.get("country") or "").lower() for a in attachments
                              if isinstance(a, dict) and a.get("country")]
        if attached_countries:
            return max(set(attached_countries), key=attached_countries.count)
        return ""

    def soviet_installation(self, item: dict[str, Any], name: str, cls: str) -> str | None:
        text = norm(f"{name} {cls}")
        origin = self.land_origin(item)
        if not any(token in origin for token in ("russia", "ussr", "soviet")):
            return None
        if "airport" in text or "airbase" in text:
            for candidate in ("wp_airbase_1", "wp_airbase_2", "wp_large_pvo_airbase"):
                if candidate in self.files["land_units"]:
                    return candidate
        if "sam" in text or "surface to air" in text:
            for candidate in ("wp_sam_site_sa-2", "wp_sam_site_sa-3", "wp_sam_site_sa-10"):
                if candidate in self.files["land_units"]:
                    return candidate
        return None

    def resolve(self, item: dict[str, Any], category: str) -> str | None:
        name = str(item.get("name") or item.get("shipname") or "")
        cls = str(item.get("class") or "")
        candidates = []
        if category == "land_units":
            installation = self.soviet_installation(item, name, cls)
            if installation:
                return installation
        name_map = self.mapping.get("name_map", {})
        candidates += name_map.get(name, [])
        simplified_name = re.sub(r"\s+(?:gen|gn)$", "", name, flags=re.IGNORECASE)
        candidates += name_map.get(simplified_name, [])
        for key, values in self.mapping.get("name_map", {}).items():
            if norm(key) in {norm(name), norm(simplified_name)}:
                candidates += values
        for key, values in self.mapping.get("class_map", {}).items():
            if key.lower() in cls.lower() or key.lower() in name.lower():
                candidates += values

        available = self.files.get(category, self.all_files)
        for candidate in candidates:
            if candidate in available:
                return candidate

        country = str(item.get("country") or "")
        prefix = self.mapping.get("country_prefixes", {}).get(country)
        text = norm(f"{name} {cls}")
        words = set(text.split())
        if "sub" in words or "submarine" in words or re.search(r"\bss[knbg]?\b", text):
            category_hint = "submarine"
        elif category == "aircraft":
            category_hint = "aircraft"
        elif category == "land_units":
            category_hint = "land_unit"
        else:
            category_hint = "vessel"
        pool = [f for f in available if not prefix or f.startswith(prefix + "_")]
        scored: list[tuple[float, str]] = []
        for filename in pool:
            stem_words = set(norm(filename.replace("_", " ")).split())
            overlap = len(words & stem_words)
            ratio = difflib.SequenceMatcher(None, text, norm(filename)).ratio()
            type_bonus = 0.2 if category_hint == "submarine" and re.search(r"_(ss|ssk|ssn|ssg|ssbn)_", filename) else 0
            scored.append((overlap * 0.35 + ratio + type_bonus, filename))
        scored.sort(reverse=True)
        if scored and scored[0][0] >= 0.9:
            if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
                self.warn(f"ambiguous mapping for {name!r}: chose {scored[0][1]} over {scored[1][1]}")
            return scored[0][1]

        civilian = any(token in norm(f"{name} {cls}") for token in ("civilian", "merchant", "fishing", "trawler", "commercial"))
        land_installation = any(token in norm(f"{name} {cls}")
                                for token in ("airport", "airbase", "air field", "airfield"))
        allow_fallback = civilian or (category_hint == "land_unit" and land_installation)
        for candidate in self.mapping.get("generic_fallbacks", {}).get(category_hint, []) if allow_fallback else []:
            if candidate in available:
                self.warn(f"no exact Sea Power mapping for {name!r}; using fallback {candidate}")
                return candidate
        self.warn(f"no Sea Power definition found for {category} {name!r}")
        return None


class Generator:
    LAND_CLUSTER_RADIUS_NM = 0.1
    SAM_CLUSTER_RADIUS_NM = 0.15

    def __init__(self, data: dict[str, Any], resolver: Resolver, year: int = 1985):
        self.data = data
        self.resolver = resolver
        self.warnings: list[str] = []
        resolver.warn = self.warn
        self.year = year
        metadata = data.get("metadata", {})
        center = metadata.get("latlong") or [0, 0]
        self.center_lat = float(center[0]) if isinstance(center, list) else 0.0
        self.center_lon = float(center[1]) if isinstance(center, list) and len(center) > 1 else 0.0

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def local(self, point: dict[str, Any] | None) -> tuple[float, float, float]:
        if not point:
            return 0.0, 0.0, 0.0
        lat, lon = float(point.get("latitude", self.center_lat)), float(point.get("longitude", self.center_lon))
        east = (lon - self.center_lon) * 60 * math.cos(math.radians(self.center_lat))
        north = (lat - self.center_lat) * 60
        return east, float(point.get("altitude") or 0), north

    def pos(self, point: dict[str, Any] | None) -> str:
        x, y, z = self.local(point)
        return f"{x:.4f},{y:.4f},{z:.4f}"

    def waypoints(self, item: dict[str, Any]) -> str | None:
        values = []
        for waypoint in item.get("waypoints", []) if isinstance(item.get("waypoints"), list) else []:
            values.append(self.pos(waypoint))
        return "|".join(values) or None

    def random_box_route(self, item: dict[str, Any]) -> str | None:
        """Convert Fleet Command's area patrol box to a closed Sea Power route."""
        tactic = str(item.get("tactic") or "").replace("_", "").replace(" ", "").upper()
        box = item.get("box") or item.get("random_start_box")
        if tactic != "RANDOMBOX" or not isinstance(box, dict):
            return None
        corners = box.get("corners")
        if not isinstance(corners, list) or len(corners) < 2:
            return None
        first, second = corners[:2]
        if not isinstance(first, dict) or not isinstance(second, dict):
            return None
        altitude = float((item.get("position") or {}).get("altitude") or 0)
        lat1 = float(first.get("latitude", self.center_lat))
        lon1 = float(first.get("longitude", self.center_lon))
        lat2 = float(second.get("latitude", self.center_lat))
        lon2 = float(second.get("longitude", self.center_lon))
        # The parser stores opposite corners.  Visit all four corners and
        # repeat the first one so Sea Power's patrol route remains in the box.
        route = [
            {"latitude": lat1, "longitude": lon1, "altitude": altitude},
            {"latitude": lat1, "longitude": lon2, "altitude": altitude},
            {"latitude": lat2, "longitude": lon2, "altitude": altitude},
            {"latitude": lat2, "longitude": lon1, "altitude": altitude},
            {"latitude": lat1, "longitude": lon1, "altitude": altitude},
        ]
        return "|".join(self.pos(point) for point in route)

    def role(self, item: dict[str, Any]) -> str:
        role = str(item.get("alliance_role") or "").lower()
        if role in {"enemy", "hostile", "threat"}: return "enemy"
        if role in {"neutral", "unknown"}: return "neutral"
        return "friendly"

    def autodetect(self, item: dict[str, Any]) -> bool:
        """Return Fleet Command's automatic initial-reveal flag."""
        value = item.get("autodetect")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def category(self, item: dict[str, Any], source: str) -> str:
        if source == "aircraft":
            text = norm(f"{item.get('name')} {item.get('class')}")
            if re.search(r"helicopter|sea hawk|sea king|lynx|dauphin|alouette|helix|\bah[- ]?\d|\bka[- ]?\d", text):
                return "helicopter"
            return "aircraft"
        text = norm(f"{item.get('name')} {item.get('class')}")
        return "submarine" if re.search(r"\bsubmarine\b|\bsub\b|\bss[knbg]?\b", text) else "vessel"

    def section(self, prefix: str, index: int, item: dict[str, Any], source: str,
               home_base: str | None = None, spawn_chance: float | None = None) -> tuple[str, str] | None:
        category = self.category(item, source)
        folder = "aircraft" if category in {"aircraft", "helicopter"} else "vessels"
        definition = self.resolver.resolve(item, folder)
        if not definition: return None
        section_type = ("Aircraft" if category == "aircraft" else
                        "Helicopter" if category == "helicopter" else
                        "Submarine" if category == "submarine" else "Vessel")
        section = f"[{prefix}{section_type}{index}]"
        lines = [section, f"Type={definition}", "CrewSkill=Trained", f"RelativePositionInNM={self.pos(item.get('position'))}"]
        if source == "vessels" and item.get("aircraft"):
            self.add_flight_deck(lines, item)
        if category in {"aircraft", "helicopter"}:
            # Sea Power resolves aircraft loadout/flight behaviour through a
            # squadron reference. Leaving this implicit can cause a null
            # reference in the mission editor even when the aircraft type is
            # valid.
            lines.insert(2, "SquadronReference=Default")
            lines.insert(3, "MissionType=NoMission")
        probability_chance: float | None = None
        group_id = item.get("group")
        if isinstance(group_id, (int, float)):
            group = next((candidate for candidate in self.data.get("groups", [])
                          if isinstance(candidate, dict) and candidate.get("group_id") == group_id), None)
            if isinstance(group, dict):
                try:
                    group_probability = float(group.get("probability", 100))
                    if 0 < group_probability < 100:
                        # Sea Power has no Fleet Command group-probability
                        # primitive. Apply the source group's chance to each
                        # emitted member as the closest runtime equivalent.
                        probability_chance = group_probability / 100
                except (TypeError, ValueError):
                    pass
        # Preserve Fleet Command's per-unit probability. Without this every
        # probabilistic aircraft candidate becomes guaranteed in Sea Power.
        try:
            member_probability = float(item.get("prob", 100))
            if 0 < member_probability < 100:
                probability_chance = (probability_chance or 1.0) * member_probability / 100
        except (TypeError, ValueError):
            pass
        if probability_chance is not None:
            lines.insert(2, f"SpawnChance={probability_chance:.3f}")
        if spawn_chance is not None:
            lines = [line for line in lines if not line.startswith("SpawnChance=")]
            lines.insert(2, f"SpawnChance={spawn_chance:.6f}")
        if home_base: lines.append(f"HomeBase={home_base}")
        if item.get("course") is not None: lines.append(f"Heading={float(item['course']):.2f}")
        if item.get("speed") is not None:
            speed = float(item["speed"])
            if category in {"aircraft", "helicopter"}:
                # Aircraft use Sea Power's separate 2-5 telegraph range. The
                # Fleet Command values are knots; 100 kt is a slow turboprop
                # setting, not Sea Power's maximum aircraft throttle.
                lines.append(f"Telegraph={max(2, min(5, round(speed / 50)))}")
            else:
                lines.append(f"Telegraph={max(1, min(5, round(speed / 5)))}")
        box = item.get("box") or item.get("random_start_box")
        if isinstance(box, dict) and isinstance(box.get("corners"), list) and len(box["corners"]) >= 2:
            first, second = box["corners"][:2]
            cx = (float(first.get("latitude", self.center_lat)) + float(second.get("latitude", self.center_lat))) / 2
            cy = (float(first.get("longitude", self.center_lon)) + float(second.get("longitude", self.center_lon))) / 2
            center = {"latitude": cx, "longitude": cy, "altitude": float(item.get("position", {}).get("altitude") or 0)}
            width = abs(float(first.get("longitude", self.center_lon)) - float(second.get("longitude", self.center_lon))) * 60 * math.cos(math.radians(self.center_lat))
            height = abs(float(first.get("latitude", self.center_lat)) - float(second.get("latitude", self.center_lat))) * 60
            lines.append(f"RandomSpawnCenter={self.pos(center)}")
            lines.append(f"RandomSpawnRange={max(width, height) / 2:.4f}")
        route = self.waypoints(item)
        if not route:
            route = self.random_box_route(item)
            if route:
                lines = [line for line in lines if not line.startswith("MissionType=")]
                lines.append("MissionType=Patrol")
        if route: lines.append(f"Waypoints={route}")
        if item.get("uniquename"): lines.append(f"# FleetCommandName={item['uniquename']}")
        return "\n".join(lines), section[1:-1]

    def probability_groups(self, source: str) -> dict[tuple[str, int], list[dict[str, Any]]]:
        """Find groups whose members represent one random choice.

        Fleet Command stores the candidate probability on each member.  We only
        collapse a group when the group itself is always present, every member
        has a partial probability, and none has a route.  That avoids confusing
        ordinary probabilistic patrol groups with an exactly-one selection.
        """
        if source != "vessels":
            return {}
        group_info = {
            int(group["group_id"]): group
            for group in self.data.get("groups", [])
            if isinstance(group, dict) and isinstance(group.get("group_id"), (int, float))
        }
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for item in self.data.get(source, []) if isinstance(self.data.get(source), list) else []:
            if not isinstance(item.get("group"), (int, float)):
                continue
            group_id = int(item["group"])
            group = group_info.get(group_id, {})
            try:
                group_probability = float(group.get("probability", 100))
                member_probability = float(item.get("prob", 100))
            except (TypeError, ValueError):
                continue
            if group_probability < 100 or not 0 < member_probability < 100:
                continue
            grouped.setdefault((self.role(item), group_id), []).append(item)
        selected: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for key, members in grouped.items():
            has_routes = any(item.get("waypoints") for item in members)
            probability_total = sum(float(item.get("prob", 0)) for item in members)
            # A 100% total is Fleet Command's one-of-N selection, including
            # patrol groups with routes. Route-less groups retain the legacy
            # one-of-N handling even where their source values are rounded.
            if len(members) > 1 and (not has_routes or 95 <= probability_total <= 105):
                selected[key] = members
        return selected

    def group_probability(self, group_id: int) -> float:
        for group in self.data.get("groups", []) if isinstance(self.data.get("groups"), list) else []:
            if isinstance(group, dict) and group.get("group_id") == group_id:
                try:
                    return max(0.0, min(100.0, float(group.get("probability", 100))))
                except (TypeError, ValueError):
                    return 100.0
        return 100.0

    def choose_probability_candidate(self, items: list[dict[str, Any]], group_id: int) -> dict[str, Any]:
        """Choose one candidate reproducibly when Sea Power cannot encode a type list."""
        seed_text = f"{self.data.get('source', '')}|{self.data.get('metadata', {}).get('missiontitle', '')}|{group_id}"
        digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
        return items[int.from_bytes(digest[:8], "big") % len(items)]

    def land_section(self, prefix: str, index: int, item: dict[str, Any]) -> tuple[str, str] | None:
        definition = self.resolver.resolve(item, "land_units")
        if not definition: return None
        section = f"[{prefix}LandUnit{index}]"
        point = item.get("position") or {}
        altitude = point.get("altitude")
        geo_altitude = "low" if not altitude else f"{float(altitude):.0f}"
        lines = [section, f"Type={definition}",
                 f"GeoPosition={float(point.get('latitude', self.center_lat)):.8f},{float(point.get('longitude', self.center_lon)):.8f},{geo_altitude}"]
        if self.is_airbase(item, definition):
            # Airbase definitions have a built-in aircraft roster in Sea Power.
            # Replace it explicitly or those aircraft appear even when the
            # Fleet Command air station has no active aircraft assigned.
            self.add_flight_deck(lines, item)
        if item.get("course") is not None: lines.append(f"Heading={float(item['course']):.2f}")
        if item.get("uniquename"): lines.append(f"# FleetCommandName={item['uniquename']}")
        return "\n".join(lines), section[1:-1]

    def is_airbase(self, item: dict[str, Any], definition: str) -> bool:
        text = norm(f"{item.get('name')} {item.get('class')} {definition}")
        return any(token in text for token in ("airport", "airbase", "air field", "airfield"))

    def land_cluster_kind(self, item: dict[str, Any]) -> str | None:
        """Return the merge class for small Fleet Command land entities."""
        definition = self.resolver.resolve(item, "land_units") or ""
        text = norm(f"{item.get('name')} {item.get('class')} {definition}")
        if self.is_airbase(item, definition) or "ssm" in text or "silkworm" in text:
            return None
        if "sam" in text or "skyguard" in text or "launcher" in text:
            return "sam"
        if (definition.startswith(("civ_comm_buildings", "tgt_", "warehouses_", "airfield_"))
                or str(item.get("name") or "") in {"Bunker", "Building2", "Building4", "Building5", "ChemicalPlant",
                                                       "Dock", "DrugFactory", "Factory", "Hangar", "Hospital", "Piers",
                                                       "PowerPlant", "Sonalysts", "TrainingFacility", "Whouse"}
                or str(item.get("name") or "").startswith(("Tent_",))):
            return "building"
        return None

    def land_clusters(self) -> list[list[dict[str, Any]]]:
        """Merge nearby building/SAM records while preserving other units."""
        items = self.data.get("land_units", []) if isinstance(self.data.get("land_units"), list) else []
        clusters: list[list[dict[str, Any]]] = []
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in items:
            kind = self.land_cluster_kind(item) if isinstance(item, dict) else None
            if kind:
                grouped.setdefault((self.role(item), kind), []).append(item)
            else:
                clusters.append([item])
        for (_, _), candidates in grouped.items():
            parent = list(range(len(candidates)))

            def find(index: int) -> int:
                while parent[index] != index:
                    parent[index] = parent[parent[index]]
                    index = parent[index]
                return index

            def union(left: int, right: int) -> None:
                left, right = find(left), find(right)
                if left != right:
                    parent[right] = left

            radius = self.SAM_CLUSTER_RADIUS_NM if candidates and self.land_cluster_kind(candidates[0]) == "sam" else self.LAND_CLUSTER_RADIUS_NM
            for index, item in enumerate(candidates):
                first = item.get("position") or {}
                for other_index in range(index):
                    other = candidates[other_index]
                    second = other.get("position") or {}
                    latitude = (float(first.get("latitude", self.center_lat))
                                + float(second.get("latitude", self.center_lat))) / 2
                    east = (float(first.get("longitude", self.center_lon))
                            - float(second.get("longitude", self.center_lon))) * 60 * math.cos(math.radians(latitude))
                    north = (float(first.get("latitude", self.center_lat))
                             - float(second.get("latitude", self.center_lat))) * 60
                    if math.hypot(east, north) <= radius:
                        union(index, other_index)
            merged: dict[int, list[dict[str, Any]]] = {}
            for index, item in enumerate(candidates):
                merged.setdefault(find(index), []).append(item)
            clusters.extend(merged.values())
        return clusters

    def flight_deck(self, item: dict[str, Any]) -> tuple[dict[str, int], list[tuple[str, int, str]]]:
        """Map a Fleet Command aircraft roster to Sea Power's air group.

        Every aircraft remains in the roster. Alert5/Alert15/Alert30 become
        ready-up tasks with 0/900/1800 seconds of delay. Aircraft without an
        alert state remain available but are not readied automatically.
        """
        roster: dict[str, int] = {}
        ready_tasks: list[tuple[str, int, str, int]] = []
        attachments = item.get("aircraft") if isinstance(item.get("aircraft"), list) else []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            definition = self.resolver.resolve(attachment, "aircraft")
            if not definition:
                continue
            statuses = attachment.get("alert_status")
            try:
                quantity = max(0, int(float(attachment.get("quantity") or 0)))
            except (TypeError, ValueError):
                quantity = 0
            roster[definition] = roster.get(definition, 0) + quantity
            text = norm(f"{attachment.get('name')} {attachment.get('class')}")
            if "electronic" in text:
                task = "EW"
            elif any(token in text for token in ("early warning", "hawkeye", "awacs")):
                task = "AEW"
            elif ("asw" in text or "anti submarine" in text or "sea hawk" in text
                  or any(token in text for token in ("ka 25", "ka 27", "sh 2", "sh 60", "p 3", "s 3", "il 38"))):
                task = "ASWBomb"
            elif "fighter" in text:
                task = "AirToAirLongRange"
            elif any(token in text for token in ("bomber", "attack")):
                task = "AntiShipHeavy"
            else:
                task = "AirToAirLongRange"
            delays = {"Alert5": 0, "Alert15": 900, "Alert30": 1800}
            if isinstance(statuses, list):
                for entry in statuses:
                    if not isinstance(entry, dict) or entry.get("alert") not in delays:
                        continue
                    try:
                        ready = max(0, min(quantity, int(float(entry.get("value") or 0))))
                    except (TypeError, ValueError):
                        ready = 0
                    if ready:
                        ready_tasks.append((definition, ready, task, delays[entry["alert"]]))
        return roster, ready_tasks

    def add_flight_deck(self, lines: list[str], item: dict[str, Any]) -> None:
        roster, ready_tasks = self.flight_deck(item)
        lines.append("CustomAirGroup=True")
        if not roster:
            roster["civ_c340"] = 0
        lines.extend(f"{definition}=Default,{quantity}" for definition, quantity in roster.items())
        if ready_tasks:
            lines.append(f"FlightDeck_ReadyUpTasks={len(ready_tasks)}")
            for number, (definition, quantity, task, delay) in enumerate(ready_tasks, 1):
                lines.append(f"FlightDeck_ReadyUpTask{number}={definition},Default,{task},{quantity},{delay}")

    def attached_aircraft(self, prefix: str, item: dict[str, Any], home_base: str,
                          counts: dict[str, int]) -> list[str]:
        """Expand aircraft carried by a vessel or stationed at a base."""
        blocks: list[str] = []
        # Fleet Command vessel aircraft represent aircraft stored in the
        # ship's internal flight deck. Sea Power's standalone aircraft
        # sections represent units already launched, so do not spawn them.
        attachments = item.get("aircraft") if isinstance(item.get("aircraft"), list) else []
        if self.is_airbase(item, ""):
            return blocks
        if attachments:
            self.warn(f"aircraft attached to {item.get('uniquename')!r} remain internal to their base unit and were not spawned")
        return blocks

    def corridor_aircraft(self, corridor: dict[str, Any], index: int) -> str:
        """Represent a Fleet Command air corridor as neutral civilian traffic."""
        attachments = corridor.get("aircraft") if isinstance(corridor.get("aircraft"), list) else []
        attachment = attachments[0] if attachments else {}
        label = norm(f"{attachment.get('name')} {attachment.get('class')}")
        definition = "civ_707" if any(token in label for token in ("jet", "airliner", "707", "large")) else "civ_c340"
        start = corridor.get("start") or {}
        end = corridor.get("end") or start
        start_local = self.local(start)
        end_local = self.local(end)
        heading = math.degrees(math.atan2(end_local[0] - start_local[0], end_local[2] - start_local[2]))
        altitude_values = [value for value in (corridor.get("minimum_altitude"), corridor.get("maximum_altitude"))
                           if isinstance(value, (int, float))]
        altitude = sum(altitude_values) / len(altitude_values) if altitude_values else 10000
        speed = float(corridor.get("speed") or 15)
        item = {
            "name": "Civilian Air Corridor Traffic",
            "class": "CIVILIAN AIRCRAFT",
            "country": "NEUTRALS",
            "alliance_role": "neutral",
            "position": {"latitude": start.get("latitude", self.center_lat),
                          "longitude": start.get("longitude", self.center_lon),
                          "altitude": altitude},
            "course": heading,
            "speed": speed,
            "waypoints": [{"latitude": end.get("latitude", self.center_lat),
                           "longitude": end.get("longitude", self.center_lon),
                           "altitude": altitude}],
            "uniquename": f"Air corridor {index}",
        }
        definition = definition if definition in self.resolver.files["aircraft"] else "civ_c340"
        section = f"[NeutralAircraft{index}]\nType={definition}\nSquadronReference=Default\nMissionType=NoMission\nCrewSkill=Trained\nRelativePositionInNM={self.pos(item['position'])}\nTelegraph={max(2, min(5, round(speed / 50)))}\nHeading={heading:.2f}\nWaypoints={self.pos(item['waypoints'][0])}\n# FleetCommandAirCorridor={corridor.get('name') or 'unnamed'}"
        return section

    def trigger_sections(self, units: dict[str, str], autodetect_units: list[str] | None = None) -> tuple[list[str], list[str]]:
        triggers, objectives = [], []
        goals = self.data.get("triggers_goals", [])
        children_by_parent: dict[int, list[dict[str, Any]]] = {}
        for child in goals:
            try:
                parent_id = int(float(child.get("parentgoalid") or -1))
            except (TypeError, ValueError):
                parent_id = -1
            children_by_parent.setdefault(parent_id, []).append(child)
        for n, (objective_id, name) in enumerate(self.objective_labels(), 1):
            goal = goals[n - 1]
            points = int(float(goal.get("points") or 0))
            # Fleet Command's negative goal points are penalties, whereas
            # Sea Power's first objective field is the completion reward and
            # its second field is the failure penalty.
            reward = max(0, points)
            penalty = points if points < 0 else -5
            objectives.append(f"{objective_id}={reward},{penalty},Complete,Main")
            target = str(goal.get("uniquename") or "")
            target_section = units.get(target)
            goal_type = int(float(goal.get("goaltype") or 2))
            goal_start = int(float(goal.get("starttime") or 0))
            time_goal = bool(re.search(r"\bend\s*mission\b|\bmission\s+over\b|\bquit\b|\bfail", name, re.IGNORECASE))
            all_enemy_aircraft = bool(re.search(r"hostile.*air|all.*air|air.*destroyed", name, re.IGNORECASE))
            child_sections = []
            for child in children_by_parent.get(int(float(goal.get("goalid") or -1)), []):
                child_section = units.get(str(child.get("uniquename") or ""))
                if child_section and child_section not in child_sections:
                    child_sections.append(child_section)
            if target_section and goal_type == 2:
                condition = f"Condition_Condition1_Units={target_section}"
                ctype = "UnitDestroyed"
                if float(goal.get("damage") or 100) < 95:
                    self.warn(f"goal {name!r} requires partial damage; using Sea Power UnitDestroyed workaround")
            elif goal_type == 3 and all_enemy_aircraft:
                condition = "Condition_Condition1_Taskforce=Taskforce2\nCondition_Condition1_UnitType=Aircraft"
                ctype = "HasNoUnitsOfType"
            elif goal_type == 3 and time_goal and goal_start > 0:
                condition = f"Condition_Condition1_Time={goal_start}"
                ctype = "Time"
            elif goal_type == 3 and child_sections and re.search(r"special.*force|all.*(killed|destroyed)", name, re.IGNORECASE):
                condition = f"Condition_Condition1_Units={','.join(child_sections)}"
                ctype = "UnitDestroyed"
            elif goal_type == 3 and goal.get("position"):
                trigger_point = dict(goal.get("position"))
                trigger_point["altitude"] = 0
                condition = f"Condition_Condition1_PositionNM={self.pos(trigger_point)}\nCondition_Condition1_AreaRadiusNM=5"
                ctype = "UnitsInTheArea"
            elif target_section:
                condition = f"Condition_Condition1_Units={target_section}"
                ctype = "UnitDestroyed"
                self.warn(f"goal {name!r} has unsupported goal type {goal_type}; using UnitDestroyed")
            else:
                condition = f"Condition_Condition1_Time={max(1, goal_start)}"
                ctype = "Time"
                if target: self.warn(f"goal {name!r} target {target!r} was not emitted or could not be linked")
            lines = [f"[Trigger{n}]", f"Name={name}", f"Action_ObjectivesCompleted={objective_id}"]
            if goal_type == 3:
                victory = "Taskforce1" if re.search(r"mission\s+over", name, re.IGNORECASE) else ("Taskforce2" if time_goal else "Taskforce1")
                lines += ["Action_EndMission=True", f"Action_Victory={victory}"]
            lines += [f"Condition_Condition1_Type={ctype}", condition, "ConditionsCompleted=<Condition1>"]
            triggers.append("\n".join(lines))
        intel = ini_text(self.data.get("metadata", {}).get("intelmessage"))
        if intel and intel.lower() not in {"none", "none available", "not available", "n/a"}:
            trigger_number = len(goals) + 1
            triggers.append("\n".join([
                f"[Trigger{trigger_number}]",
                "Name=Fleet Command intelligence",
                "Condition_Condition1_Type=Time",
                "Condition_Condition1_Time=1",
                "Action_Taskforce1_Message=Taskforce1IntelMessage",
                "Action_Taskforce1_Intel=Taskforce1ScenarioIntel",
                "ConditionsCompleted=<Condition1>",
            ]))
        reveal_units = list(dict.fromkeys(autodetect_units or []))
        reveal_start = len(goals) + (1 if intel and intel.lower() not in {"none", "none available", "not available", "n/a"} else 0) + 1
        for offset, section in enumerate(reveal_units):
            triggers.append("\n".join([
                f"[Trigger{reveal_start + offset}]",
                "Name=Fleet Command autodetect",
                "Condition_Condition1_Type=Time",
                "Condition_Condition1_Time=1",
                f"Action_Units={section}",
                "Action_UnitRevealToTaskforce=Taskforce1|Identify",
                "Action_UnitRevealTime=-1",
                "ConditionsCompleted=<Condition1>",
            ]))
        return triggers, objectives

    def objective_labels(self) -> list[tuple[str, str]]:
        """Return stable Sea Power objective IDs and their display names."""
        labels: list[tuple[str, str]] = []
        objective_names: set[str] = set()
        for n, goal in enumerate(self.data.get("triggers_goals", []), 1):
            name = ini_text(goal.get("goalname") or f"Goal {n}")
            objective_id = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or f"Goal{n}"
            if objective_id.lower() in {value.lower() for value in objective_names}:
                objective_id += f"_{n}"
            objective_names.add(objective_id)
            labels.append((objective_id, name))
        return labels

    def render(self) -> str:
        meta = self.data.get("metadata", {})
        title = ini_text(meta.get("missiontitle") or Path(str(self.data.get("source") or "mission")).stem)
        description = ini_text(meta.get("description") or meta.get("taskingmessage") or "Converted Fleet Command scenario")
        tasking = ini_text(meta.get("taskingmessage"))
        intel = ini_text(meta.get("intelmessage"))
        objective_labels = [f"Objective_{objective_id}={name}" for objective_id, name in self.objective_labels()]
        lines = ["[General]", "Type=Scenario", "", "[Language_en]", f"Name={title}", f"Description={description}",
                 *objective_labels]
        if tasking:
            lines.append(f"Taskforce1StartMessage={tasking}|Begin mission")
        if intel and intel.lower() not in {"none", "none available", "not available", "n/a"}:
            lines += ["Taskforce1IntelMessage=Intel|New intelligence received|Acknowledge",
                      f"Taskforce1ScenarioIntel={intel}"]
        month = int(float(meta.get("month") or 1))
        hour = int(float(meta.get("timeofday") or 12)) % 24
        sea_state = int(float(meta.get("seastate") or 0))
        weather_names = {0: "Clear", 1: "Scattered", 2: "Broken", 3: "Overcast"}
        cloud_names = {0: "Clear", 1: "Scattered", 2: "Broken", 3: "Overcast"}
        weather = weather_names.get(int(float(meta.get("weather") or 0)), "Clear")
        clouds = cloud_names.get(int(float(meta.get("weather") or 0)), weather)
        difficulty = int(float(meta.get("difficulty") or 2))
        lines += ["", "[Environment]", f"Date={self.year},{month},1", f"Time={hour},0", "ConvertTimeToLocal=False",
                  f"SeaState={sea_state}", f"Clouds={clouds}", f"Weather={weather}",
                  f"CloudHeight={float(meta.get('cloudheight') or 0):.0f}",
                  f"MapCenterLatitude={self.center_lat:.6f}", f"MapCenterLongitude={self.center_lon:.6f}", "LoadBackgroundData=False", "",
                  "[Mission]", f"Difficulty={difficulty}", "PlayerTaskforce=Taskforce1", "EnemyTaskforce=Taskforce2"]
        units: dict[str, str] = {}
        blocks: list[str] = []
        counts = {"Taskforce1Vessel": 0, "Taskforce2Vessel": 0, "NeutralVessel": 0,
                  "Taskforce1Submarine": 0, "Taskforce2Submarine": 0, "NeutralSubmarine": 0,
                  "Taskforce1Aircraft": 0, "Taskforce2Aircraft": 0, "NeutralAircraft": 0,
                  "Taskforce1Helicopter": 0, "Taskforce2Helicopter": 0, "NeutralHelicopter": 0,
                  "Taskforce1LandUnit": 0, "Taskforce2LandUnit": 0, "NeutralLandUnit": 0}
        group_refs: dict[str, dict[int, list[str]]] = {"friendly": {}, "enemy": {}, "neutral": {}}
        group_has_routes: dict[str, set[int]] = {"friendly": set(), "enemy": set(), "neutral": set()}
        autodetect_units: list[str] = []
        random_groups = self.probability_groups("vessels")
        random_group_members = {id(item) for members in random_groups.values() for item in members}
        random_group_emitted: set[tuple[str, int]] = set()
        random_group_ids: dict[str, set[int]] = {"friendly": set(), "enemy": set(), "neutral": set()}
        for source in ("vessels", "aircraft"):
            for item in self.data.get(source, []) if isinstance(self.data.get(source), list) else []:
                role = self.role(item); prefix = {"friendly": "Taskforce1", "enemy": "Taskforce2", "neutral": "Neutral"}[role]
                random_key = (role, int(item["group"])) if source == "vessels" and isinstance(item.get("group"), (int, float)) else None
                if id(item) in random_group_members:
                    if random_key in random_group_emitted:
                        continue
                    candidates = random_groups[random_key]
                    random_group_emitted.add(random_key)
                    random_group_ids[role].add(random_key[1])
                    candidate = self.choose_probability_candidate(candidates, random_key[1])
                    candidate_kind = self.category(candidate, source)
                    candidate_key = prefix + ("Submarine" if candidate_kind == "submarine" else "Vessel")
                    group_chance = self.group_probability(random_key[1]) / 100
                    result = self.section(prefix, counts[candidate_key] + 1, candidate,
                                          source, spawn_chance=group_chance)
                    if not result:
                        continue
                    block, section = result
                    block += f"\n# FleetCommandRandomGroup={random_key[1]}; selected={candidate.get('uniquename')}; candidates={','.join(str(item.get('uniquename')) for item in candidates)}"
                    counts[candidate_key] += 1
                    blocks.append(block)
                    for candidate_item in candidates:
                        if candidate_item.get("uniquename"):
                            units[str(candidate_item["uniquename"])] = section
                    if role != "friendly" and any(self.autodetect(candidate_item) for candidate_item in candidates):
                        autodetect_units.append(section)
                    group_refs[role].setdefault(random_key[1], []).append(section)
                    blocks.extend(self.attached_aircraft(prefix, candidate, section, counts))
                    self.warn(f"group {random_key[1]} selected one reproducibly-random vessel at conversion time: {candidate.get('uniquename')!r}")
                    continue
                kind = self.category(item, source)
                key = prefix + ("Submarine" if kind == "submarine" else
                                "Helicopter" if kind == "helicopter" else
                                "Aircraft" if kind == "aircraft" else "Vessel")
                result = self.section(prefix, counts[key] + 1, item, source)
                if result:
                    counts[key] += 1
                    block, section = result; blocks.append(block)
                    if item.get("uniquename"): units[str(item["uniquename"])] = section
                    if role != "friendly" and self.autodetect(item):
                        autodetect_units.append(section)
                    if isinstance(item.get("group"), (int, float)):
                        group_id = int(item["group"])
                        group_refs[role].setdefault(group_id, []).append(section)
                        if isinstance(item.get("waypoints"), list) and item.get("waypoints"):
                            group_has_routes[role].add(group_id)
                    if source == "vessels":
                        blocks.extend(self.attached_aircraft(prefix, item, section, counts))
        for cluster in self.land_clusters():
            item = cluster[0]
            if len(cluster) > 1:
                item = dict(item)
                points = [member.get("position") or {} for member in cluster]
                item["position"] = {
                    "latitude": sum(float(point.get("latitude", self.center_lat)) for point in points) / len(points),
                    "longitude": sum(float(point.get("longitude", self.center_lon)) for point in points) / len(points),
                    "altitude": sum(float(point.get("altitude") or 0) for point in points) / len(points),
                }
            role = self.role(item); prefix = {"friendly": "Taskforce1", "enemy": "Taskforce2", "neutral": "Neutral"}[role]
            key = prefix + "LandUnit"
            result = self.land_section(prefix, counts[key] + 1, item)
            if result:
                counts[key] += 1
                block, section = result
                if len(cluster) > 1:
                    names = [str(member.get("uniquename") or member.get("name") or "unnamed") for member in cluster]
                    block += f"\n# FleetCommandMergedLandUnits={','.join(names)}"
                blocks.append(block)
                for member in cluster:
                    if member.get("uniquename"):
                        units[str(member["uniquename"])] = section
                    if isinstance(member.get("group"), (int, float)):
                        group_id = int(member["group"])
                        refs = group_refs[role].setdefault(group_id, [])
                        if section not in refs:
                            refs.append(section)
                        if isinstance(member.get("waypoints"), list) and member.get("waypoints"):
                            group_has_routes[role].add(group_id)
                if role != "friendly" and any(self.autodetect(member) for member in cluster):
                    autodetect_units.append(section)
                blocks.extend(self.attached_aircraft(prefix, item, section, counts))
        for corridor in self.data.get("air_corridors", []) if isinstance(self.data.get("air_corridors"), list) else []:
            attachments = corridor.get("aircraft") if isinstance(corridor, dict) else []
            quantity = 1
            if attachments and isinstance(attachments[0], dict):
                quantity = max(1, int(float(attachments[0].get("quantity") or 1)))
            for _ in range(quantity):
                counts["NeutralAircraft"] += 1
                blocks.append(self.corridor_aircraft(corridor, counts["NeutralAircraft"]))
        # Older Fleet Command scenarios often encode tactical formations as a
        # formationleader link on each escort rather than as a GROUP record.
        # Reconstruct those links after section names are known.
        leader_formations: dict[tuple[str, str], list[str]] = {}
        for vessel in self.data.get("vessels", []) if isinstance(self.data.get("vessels"), list) else []:
            leader_name = vessel.get("formationleader")
            member_name = vessel.get("uniquename")
            if not leader_name or not member_name or member_name not in units or leader_name not in units:
                continue
            role = self.role(vessel)
            key = (role, str(leader_name))
            refs = leader_formations.setdefault(key, [])
            for ref in (units[str(leader_name)], units[str(member_name)]):
                if ref not in refs:
                    refs.append(ref)
        for key, count in counts.items():
            suffix = "" if key.endswith("Aircraft") else "s"
            lines.append(f"NumberOf{key}{suffix}={count}")
        group_names = {int(g.get("group_id")): str(g.get("name") or f"Group {g.get('group_id')}")
                       for g in self.data.get("groups", []) if isinstance(g, dict) and isinstance(g.get("group_id"), (int, float))}
        for role, taskforce in (("friendly", "Taskforce1"), ("enemy", "Taskforce2")):
            formations = [(gid, refs) for gid, refs in group_refs[role].items()
                          if refs and gid not in group_has_routes[role] and gid not in random_group_ids[role]]
            linked = [(label, refs) for (formation_role, label), refs in leader_formations.items()
                      if formation_role == role and len(refs) > 1]
            formations.extend((label, refs) for label, refs in linked)
            for gid in sorted(group_has_routes[role] & set(group_refs[role])):
                self.warn(f"group {gid} in {taskforce} has unit routes; individual routes were preserved instead of a formation")
            if formations:
                lines.append(f"{taskforce}_NumberOfFormations={len(formations)}")
                for n, (gid, refs) in enumerate(formations, 1):
                    label = ini_text(group_names.get(gid, f"Group {gid}"))
                    # Preserve the Fleet Command positions. Without the final
                    # override flag Sea Power may apply its own formation
                    # geometry and spread vessels far beyond their source
                    # coordinates.
                    lines.append(f"{taskforce}_Formation{n}={','.join(refs)}|{label}|Vic|0.1|OverrideSpawnPositions")
        if self.data.get("formations"):
            self.warn("Fleet Command alliance declarations are not tactical Sea Power formations and were not copied")
        trigger_count = len(self.data.get("triggers_goals", [])) + (1 if intel and intel.lower() not in {"none", "none available", "not available", "n/a"} else 0) + len(set(autodetect_units))
        lines.append(f"NumberOfTriggers={trigger_count}")
        lines += ["", *blocks]
        triggers, objectives = self.trigger_sections(units, autodetect_units)
        if triggers:
            # Keep the standard section marker used by Sea Power's mission
            # editor. It helps the editor classify the following [TriggerN]
            # blocks while remaining a harmless INI section to the game.
            lines += ["", "[ ============================== Triggers ============================== ]", *triggers]
        if objectives: lines += ["", "[Taskforce1_Objectives]", *objectives]
        lines += ["", "[BackgroundData]", "NumberOfBackgroundCityFiles=0", "NumberOfBackgroundAirportFiles=0",
                  "NumberOfBackgroundPortFiles=0", "NumberOfBackgroundInstallationFiles=0", "NumberOfBackgroundSceneryFiles=0", ""]
        if self.warnings:
            lines.insert(0, "# Conversion warnings are printed by the generator; see stderr.")
        return "\n".join(lines)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Fleet Command JSON or directory of scenario files")
    parser.add_argument("output", type=Path, nargs="?", help="INI output, or batch output directory")
    parser.add_argument("--streaming-assets", type=Path, default=Path("SeaPower_StreamingAssets"))
    parser.add_argument("--mapping", type=Path, default=Path("seapower_mapping.json"))
    parser.add_argument("--output-dir", type=Path, help="Batch output directory")
    parser.add_argument("--report", type=Path, help="Quality report JSON path")
    parser.add_argument("--year", type=int, default=1985, help="Mission year (Fleet Command files do not encode one)")
    args = parser.parse_args()
    resolver = Resolver(args.streaming_assets, args.mapping, print)

    if args.input.is_dir():
        output_dir = args.output_dir or args.output or Path("SeaPower_Missions_Generated")
        output_dir.mkdir(parents=True, exist_ok=True)
        sources = sorted(path for path in args.input.iterdir()
                         if path.suffix.lower() in {".scs", ".sct", ".scm", ".scc"})
        report: list[dict[str, Any]] = []
        warning_lines: list[str] = []
        failures = 0
        for source in sources:
            output = output_dir / f"{source.stem}.ini"
            try:
                data = parse_source(source)
                generator = Generator(data, resolver, args.year)
                rendered = generator.render()
                output.write_text(rendered, encoding="utf-8", newline="\n")
                warning_lines.append(f"{source.name}: {len(generator.warnings)} warning(s) -> {output.name}")
                warning_lines.extend(f"  WARNING: {warning}" for warning in generator.warnings)
                report.append(quality_entry(source, output, data, rendered, generator.warnings))
            except Exception as exc:
                failures += 1
                warning_lines.append(f"{source.name}: FAILED: {exc}")
                report.append({"source": source.name, "output": output.name, "status": "failed", "error": str(exc)})
        (output_dir / "conversion_warnings.txt").write_text("\n".join(warning_lines) + "\n", encoding="utf-8")
        report_path = args.report or output_dir / "quality_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"scenarios": report, "summary": {
            "total": len(sources), "generated": len(sources) - failures, "failed": failures,
            "warnings": sum(len(entry.get("warnings", [])) for entry in report)
        }}, indent=2) + "\n", encoding="utf-8")
        print(f"Generated {len(sources) - failures} of {len(sources)} missions in {output_dir}")
        print(f"Warnings: {output_dir / 'conversion_warnings.txt'}")
        print(f"Quality report: {report_path}")
        return 1 if failures else 0

    if args.input.suffix.lower() != ".json":
        parser.error("a single input must be custom JSON; use a directory for .scs/.sct/.scm/.scc files")
    output = args.output or args.input.with_suffix(".ini")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    generator = Generator(data, resolver, args.year)
    rendered = generator.render()
    output.write_text(rendered, encoding="utf-8", newline="\n")
    for warning in generator.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"Wrote {output} ({len(generator.warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
