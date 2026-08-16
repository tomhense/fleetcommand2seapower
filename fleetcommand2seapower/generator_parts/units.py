"""Sea Power section builders and unit grouping rules."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from ..generator_utils import ini_text, norm, role_prefix

class UnitMixin:
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

