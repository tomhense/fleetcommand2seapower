"""Coordinate and source-unit classification helpers."""

from __future__ import annotations

import math
import re
from typing import Any

from ..generator_utils import norm

class GeometryMixin:
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
        if not role:
            alliance_id = item.get("alliance_id", item.get("alliance"))
            try:
                alliance_id = int(alliance_id)
            except (TypeError, ValueError):
                alliance_id = None
            for alliance in self.data.get("alliances", []) if isinstance(self.data.get("alliances"), list) else []:
                if isinstance(alliance, dict) and alliance.get("id") == alliance_id:
                    role = str(alliance.get("role") or "").lower()
                    break
        if role in {"enemy", "hostile", "threat"}: return "enemy"
        if role in {"neutral", "unknown"}: return "neutral"
        return "friendly"

    def alliance_id(self, item: dict[str, Any]) -> int | None:
        value = item.get("alliance_id", item.get("alliance"))
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def taskforce_for(self, item: dict[str, Any]) -> str:
        """Return the Sea Power taskforce assigned to a Fleet Command entity."""
        role = self.role(item)
        if role == "neutral":
            return "Neutral"
        if role == "enemy":
            return self.enemy_taskforces.get(self.alliance_id(item), "Taskforce2")
        return "Taskforce1"

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

