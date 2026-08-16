"""Sea Power definition and name resolver."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from ..generator_utils import norm

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
    def conditional_candidates(self, item: dict[str, Any], category: str, name: str, cls: str) -> list[str]:
        """Return candidates from declarative category/name/country rules."""
        origin = norm(self.land_origin(item))
        name_text = norm(name)
        class_text = norm(cls)
        for rule in self.mapping.get("conditional_map", []):
            if not isinstance(rule, dict) or rule.get("category") != category:
                continue
            match = rule.get("match", {})
            if not isinstance(match, dict):
                continue
            names = match.get("name", [])
            classes = match.get("class", [])
            names = [names] if isinstance(names, str) else names
            classes = [classes] if isinstance(classes, str) else classes
            name_match = any(norm(value) == name_text for value in names) if names else False
            class_match = any(norm(value) in class_text for value in classes) if classes else False
            if not (name_match or class_match):
                continue
            country_map = rule.get("country_map", {})
            if not isinstance(country_map, dict):
                continue
            for country, candidates in country_map.items():
                if country != "*" and norm(country) != origin:
                    continue
                if isinstance(candidates, str):
                    return [candidates]
                if isinstance(candidates, list):
                    return [candidate for candidate in candidates if isinstance(candidate, str)]
        return []

    def resolve(self, item: dict[str, Any], category: str) -> str | None:
        name = str(item.get("name") or item.get("shipname") or "")
        cls = str(item.get("class") or "")
        candidates = self.conditional_candidates(item, category, name, cls)
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
