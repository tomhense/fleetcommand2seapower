"""Trigger and objective conversion rules."""

from __future__ import annotations

import re
from typing import Any

from ..generator_utils import ini_text

class ObjectiveMixin:
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

