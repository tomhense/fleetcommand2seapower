"""Sea Power section builders and unit grouping rules."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from ..generator_utils import ini_text, norm, role_prefix

class UnitMixin:
    # Land definitions often have ground pads/selection visuals extending
    # beyond their physical colliders.  Reserve extra space for those meshes.
    LAND_GROUND_FOOTPRINT_FACTOR = 2.0

    def goal_aircraft_matches(self, aircraft: dict[str, Any], goal: dict[str, Any]) -> bool:
        filters = [str(goal.get(key) or "") for key in ("class", "shipname", "tagname")]
        filters = [norm(value) for value in filters if norm(value)]
        if not filters:
            return False
        aircraft_text = norm(f"{aircraft.get('name')} {aircraft.get('variant')} {aircraft.get('class')}")
        compact = aircraft_text.replace(" ", "")
        return all(all(part.replace(" ", "") in compact for part in filter_text.split()) for filter_text in filters)

    def objective_aircraft(self) -> list[dict[str, Any]]:
        """Find embarked aircraft selected by Fleet Command goal filters."""
        goals = [goal for goal in self.data.get("triggers_goals", [])
                 if isinstance(goal, dict) and int(float(goal.get("goaltype") or 0)) in {0, 3}]
        matches = [goal for goal in goals if goal.get("class") or goal.get("shipname") or goal.get("tagname")]
        if not matches:
            return []
        output: list[dict[str, Any]] = []
        for vessel in self.data.get("vessels", []) if isinstance(self.data.get("vessels"), list) else []:
            if not isinstance(vessel, dict) or self.role(vessel) != "friendly":
                continue
            for attachment in vessel.get("aircraft", []) if isinstance(vessel.get("aircraft"), list) else []:
                if not isinstance(attachment, dict) or not any(self.goal_aircraft_matches(attachment, goal) for goal in matches):
                    continue
                output.append(attachment)
        return output

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
        reference = self.resolver.national_reference(item, definition, folder)
        if folder == "vessels" and reference:
            lines.insert(2, f"VariantReference={reference}")
        if source == "vessels" and item.get("aircraft"):
            path = self.resolver.root / "vessels" / f"{definition}.ini"
            try:
                has_deck = bool(re.search(r"(?m)^\[FlightDeck\]\s*$", path.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                has_deck = False
            if has_deck:
                self.add_flight_deck(lines, item)
                self.warn(f"aircraft attached to {item.get('uniquename')!r} remain internal to their base unit and were not spawned")
            else:
                self.warn(f"{definition} has no flight deck; aircraft aboard {item.get('uniquename')!r} cannot be represented")
        if category in {"aircraft", "helicopter"}:
            # Sea Power resolves aircraft loadout/flight behaviour through a
            # squadron reference. Leaving this implicit can cause a null
            # reference in the mission editor even when the aircraft type is
            # valid.
            lines.insert(2, f"SquadronReference={reference or 'Default'}")
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
            grouped.setdefault((self.taskforce_for(item), group_id), []).append(item)
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
        # Fleet Command installations generally have considerably fewer hit
        # points than the corresponding Sea Power scenery entity.  Sea Power
        # does not expose a numeric hit-point override in a mission file, but
        # it does support the same damage state used by the mission editor.
        # Starting these large replacement models heavily damaged keeps a
        # converted building/SAM from becoming an unexpectedly durable target.
        if self.land_cluster_kind(item) in {"building", "sam"} or self.is_airbase(item, definition):
            lines.append("Damage=Heavy")
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
        text = norm(f"{item.get('name')} {item.get('class')} {item.get('entity_type')} {definition}")
        if self.is_airbase(item, definition) or "ssm" in text or "silkworm" in text:
            return None
        # Database names vary between scenarios (SAM, SA-2, PVO, etc.).
        # Include the entity type because some records have no useful class.
        sam_tokens = ("sam", "surface to air", "air defense", "antiair", "skyguard",
                      "sa 2", "sa 3", "sa 10", "hawk", "roland", "crotale",
                      "chaparral", "rapier")
        if any(token in text for token in sam_tokens) or ("launcher" in text and "building" not in text):
            return "sam"
        name = norm(str(item.get("name") or ""))
        building_names = {"bunker", "building2", "building4", "building5", "chemicalplant",
                          "dock", "drugfactory", "factory", "hangar", "hospital", "piers",
                          "powerplant", "sonalysts", "trainingfacility", "whouse"}
        if (str(item.get("entity_type") or "").upper() == "BUILDINGENTITY"
                or definition.startswith(("civ_comm_buildings", "tgt_", "warehouses_", "airfield_"))
                or name in building_names or name.startswith(("tent ", "building"))):
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
                grouped.setdefault((self.taskforce_for(item), kind), []).append(item)
            else:
                clusters.append([item])
        for (_, _), candidates in grouped.items():
            radius = self.SAM_CLUSTER_RADIUS_NM if candidates and self.land_cluster_kind(candidates[0]) == "sam" else self.LAND_CLUSTER_RADIUS_NM
            # Complete-linkage clustering: every member must be close to all
            # existing members.  A union-find/transitive merge lets a long
            # chain of tiny buildings turn into one very large replacement
            # model, which is exactly the overlap problem this conversion is
            # meant to avoid.
            grouped_candidates: list[list[dict[str, Any]]] = []
            for item in candidates:
                for cluster in grouped_candidates:
                    if all(self.land_distance(item, other) <= radius for other in cluster):
                        cluster.append(item)
                        break
                else:
                    grouped_candidates.append([item])
            clusters.extend(grouped_candidates)
        return clusters

    def land_cluster_items(self, clusters: list[list[dict[str, Any]]]) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
        """Build renderable representatives, retaining merged source members."""
        result: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
        for cluster in clusters:
            item = cluster[0]
            if len(cluster) > 1:
                item = dict(item)
                points = [member.get("position") or {} for member in cluster]
                item["position"] = {
                    "latitude": sum(float(point.get("latitude", self.center_lat)) for point in points) / len(points),
                    "longitude": sum(float(point.get("longitude", self.center_lon)) for point in points) / len(points),
                    "altitude": sum(float(point.get("altitude") or 0) for point in points) / len(points),
                }
            result.append((cluster, item))
        self.arrange_land_items(result)
        return result

    def collision_radius_nm(self, item: dict[str, Any]) -> float | None:
        """Read a land definition's [coll_selection] footprint in nautical miles.

        Sea Power's scenario positions use metres (60 map units per NM), while
        the definition dimensions are model metres.  A bounding circle is used
        here deliberately: it remains safe when the unit's heading rotates the
        rectangular selection box.
        """
        definition = self.resolver.resolve(item, "land_units")
        if self.land_cluster_kind(item) not in {"building", "sam"} and not self.is_airbase(item, definition or ""):
            return None
        root = getattr(self.resolver, "root", None)
        path = root / "land_units" / f"{definition}.ini" if root and definition else None
        if path is None or not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        match = re.search(r"(?ms)^\[coll_selection\]\s*(.*?)(?=^\[|\Z)", text)
        if not match:
            # Some composite installations do not define a selection box.
            # Use the outermost physical collider instead of treating them as
            # zero-sized; this covers warehouse and airbase-style definitions.
            collider_radii: list[float] = []
            for collider in re.finditer(r"(?ms)^\[(coll_[^\]]+)\]\s*(.*?)(?=^\[|\Z)", text):
                section = collider.group(2)
                kind = re.search(r"(?m)^Collider=\s*(\w+)", section)
                position = re.search(r"(?m)^Position=\s*([-+.0-9eE]+)\s*,\s*[-+.0-9eE]+\s*,\s*([-+.0-9eE]+)", section)
                scale = re.search(r"(?m)^Scale=\s*([-+.0-9eE]+)\s*,\s*[-+.0-9eE]+\s*,\s*([-+.0-9eE]+)", section)
                if not kind or not position or not scale:
                    continue
                try:
                    offset = math.hypot(float(position.group(1)), float(position.group(2)))
                    dimensions = (float(scale.group(1)), float(scale.group(2)))
                except ValueError:
                    continue
                if kind.group(1).lower() == "sphere":
                    radius = max(abs(dimensions[0]), abs(dimensions[1])) + offset
                else:
                    radius = math.hypot(abs(dimensions[0]), abs(dimensions[1])) / 2 + offset
                collider_radii.append(radius)
            if collider_radii:
                return max(collider_radii) * self.LAND_GROUND_FOOTPRINT_FACTOR / 1852
            # A number of small installations have no selection collider in
            # their definition.  Their authoritative footprint is still
            # available in the general Length/Beam fields.
            dimensions = [re.search(rf"(?m)^{name}=\s*([-+.0-9eE]+)", text)
                          for name in ("Length", "Beam")]
            if not all(dimensions):
                return None
            try:
                return (math.hypot(float(dimensions[0].group(1)), float(dimensions[1].group(1)))
                        * self.LAND_GROUND_FOOTPRINT_FACTOR / (2 * 1852))
            except (AttributeError, ValueError):
                return None
        section = match.group(1)

        def vector(name: str) -> tuple[float, float, float] | None:
            value = re.search(rf"(?m)^{name}=\s*([-+.0-9eE]+)\s*,\s*([-+.0-9eE]+)\s*,\s*([-+.0-9eE]+)", section)
            if not value:
                return None
            try:
                return tuple(float(value.group(index)) for index in range(1, 4))
            except ValueError:
                return None

        scale = vector("Scale")
        position = vector("Position") or (0.0, 0.0, 0.0)
        if not scale:
            return None
        half_diagonal = math.hypot(abs(scale[0]), abs(scale[2])) / 2
        return ((half_diagonal + math.hypot(position[0], position[2]))
                * self.LAND_GROUND_FOOTPRINT_FACTOR / 1852)

    def arrange_land_items(self, items: list[tuple[list[dict[str, Any]], dict[str, Any]]]) -> None:
        """Nudge installation representatives until selection footprints clear."""
        footprints = [(item, self.collision_radius_nm(item)) for _, item in items]
        # A small gap avoids touching collider edges and makes the layout less
        # sensitive to floating-point rounding in the game/editor.
        gap_nm = 2.0 / 1852
        largest_radius = max((radius or 0 for _, radius in footprints), default=0)
        if not largest_radius:
            return
        grid_step = largest_radius * 2 + gap_nm
        scale = 60 * math.cos(math.radians(self.center_lat))
        placed: list[tuple[dict[str, Any], float]] = []
        # A deterministic square-spiral candidate list gives every item a
        # position that is clear of all earlier items.  Unlike pairwise
        # repulsion, it cannot finish with an old overlap reintroduced by a
        # later correction in a dense installation cluster.
        candidates = [(x, z) for x in range(-12, 13) for z in range(-12, 13)]
        candidates.sort(key=lambda offset: (offset[0] * offset[0] + offset[1] * offset[1], offset[0], offset[1]))
        for item, radius in footprints:
            if radius is None:
                continue
            point = item.setdefault("position", {})
            origin_east = (float(point.get("longitude", self.center_lon)) - self.center_lon) * scale
            origin_north = (float(point.get("latitude", self.center_lat)) - self.center_lat) * 60
            for grid_x, grid_z in candidates:
                east = origin_east + grid_x * grid_step
                north = origin_north + grid_z * grid_step
                if all(math.hypot(east - other_east, north - other_north) >= radius + other_radius + gap_nm
                       for other, other_radius in placed
                       for other_east, other_north in [((float(other.get("position", {}).get("longitude", self.center_lon)) - self.center_lon) * scale,
                                                       (float(other.get("position", {}).get("latitude", self.center_lat)) - self.center_lat) * 60)]):
                    point["longitude"] = self.center_lon + east / scale
                    point["latitude"] = self.center_lat + north / 60
                    placed.append((item, radius))
                    break

    def land_distance(self, first: dict[str, Any], second: dict[str, Any]) -> float:
        """Return the horizontal distance between two land records in NM."""
        first_point = first.get("position") or {}
        second_point = second.get("position") or {}
        latitude = (float(first_point.get("latitude", self.center_lat))
                    + float(second_point.get("latitude", self.center_lat))) / 2
        east = (float(first_point.get("longitude", self.center_lon))
                - float(second_point.get("longitude", self.center_lon))) * 60 * math.cos(math.radians(latitude))
        north = (float(first_point.get("latitude", self.center_lat))
                 - float(second_point.get("latitude", self.center_lat))) * 60
        return math.hypot(east, north)

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
        if self.is_airbase(item, ""):
            return blocks
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
