"""Mission-level rendering orchestration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..generator_utils import ini_text

class RenderMixin:
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
        taskforces = list(dict.fromkeys(["Taskforce1", *self.enemy_taskforce_prefixes, "Neutral"]))
        categories = ("Vessel", "Submarine", "Aircraft", "Helicopter", "LandUnit")
        counts = {prefix + category: 0 for prefix in taskforces for category in categories}
        group_refs: dict[str, dict[int, list[str]]] = {prefix: {} for prefix in taskforces}
        group_has_routes: dict[str, set[int]] = {prefix: set() for prefix in taskforces}
        autodetect_units: list[str] = []
        random_groups = self.probability_groups("vessels")
        random_group_members = {id(item) for members in random_groups.values() for item in members}
        random_group_emitted: set[tuple[str, int]] = set()
        random_group_ids: dict[str, set[int]] = {prefix: set() for prefix in taskforces}
        aircraft_items = list(self.data.get("aircraft", [])) if isinstance(self.data.get("aircraft"), list) else []
        aircraft_items.extend(self.objective_aircraft())
        for source in ("vessels", "aircraft"):
            source_items = aircraft_items if source == "aircraft" else self.data.get(source, [])
            for item in source_items if isinstance(source_items, list) else []:
                role = self.role(item); prefix = self.taskforce_for(item)
                random_key = (prefix, int(item["group"])) if source == "vessels" and isinstance(item.get("group"), (int, float)) else None
                if id(item) in random_group_members:
                    if random_key in random_group_emitted:
                        continue
                    candidates = random_groups[random_key]
                    random_group_emitted.add(random_key)
                    random_group_ids[prefix].add(random_key[1])
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
                    group_refs[prefix].setdefault(random_key[1], []).append(section)
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
                        group_refs[prefix].setdefault(group_id, []).append(section)
                        if isinstance(item.get("waypoints"), list) and item.get("waypoints"):
                            group_has_routes[prefix].add(group_id)
                    if source == "vessels":
                        blocks.extend(self.attached_aircraft(prefix, item, section, counts))
        for cluster, item in self.land_cluster_items(self.land_clusters()):
            role = self.role(item); prefix = self.taskforce_for(item)
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
                    # Fleet Command groups can contain an airbase plus its
                    # aircraft. Sea Power formations are movement formations;
                    # including the land installation makes the airbase part
                    # of an aircraft formation and can prevent it from
                    # spawning/behaving as an airbase. Keep land units out of
                    # tactical formation references.
                    if isinstance(member.get("group"), (int, float)):
                        group_id = int(member["group"])
                        if isinstance(member.get("waypoints"), list) and member.get("waypoints"):
                            group_has_routes[prefix].add(group_id)
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
            taskforce = self.taskforce_for(vessel)
            key = (taskforce, str(leader_name))
            refs = leader_formations.setdefault(key, [])
            for ref in (units[str(leader_name)], units[str(member_name)]):
                if ref not in refs:
                    refs.append(ref)
        for key, count in counts.items():
            suffix = "" if key.endswith("Aircraft") else "s"
            lines.append(f"NumberOf{key}{suffix}={count}")
        group_names = {int(g.get("group_id")): str(g.get("name") or f"Group {g.get('group_id')}")
                       for g in self.data.get("groups", []) if isinstance(g, dict) and isinstance(g.get("group_id"), (int, float))}
        for taskforce in [prefix for prefix in taskforces if prefix != "Neutral"]:
            formations = [(gid, refs) for gid, refs in group_refs[taskforce].items()
                          if refs and gid not in group_has_routes[taskforce] and gid not in random_group_ids[taskforce]]
            linked = [(label, refs) for (formation_role, label), refs in leader_formations.items()
                      if formation_role == taskforce and len(refs) > 1]
            formations.extend((label, refs) for label, refs in linked)
            for gid in sorted(group_has_routes[taskforce] & set(group_refs[taskforce])):
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
        side_taskforces = [prefix for prefix in taskforces if prefix != "Neutral"]
        lines += ["[Sides]", *[f"Side{number}={prefix}" for number, prefix in enumerate(side_taskforces, 1)],
                  f"Side{len(side_taskforces) + 1}=Neutral|Civilian", "[Relations]"]
        lines.extend(f"Taskforce1,{enemy}=Hostile" for enemy in self.enemy_taskforce_prefixes)
        for index, first in enumerate(self.enemy_taskforce_prefixes):
            lines.extend(f"{first},{second}=Neutral" for second in self.enemy_taskforce_prefixes[index + 1:])
        lines.extend(f"{prefix},Neutral=Neutral" for prefix in side_taskforces)
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
