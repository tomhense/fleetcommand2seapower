"""Public compatibility facade for Sea Power mission generation."""

from __future__ import annotations

from typing import Any

from .generator_utils import ini_text, norm, role_prefix, scalar
from .generator_parts.geometry import GeometryMixin
from .generator_parts.objectives import ObjectiveMixin
from .generator_parts.render import RenderMixin
from .generator_parts.resolver import Resolver
from .generator_parts.units import UnitMixin
from .generator_reporting import quality_entry, warning_category

class Generator(GeometryMixin, UnitMixin, ObjectiveMixin, RenderMixin):
    """Coordinate the specialized conversion components."""

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
