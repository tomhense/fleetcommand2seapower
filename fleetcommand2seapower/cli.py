"""Unified command-line interface for Fleet Command to Sea Power conversion."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from .scs_converter import parse
from .seapower_generator import Generator, Resolver, quality_entry

SCENARIO_SUFFIXES = {".scs", ".sct", ".scm", ".scc"}
JSON_SUFFIXES = {".json"}
LOGGER = logging.getLogger(__name__)


def clean_stem(path: Path) -> str:
    return path.stem.strip() or path.stem


def default_output_path(path: Path, suffix: str) -> Path:
    return path.with_name(f"{clean_stem(path)}{suffix}")


def default_scenario_dir() -> Path:
    """Return Fleet Command's conventional installed scenario directory."""
    if os.name == "nt":
        program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get(
            "PROGRAMFILES", r"C:\Program Files (x86)")
        return Path(program_files) / "Steam" / "steamapps" / "common" /             "Fleet Command" / "scenario"
    return Path.home() / ".local" / "share" / "Steam" / "steamapps" /         "common" / "Fleet Command" / "scenario"


def default_streaming_assets() -> Path:
    """Return Sea Power's conventional installed StreamingAssets directory."""
    if os.name == "nt":
        program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES") or r"C:\Program Files (x86)"
        return Path(program_files) / "Steam" / "steamapps" / "common" / "Sea Power" / "Sea Power_Data" / "StreamingAssets" / "original"
    return Path.home() / ".local" / "share" / "Steam" / "steamapps" / "common" / "Sea Power" / "Sea Power_Data" / "StreamingAssets" / "original"


def default_mapping_path() -> Path:
    """Return the mapping bundled with the Python package."""
    return Path(__file__).with_name("seapower_mapping.json")

def default_info_path() -> Path:
    """Return the bundled Sea Power scenario metadata template."""
    return Path(__file__).with_name("_info.ini")


def converter_json(source: Path, origin_x: float = 555600, origin_y: float = 555600,
                   units_per_degree: float = 111120) -> str:
    """Run conversion and return the JSON-string intermediate."""
    return json.dumps(parse(source, origin_x, origin_y, units_per_degree),
                      ensure_ascii=False)


def _add_converter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", type=Path, nargs="?", default=default_scenario_dir(),
                        help="scenario file or directory "
                             f"(default: {default_scenario_dir()})")
    parser.add_argument("-o", "--output", type=Path,
                        help="output JSON path for one file")
    parser.add_argument("--output-dir", type=Path,
                        help="output directory for batch conversion "
                             "(default: FleetCommand_scenarios_converted)")
    parser.add_argument("--origin-x", type=float, default=555600)
    parser.add_argument("--origin-y", type=float, default=555600)
    parser.add_argument("--units-per-degree", type=float, default=111120)


def run_scs(args: argparse.Namespace) -> int:
    if args.input.is_dir():
        if args.output:
            args.parser.error("--output cannot be used with a directory; use --output-dir")
        output_dir = args.output_dir or Path("FleetCommand_scenarios_converted")
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(default_info_path(), output_dir / "_info.ini")
        sources = sorted(p for p in args.input.iterdir()
                         if p.suffix.lower() in SCENARIO_SUFFIXES)
        failures = 0
        for source in sources:
            output = output_dir / f"{clean_stem(source)}.json"
            try:
                output.write_text(
                    converter_json(source, args.origin_x, args.origin_y,
                                   args.units_per_degree) + "\n",
                    encoding="utf-8")
            except Exception as exc:
                failures += 1
                LOGGER.error("Failed to parse %s: %s", source, exc)
            else:
                print(f"Wrote {output}")
        print(f"Converted {len(sources) - failures} of {len(sources)} scenarios "
              f"to {output_dir}")
        return 1 if failures else 0

    rendered = converter_json(args.input, args.origin_x, args.origin_y,
                              args.units_per_degree) + "\n"
    if args.output_dir:
        args.parser.error("--output-dir requires a directory input")
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


def _add_generator_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", type=Path, nargs="?", default=default_scenario_dir(),
                        help=f"Fleet Command JSON, scenario file, or scenario directory (default: {default_scenario_dir()})")
    parser.add_argument("output", type=Path, nargs="?",
                        help="INI output, or batch output directory")
    parser.add_argument("--streaming-assets", type=Path,
                        default=default_streaming_assets(), help=f"Sea Power StreamingAssets directory (default: {default_streaming_assets()})")
    parser.add_argument("--mapping", type=Path, default=default_mapping_path(), help=f"mapping file (default: {default_mapping_path()})")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--year", type=int, default=1986)
    parser.add_argument("--debug", action="store_true", help="write quality_report.json")


def _source_json(source: Path) -> dict:
    # Keep the converter/generator boundary explicitly JSON-string based.
    if source.suffix.lower() in SCENARIO_SUFFIXES:
        return json.loads(converter_json(source))
    return json.loads(source.read_text(encoding="utf-8"))


def run_generator(args: argparse.Namespace) -> int:
    resolver = Resolver(args.streaming_assets, args.mapping, LOGGER.warning)

    if args.input.is_dir():
        output_dir = args.output_dir or args.output or Path("SeaPower_Missions_Generated")
        output_dir.mkdir(parents=True, exist_ok=True)
        sources = sorted(p for p in args.input.iterdir()
                         if p.suffix.lower() in SCENARIO_SUFFIXES | JSON_SUFFIXES)
        report, failures = [], 0
        for source in sources:
            output = output_dir / f"{clean_stem(source)}.ini"
            try:
                data = _source_json(source)
                generator = Generator(data, resolver, args.year)
                rendered = generator.render()
                output.write_text(rendered, encoding="utf-8", newline="\n")
                for warning in generator.warnings:
                    LOGGER.warning("%s: %s", source.name.strip(), warning)
                report.append(quality_entry(source, output, data, rendered,
                                            generator.warnings))
            except Exception as exc:
                failures += 1
                LOGGER.error("Failed to convert %s: %s", source, exc)
                report.append({"source": source.name.strip(), "output": output.name,
                               "status": "failed", "error": str(exc)})
        if args.debug:
            report_path = args.report or output_dir / "quality_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                "scenarios": report,
                "summary": {
                    "total": len(sources), "generated": len(sources) - failures,
                    "failed": failures,
                    "warnings": sum(len(e.get("warnings", [])) for e in report),
                },
            }, indent=2) + "\n", encoding="utf-8")
            LOGGER.info("Quality report: %s", report_path)
        LOGGER.info("Generated %s of %s missions in %s",
                    len(sources) - failures, len(sources), output_dir)
        return 1 if failures else 0

    if args.input.suffix.lower() in SCENARIO_SUFFIXES:
        data = _source_json(args.input)
        output = args.output or default_output_path(args.input, ".ini")
    elif args.input.suffix.lower() == ".json":
        output = args.output or default_output_path(args.input, ".ini")
        data = _source_json(args.input)
    else:
        args.parser.error("a single input must be custom JSON or a Fleet Command scenario")

    generator = Generator(data, resolver, args.year)
    output.write_text(generator.render(), encoding="utf-8", newline="\n")
    for warning in generator.warnings:
        LOGGER.warning("%s", warning)
    LOGGER.info("Wrote %s (%s warning(s))", output, len(generator.warnings))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleetcommand2seapower")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scs = subparsers.add_parser(
        "parse",
        help="convert Fleet Command scenarios to JSON")
    _add_converter_options(scs)
    scs.set_defaults(handler=run_scs, parser=scs)

    generator = subparsers.add_parser(
        "convert",
        help="convert scenarios into Sea Power missions")
    _add_generator_options(generator)
    generator.set_defaults(handler=run_generator, parser=generator)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "debug", False) else logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
