#!/usr/bin/env python3
"""Generate the NATO/MIL-STD-2525 SVG assets used by the map viewer.

The ``military-symbol`` package is installed as a uv tool, so this script
deliberately invokes its command-line entry point instead of importing it
into the converter's Python environment.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


SYMBOLS = {
    "fighter": "fighter",
    "bomber": "bomber",
    "attack": "attack aircraft",
    "helicopter": "helicopter",
    "transport": "transport aircraft",
    "uav": "unmanned aircraft",
    "carrier": "aircraft carrier",
    "cruiser": "cruiser",
    "destroyer": "destroyer",
    "frigate": "frigate",
    "corvette": "corvette",
    # FSG is a guided-missile corvette; the package's corvette entity is the
    # closest supported base symbol, while the application preserves FSG as
    # the subtype.
    "fsg": "corvette",
    "patrol": "patrol boat",
    "submarine": "submarine",
    "ssbn": "nuclear-powered ballistic missile submarine",
    "ssgn": "guided missile submarine",
    "ssn": "attack submarine",
    "ssk": "submarine",
    "sam": "surface-to-air missile battery",
    "mine": "sea mine",
    "armor": "armored unit",
    "artillery": "artillery",
    "land": "infantry",
    "airbase": "air base",
    # Patrol boat is the closest generic surface-unit entity in the package.
    "vessel": "patrol boat",
}
AFFILIATIONS = {
    "friendly": "Friendly",
    "enemy": "Hostile",
    "neutral": "Neutral",
}
AFFILIATION_CODES = {"friendly": "3", "enemy": "6", "neutral": "4"}
FSG_SIDC = "13033000001202051500000000000"


def find_binary(requested: str) -> str:
    found = shutil.which(requested)
    if found:
        return found
    uv = shutil.which("uv")
    if uv:
        result = subprocess.run([uv, "tool", "dir"], check=False, capture_output=True, text=True)
        if result.returncode == 0:
            candidate = Path(result.stdout.strip()) / "military-symbol" / "bin" / requested
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError(
        f"Could not find {requested!r}; install it with 'uv tool install military-symbol' "
        "or pass --binary /path/to/military-symbol"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="military-symbol", help="military-symbol executable")
    parser.add_argument("--output", type=Path, default=Path("assets/symbols"))
    args = parser.parse_args()

    binary = find_binary(args.binary)
    args.output.mkdir(parents=True, exist_ok=True)
    for affiliation, label in AFFILIATIONS.items():
        for key, entity in SYMBOLS.items():
            target = args.output / f"{affiliation}-{key}.svg"
            # Use a temporary output directory because the CLI auto-names SVGs
            # from the generated SIDC. Rename the one output to our stable key.
            temp = args.output / ".generated"
            temp.mkdir(exist_ok=True)
            for old in temp.glob("*.svg"):
                old.unlink()
            if key == "fsg":
                # The resolver does not combine "guided missile" with
                # "corvette", so use the package's explicit capability
                # modifier (M1=15) on the corvette SIDC.
                originator = FSG_SIDC[:3] + AFFILIATION_CODES[affiliation] + FSG_SIDC[4:]
                command = [binary, "-o", str(temp), originator]
            else:
                command = [binary, "-n", "-o", str(temp), f"{label} {entity}"]
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            generated = next(temp.glob("*.svg"), None)
            if generated is None:
                raise RuntimeError(f"No SVG generated for {label} {entity}")
            generated.replace(target)
    shutil.rmtree(args.output / ".generated")
    print(f"Generated {len(SYMBOLS) * len(AFFILIATIONS)} SVG symbols in {args.output}")


if __name__ == "__main__":
    main()
