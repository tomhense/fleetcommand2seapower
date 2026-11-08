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
        return None

    def goal_unit_types(self, goal: dict[str, Any]) -> list[str]:
        """Resolve Fleet Command's target filters to exact Sea Power unit types."""
        filters = [norm(goal.get(key)) for key in ("class", "shipname", "tagname") if norm(goal.get(key))]
        specific_filters = [value for value in filters if value not in self.GENERIC_GOAL_FILTERS]
        if not specific_filters:
            return []
        matches: list[str] = []
        for source, folder in (("vessels", "vessels"), ("aircraft", "aircraft"), ("land_units", "land_units")):
            items = self.data.get(source, [])
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                text = norm(" ".join(str(item.get(key) or "") for key in
                                      ("name", "class", "shipname", "tagname", "variant")))
                compact = text.replace(" ", "")
                if not any(value in text or value.replace(" ", "") in compact for value in specific_filters):
                    continue
                definition = self.resolver.resolve(item, folder)
                if definition and definition not in matches:
                    matches.append(definition)
        for item in self.objective_aircraft():
            text = norm(" ".join(str(item.get(key) or "") for key in ("name", "class", "variant")))
            compact = text.replace(" ", "")
            if not any(value in text or value.replace(" ", "") in compact for value in specific_filters):
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
            goal_type_value = goal.get("goaltype")
            goal_type = int(float(goal_type_value)) if goal_type_value is not None else 2
            goal_start = int(float(goal.get("starttime") or 0))
            time_goal = bool(re.search(r"\bend\s*mission\b|\bmission\s+over\b|\bquit\b|\bfail", name, re.IGNORECASE))
            all_enemy_aircraft = bool(re.search(r"hostile.*air|all.*air|air.*destroyed", name, re.IGNORECASE))
            ctype = None
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
            elif goal_type in {0, 3} and goal.get("position") and (goal_type == 3 or goal.get("class") or goal.get("shipname") or goal.get("tagname")):
                trigger_point = self.target_position(target) or dict(goal.get("position"))
                trigger_point["altitude"] = 0
                filtered_unit_goal = bool(goal.get("class") or goal.get("shipname") or goal.get("tagname"))
                if not filtered_unit_goal:
                    player_units = sorted({section for section in units.values() if section.startswith("Taskforce1")})
                condition = (f"Condition_Condition1_PositionNM={self.pos(trigger_point)}\n"
                             "Condition_Condition1_AreaRadiusNM=5\n"
                             "Condition_Condition1_AreaDisplaySide=None")
                if not filtered_unit_goal and player_units:
                    condition += f"\nCondition_Condition1_Units={','.join(player_units)}"
                condition += "\nCondition_Condition1_Taskforce=Taskforce1\nCondition_Condition1_MinimumUnits=1"
                if filtered_unit_goal:
                    unit_types = self.goal_unit_types(goal)
                    if unit_types:
                        condition += f"\nCondition_Condition1_UnitTypes={','.join(unit_types)}"
                    elif unit_type := self.goal_unit_type(goal):
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
            lines = [f"[Trigger{n}]", f"Name={name}", f"Action_ObjectivesCompleted={objective_id}"]
            if goal_type == 3:
                victory = "Taskforce1" if re.search(r"mission\s+over", name, re.IGNORECASE) else ("Taskforce2" if time_goal else "Taskforce1")
                lines += ["Action_EndMission=True", f"Action_Victory={victory}"]
            if ctype:
                lines.append(f"Condition_Condition1_Type={ctype}")
            condition_count = condition.count("_Type=") or 1
            lines += [condition, "ConditionsCompleted=" + " AND ".join(
                f"<Condition{number}>" for number in range(1, condition_count + 1))]
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
