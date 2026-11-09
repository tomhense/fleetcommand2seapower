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
        self._variant_cache: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self._vessel_names: dict[str, dict[str, str]] | None = None

    def national_reference(self, item: dict[str, Any], definition: str, category: str) -> str | None:
        """Select an available national variant or squadron for a mapped unit."""
        country = norm(item.get("country"))
        aliases = {"united states": "us", "usa": "us", "ussr": "soviet",
                   "soviet union": "soviet", "russia": "soviet", "united kingdom": "uk"}
        country = aliases.get(country, country)
        if not country:
            return None
        suffix = "variants" if category == "vessels" else "squadrons"
        key = (definition, category)
        if key not in self._variant_cache:
            path = self.root / category / f"{definition}_{suffix}.ini"
            try:
                contents = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                contents = ""
            entries: list[tuple[str, str]] = []
            for match in re.finditer(r"(?ms)^\[(Default|Variant\d+|Squadron\d+)\][^\n]*\n(.*?)(?=^\[|\Z)", contents):
                nation = re.search(r"(?m)^\s*Nation\s*=\s*([^/\r\n#]+)", match.group(2))
                if nation:
                    value = norm(nation.group(1))
                    entries.append((match.group(1), aliases.get(value, value)))
            self._variant_cache[key] = entries
        entries = self._variant_cache[key]
        matching = [ref for ref, nation in entries if nation == country]
        if not matching:
            return None
        if category == "vessels" and item.get("shipname"):
            if self._vessel_names is None:
                self._vessel_names = {}
                path = self.root / "language_en" / "vessel_names.ini"
                try:
                    contents = path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    contents = ""
                for match in re.finditer(r"(?ms)^\[([^\]]+)\][^\n]*\n(.*?)(?=^\[|\Z)", contents):
                    self._vessel_names[match.group(1)] = dict(re.findall(r"(?m)^(Default|Variant\d+)\s*=\s*([^\r\n#]+)", match.group(2)))
            names = self._vessel_names.get(definition, {})
            source = norm(item["shipname"])
            source_numbers = set(re.findall(r"\d+", source))
            for ref in matching:
                label = names.get(ref, "")
                if not label:
                    continue
                label_numbers = set(re.findall(r"\d+", norm(label)))
                if source_numbers and source_numbers & label_numbers:
                    return ref
                parts = [norm(part) for part in label.split(",") if norm(part)]
                if any(len(part) >= 4 and part in source for part in parts):
                    return ref
            if "Default" not in matching:
                self.warn(f"no exact {country} variant for {item['shipname']!r} on {definition}; using {matching[0]}")
        # Default already uses this nation; otherwise select a national
        # variant even if the individual ship is unavailable.
        return "Default" if "Default" in matching else matching[0]

    def land_origin(self, item: dict[str, Any]) -> str:
        """Infer the operator of generic Fleet Command installations."""
        direct = str(item.get("country") or item.get("alliance_country") or "").lower()
        if direct not in {"", "neutral", "neutrals", "unknown", "none"}:
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
    def conditional_candidates(self, item: dict[str, Any], category: str, name: str, cls: str) -> tuple[list[str], bool]:
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
            shipnames = match.get("shipname", [])
            names = [names] if isinstance(names, str) else names
            classes = [classes] if isinstance(classes, str) else classes
            shipnames = [shipnames] if isinstance(shipnames, str) else shipnames
            name_match = any(norm(value) == name_text for value in names) if names else False
            class_match = any(norm(value) in class_text for value in classes) if classes else False
            shipname_text = norm(item.get("shipname"))
            shipname_match = any(norm(value) == shipname_text for value in shipnames) if shipnames else False
            if not (name_match or class_match or shipname_match):
                continue
            country_map = rule.get("country_map", {})
            if not isinstance(country_map, dict):
                continue
            # An alliance with several countries is still useful evidence when
            # every listed operator selects the same installation family.
            countries = item.get("alliance_countries")
            if not origin and rule.get("strict_country") and isinstance(countries, list) and countries:
                by_country = {norm(country): candidates for country, candidates in country_map.items() if country != "*"}
                choices = [by_country.get(norm(country)) for country in countries]
                if choices[0] and all(choice == choices[0] for choice in choices):
                    values = choices[0] if isinstance(choices[0], list) else [choices[0]]
                    return [value for value in values if isinstance(value, str)], True
            # An exact country must win even if a custom mapping puts * first.
            matching = [(country, candidates) for country, candidates in country_map.items()
                        if country != "*" and norm(country) == origin]
            if not matching and "*" in country_map:
                matching = [("*", country_map["*"])]
            for country, candidates in matching:
                if isinstance(candidates, str):
                    return [candidates], bool(rule.get("strict_country") and country != "*")
                if isinstance(candidates, list):
                    return [candidate for candidate in candidates if isinstance(candidate, str)], bool(rule.get("strict_country") and country != "*")
        return [], False

    def resolve(self, item: dict[str, Any], category: str) -> str | None:
        name = str(item.get("name") or item.get("shipname") or "")
        cls = str(item.get("class") or "")
        candidates, strict_country = self.conditional_candidates(item, category, name, cls)
        if strict_country:
            available = self.files.get(category, self.all_files)
            for candidate in candidates:
                if candidate in available:
                    return candidate
            self.warn(f"no country-appropriate Sea Power definition found for {category} {name!r}")
            return None
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
