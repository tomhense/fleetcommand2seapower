"""Trigger and objective conversion rules."""

from __future__ import annotations

import re
from typing import Any

from ..generator_utils import ini_text, norm

class ObjectiveMixin:
    GENERIC_GOAL_FILTERS = {
        "air", "aircraft", "fighter", "fighter attack", "fighterattack", "bomber",
        "helicopter", "helo", "ship", "ships", "vessel", "sub", "submarine",
        "asw", "surface", "land", "land unit", "land units",
    }

    def tasking_goal(self, goal: dict[str, Any], name: str) -> bool:
        return (int(float(goal.get("goaltype") or 0)) == 3
                and int(float(goal.get("messagetypeid") or 0)) == 1
                and bool(goal.get("message"))
                and not re.search(r"\bend\s*mission\b|\bmission\s+(?:over|incomplete)\b|\bquit\b|\bfail", name, re.IGNORECASE))

    def goal_has_message(self, goal: dict[str, Any]) -> bool:
        name = str(goal.get("goalname") or "")
        return bool(goal.get("message")) and (self.tasking_goal(goal, name) or
                                                int(float(goal.get("goaltype") or 0)) == 0)

    def goal_message(self, goal: dict[str, Any]) -> str:
        if (int(float(goal.get("goaltype") or 0)) == 0 and goal.get("position")
                and any(goal.get(key) for key in ("class", "shipname", "tagname"))):
            # An area condition only establishes presence in the area; the
            # source message may describe actions we cannot represent.
            return "Units have reached the objective area."
        return ini_text(goal.get("message"))

    def goal_unit_type(self, goal: dict[str, Any]) -> str | None:
        """Map Fleet Command's goal class/name filters to Sea Power's unit type."""
        text = norm(" ".join(str(goal.get(key) or "") for key in ("class", "shipname", "tagname")))
        if "helicopter" in text or " helo" in f" {text}":
            return "Helicopter"
        if "aircraft" in text or "fighter" in text or "bomber" in text:
            return "Aircraft"
        if "submarine" in text or " sub " in f" {text} ":
            return "Submarine"
        if "ship" in text or "vessel" in text:
            return "Vessel"
        if any(token in text for token in ("land unit", "building", "sam", "installation")):
            return "LandUnit"
        categories: set[str] = set()
        category_map = {"aircraft": "Aircraft", "helicopter": "Helicopter",
                        "submarine": "Submarine", "vessel": "Vessel"}
        for source in ("vessels", "aircraft", "land_units"):
            for item in self.data.get(source, []) if isinstance(self.data.get(source), list) else []:
                if isinstance(item, dict) and self.goal_filter_matches(goal, item):
                    category = "LandUnit" if source == "land_units" else category_map.get(self.category(item, source))
                    if category:
                        categories.add(category)
        return next(iter(categories)) if len(categories) == 1 else None

    def goal_filter_matches(self, goal: dict[str, Any], item: dict[str, Any]) -> bool:
        filters = [norm(goal.get(key)) for key in ("class", "shipname", "tagname") if norm(goal.get(key))]
        specific_filters = [value for value in filters if value not in self.GENERIC_GOAL_FILTERS]
        if not specific_filters:
            return False
        text = norm(" ".join(str(item.get(key) or "") for key in
                              ("name", "class", "shipname", "tagname", "variant")))
        compact = text.replace(" ", "")
        return any(value in text or value.replace(" ", "") in compact for value in specific_filters)

    def goal_unit_types(self, goal: dict[str, Any]) -> list[str]:
        """Resolve Fleet Command's target filters to exact Sea Power unit types."""
        if not any(norm(goal.get(key)) for key in ("class", "shipname", "tagname")):
            return []
        matches: list[str] = []
        for source, folder in (("vessels", "vessels"), ("aircraft", "aircraft"), ("land_units", "land_units")):
            items = self.data.get(source, [])
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or not self.goal_filter_matches(goal, item):
                    continue
                definition = self.resolver.resolve(item, folder)
                if definition and definition not in matches:
                    matches.append(definition)
        for item in self.objective_aircraft():
            if not self.goal_filter_matches(goal, item):
                continue
            definition = self.resolver.resolve(item, "aircraft")
            if definition and definition not in matches:
                matches.append(definition)
        return matches

    def target_position(self, uniquename: str) -> dict[str, Any] | None:
        for source in ("vessels", "aircraft", "land_units"):
            for item in self.data.get(source, []) if isinstance(self.data.get(source), list) else []:
                if isinstance(item, dict) and str(item.get("uniquename") or "") == uniquename:
                    position = item.get("position")
                    return dict(position) if isinstance(position, dict) else None
        return None

    def target_may_not_spawn(self, goal: dict[str, Any], selected_groups: set[int] | None = None) -> bool:
        target = str(goal.get("uniquename") or "")
        for source in ("vessels", "aircraft", "land_units"):
            for item in self.data.get(source, []) if isinstance(self.data.get(source), list) else []:
                if not isinstance(item, dict) or str(item.get("uniquename") or "") != target:
                    continue
                try:
                    probability = float(item.get("prob", 100))
                    if 0 < probability < 100:
                        return True
                    group_id = item.get("group")
                    if group_id in (selected_groups or set()):
                        return False
                    return any(isinstance(group, dict) and group.get("group_id") == group_id
                               and 0 < float(group.get("probability", 100)) < 100
                               for group in self.data.get("groups", []) if group_id is not None)
                except (TypeError, ValueError):
                    return False
        return False

    def trigger_sections(self, units: dict[str, str], autodetect_units: list[str] | None = None,
                         excluded_groups: set[int] | None = None,
                         selected_groups: set[int] | None = None) -> tuple[list[str], list[str]]:
        triggers, objectives = [], []
        goals = self.data.get("triggers_goals", [])
        excluded_groups = excluded_groups or set()
        target_groups = {str(item.get("uniquename")): item.get("group")
                         for source in ("vessels", "aircraft", "land_units")
                         for item in (self.data.get(source) if isinstance(self.data.get(source), list) else [])
                         if isinstance(item, dict) and item.get("uniquename")}
        inactive = {id(goal) for goal in goals if goal.get("group") in excluded_groups
                    or target_groups.get(str(goal.get("uniquename") or "")) in excluded_groups}
        children_by_parent: dict[int, list[dict[str, Any]]] = {}
        goal_numbers = {id(goal): number for number, goal in enumerate(goals, 1)}
        for child in goals:
            try:
                parent_id = int(float(child.get("parentgoalid", -1)))
            except (TypeError, ValueError):
                parent_id = -1
            children_by_parent.setdefault(parent_id, []).append(child)
        # A parent with no remaining children cannot be completed either.
        while True:
            newly_inactive = set()
            for goal in goals:
                try:
                    goal_id = int(float(goal.get("goalid", -1)))
                except (TypeError, ValueError):
                    continue
                children = [child for child in children_by_parent.get(goal_id, []) if child is not goal]
                if children and all(id(child) in inactive for child in children):
                    newly_inactive.add(id(goal))
            if newly_inactive <= inactive:
                break
            inactive.update(newly_inactive)
        for n, (objective_id, name) in enumerate(self.objective_labels(), 1):
            goal = goals[n - 1]
            if id(goal) in inactive:
                triggers.append("\n".join([f"[Trigger{n}]", f"Name={name}", "Disabled=True",
                                            "Condition_Condition1_Type=Time", "Condition_Condition1_Time=1",
                                            "ConditionsCompleted=<Condition1>"]))
                continue
            points = int(float(goal.get("points") or 0))
            # Fleet Command's negative goal points are penalties, whereas
            # Sea Power's first objective field is the completion reward and
            # its second field is the failure penalty.
            reward = max(0, points)
            penalty = points if points < 0 else -5
            try:
                goal_id = int(float(goal.get("goalid", -1)))
            except (TypeError, ValueError):
                goal_id = -1
            try:
                parent_id = int(float(goal.get("parentgoalid", -1)))
            except (TypeError, ValueError):
                parent_id = -1
            children = [child for child in children_by_parent.get(goal_id, [])
                        if child is not goal and id(child) not in inactive] if goal_id >= 0 else []
            is_tasking = self.tasking_goal(goal, name) and not children
            if not is_tasking:
                objectives.append(f"{objective_id}={reward},{penalty},Complete,Main")
            target = str(goal.get("uniquename") or "")
            target_section = units.get(target)
            goal_type_value = goal.get("goaltype")
            goal_type = int(float(goal_type_value)) if goal_type_value is not None else 2
            goal_start = int(float(goal.get("starttime") or 0))
            time_goal = bool(re.search(r"\bend\s*mission\b|\bmission\s+over\b|\bquit\b|\bfail", name, re.IGNORECASE))
            all_enemy_aircraft = bool(re.search(r"hostile.*air|all.*air|air.*destroyed", name, re.IGNORECASE))
            ctype = None
            condition_types: list[str] | None = None
            completion_expression: str | None = None
            if goal_type == 3 and children:
                condition_lines = []
                condition_types = []
                terms = []
                for child in children:
                    condition_types.append("TriggerCompleted")
                    number = len(condition_types)
                    condition_lines.append(f"Condition_Condition{number}_Triggers=Trigger{goal_numbers[id(child)]}")
                    term = f"<Condition{number}>"
                    section = units.get(str(child.get("uniquename") or ""))
                    if section and self.target_may_not_spawn(child, selected_groups):
                        # An absent optional unit has no destruction event. A
                        # globe-wide area check makes it optional while still
                        # requiring every unit that did spawn to be destroyed.
                        condition_types.append("UnitsInTheArea")
                        area_number = len(condition_types)
                        condition_lines.extend((
                            f"Condition_Condition{area_number}_Units={section}",
                            f"Condition_Condition{area_number}_PositionNM=geo,{self.center_lat:.6f},{self.center_lon:.6f},0",
                            f"Condition_Condition{area_number}_AreaRadiusNM=12000",
                            f"Condition_Condition{area_number}_AreaDisplaySide=None",
                            f"Condition_Condition{area_number}_MinimumUnits=1",
                        ))
                        taskforce = re.match(r"^(Taskforce\d+|Neutral)", section)
                        if taskforce:
                            condition_lines.append(f"Condition_Condition{area_number}_Taskforce={taskforce.group(1)}")
                        term = f"({term} OR NOT <Condition{area_number}>)"
                    terms.append(term)
                start_hour = int(float(self.data.get("metadata", {}).get("timeofday") or 0)) % 24
                elapsed_start = goal_start - start_hour * 3600
                if elapsed_start > 0:
                    condition_types.append("Time")
                    number = len(condition_types)
                    condition_lines.append(f"Condition_Condition{number}_Time={elapsed_start}")
                    terms.append(f"<Condition{number}>")
                condition = "\n".join(condition_lines)
                completion_expression = " AND ".join(terms)
            elif is_tasking:
                # Fleet Command goal times use seconds since midnight; Sea
                # Power's Time condition counts seconds since mission start.
                start_hour = int(float(self.data.get("metadata", {}).get("timeofday") or 0)) % 24
                elapsed = max(1, goal_start - start_hour * 3600)
                condition = f"Condition_Condition1_Time={elapsed}"
                ctype = "Time"
            elif target_section and goal_type == 2:
                condition = f"Condition_Condition1_Units={target_section}"
                ctype = "UnitDestroyed"
                if float(goal.get("damage") or 100) < 95:
                    self.warn(f"goal {name!r} requires partial damage; using Sea Power UnitDestroyed workaround")
            elif goal_type == 3 and all_enemy_aircraft:
                condition_lines = []
                condition_types = []
                for condition_number, taskforce in enumerate(self.enemy_taskforce_prefixes, 1):
                    condition_lines.extend((f"Condition_Condition{condition_number}_Taskforce={taskforce}",
                                            f"Condition_Condition{condition_number}_UnitType=Aircraft"))
                    condition_types.append("HasNoUnitsOfType")
                condition = "\n".join(condition_lines)
            elif goal_type == 3 and time_goal and goal_start > 0:
                condition = f"Condition_Condition1_Time={goal_start}"
                ctype = "Time"
            elif goal_type in {0, 3} and goal.get("position") and (goal_type == 3 or goal.get("class") or goal.get("shipname") or goal.get("tagname")):
                trigger_point = self.target_position(target) or dict(goal.get("position"))
                trigger_point["altitude"] = 0
                filtered_unit_goal = bool(goal.get("class") or goal.get("shipname") or goal.get("tagname"))
                if not filtered_unit_goal:
                    player_units = sorted({section for section in units.values() if section.startswith("Taskforce1")})
                # Geo coordinates match current editor saves and remain stable
                # with Sea Power's globe/LCC map projection.
                condition = (f"Condition_Condition1_PositionNM=geo,{float(trigger_point.get('latitude', self.center_lat)):.6f},"
                             f"{float(trigger_point.get('longitude', self.center_lon)):.6f},0\n"
                             "Condition_Condition1_AreaRadiusNM=5\n"
                             "Condition_Condition1_AreaDisplaySide=None")
                if not filtered_unit_goal and player_units:
                    condition += f"\nCondition_Condition1_Units={','.join(player_units)}"
                condition += "\nCondition_Condition1_Taskforce=Taskforce1\nCondition_Condition1_MinimumUnits=1"
                if filtered_unit_goal:
                    unit_types = self.goal_unit_types(goal)
                    unit_type = self.goal_unit_type(goal)
                    if unit_types:
                        # The mission editor stores the broad category and
                        # exact definition filter together on area conditions.
                        if unit_type:
                            condition += f"\nCondition_Condition1_UnitType={unit_type}"
                        condition += f"\nCondition_Condition1_UnitTypes={','.join(unit_types)}"
                    elif unit_type:
                        condition += f"\nCondition_Condition1_UnitType={unit_type}"
                        self.warn(f"goal {name!r} target filter could not be mapped to exact Sea Power unit types; using {unit_type}")
                    else:
                        self.warn(f"goal {name!r} target filter could not be mapped to exact Sea Power unit types")
                ctype = "UnitsInTheArea"
            elif target_section:
                condition = f"Condition_Condition1_Units={target_section}"
                ctype = "UnitDestroyed"
                self.warn(f"goal {name!r} has unsupported goal type {goal_type}; using UnitDestroyed")
            else:
                condition = f"Condition_Condition1_Time={max(1, goal_start)}"
                ctype = "Time"
                if target: self.warn(f"goal {name!r} target {target!r} was not emitted or could not be linked")
            lines = [f"[Trigger{n}]", f"Name={name}"]
            if not is_tasking:
                lines.append(f"Action_ObjectivesCompleted={objective_id}")
            if self.goal_has_message(goal):
                lines.append(f"Action_Taskforce1_Message=Taskforce1Goal{n}Message")
            if goal_type == 3 and not is_tasking and (not children or parent_id < 0):
                failed = bool(re.search(r"\bfail|\bincomplete\b|\bimpossible\b|\bend\s*mission\b|\bquit\b", name, re.IGNORECASE))
                victory = "Taskforce2" if failed else "Taskforce1"
                lines += ["Action_EndMission=True", f"Action_Victory={victory}"]
            if condition_types:
                lines.extend(f"Condition_Condition{number}_Type={condition_type}"
                             for number, condition_type in enumerate(condition_types, 1))
            elif ctype:
                lines.append(f"Condition_Condition1_Type={ctype}")
            condition_count = len(condition_types) if condition_types else max(
                [int(number) for number in re.findall(r"Condition_Condition(\d+)_", condition)] or [1])
            lines += [condition, "ConditionsCompleted=" + (completion_expression or " AND ".join(
                f"<Condition{number}>" for number in range(1, condition_count + 1)))]
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
