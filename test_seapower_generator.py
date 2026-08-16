#!/usr/bin/env python3
"""Regression tests for Fleet Command JSON to Sea Power mission conversion."""

from __future__ import annotations

import configparser
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fleetcommand2seapower.cli import (
    converter_json,
    default_scenario_dir,
    default_streaming_assets,
)
from fleetcommand2seapower.scs_converter import parse
from fleetcommand2seapower.seapower_generator import Generator, Resolver

ROOT = Path(__file__).parent
ASSETS = default_streaming_assets()
MAPPING = ROOT / "seapower_mapping.json"


@unittest.skipUnless(default_scenario_dir().is_dir() and ASSETS.is_dir(), "Fleet Command and Sea Power Steam fixtures are not installed")
class SeaPowerGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.resolver = Resolver(ASSETS, MAPPING, lambda _: None)
        cls.sources = sorted(path for path in (default_scenario_dir()).iterdir()
                             if path.suffix.lower() in {".scs", ".sct", ".scm", ".scc"})

    def test_generator_accepts_converter_json_string_intermediate(self) -> None:
        source = default_scenario_dir() / " Single06.scs"
        converted_json = converter_json(source)
        rendered = Generator(json.loads(converted_json), self.resolver).render()
        self.assertIn("[Mission]", rendered)
        self.assertIn("NumberOfTriggers=", rendered)

    def test_all_scenarios_render_valid_ini(self) -> None:
        self.assertEqual(len(self.sources), 50)
        for source in self.sources:
            with self.subTest(source=source.name):
                data = parse(source)
                rendered = Generator(data, self.resolver).render()
                parser = configparser.ConfigParser(interpolation=None, strict=True)
                parser.read_string(rendered)
                self.assertIn("Mission", parser)
                self.assertIn("Environment", parser)

    def test_emitted_types_exist(self) -> None:
        available = self.resolver.all_files
        for source in self.sources:
            with self.subTest(source=source.name):
                rendered = Generator(parse(source), self.resolver).render()
                for definition in re.findall(r"^Type=(.+)$", rendered, re.MULTILINE):
                    if definition == "Scenario":
                        continue
                    for option in definition.split(","):
                        self.assertIn(option, available)

    def test_coordinate_center_is_preserved(self) -> None:
        data = parse(default_scenario_dir() / " Single04.scs")
        generator = Generator(data, self.resolver)
        rendered = generator.render()
        self.assertIn("MapCenterLatitude=-2.215835", rendered)
        self.assertIn("MapCenterLongitude=120.044100", rendered)

    def test_air_corridor_becomes_civilian_aircraft(self) -> None:
        data = {
            "metadata": {"latlong": [0, 0], "month": 1, "timeofday": 12},
            "air_corridors": [{
                "name": "Commercial route",
                "start": {"latitude": 0, "longitude": 0, "altitude": 0},
                "end": {"latitude": 1, "longitude": 1, "altitude": 0},
                "speed": 15,
                "minimum_altitude": 5000,
                "maximum_altitude": 10000,
                "aircraft": [{"name": "Airliner", "class": "CIVILIAN AIRCRAFT", "quantity": 2}],
            }],
        }
        generator = Generator(data, self.resolver)
        rendered = generator.render()
        self.assertIn("NumberOfNeutralAircraft=2", rendered)
        self.assertEqual(rendered.count("Type=civ_707"), 2)
        self.assertIn("Waypoints=60.0000,7500.0000,60.0000", rendered)
        self.assertNotIn("cannot be represented exactly", "\n".join(generator.warnings))

    def test_random_box_becomes_closed_patrol_route(self) -> None:
        data = {
            "metadata": {"latlong": [10, 20], "month": 1, "timeofday": 12},
            "aircraft": [{
                "name": "F-14 Tomcat", "class": "FIGHTER", "position": {"latitude": 11, "longitude": 21, "altitude": 5000},
                "tactic": "RANDOMBOX", "box": {"corners": [
                    {"latitude": 10, "longitude": 20}, {"latitude": 12, "longitude": 22}
                ]},
            }],
        }
        rendered = Generator(data, self.resolver).render()
        self.assertIn("MissionType=Patrol", rendered)
        self.assertIn("Waypoints=0.0000,5000.0000,0.0000|118.1769,5000.0000,0.0000|118.1769,5000.0000,120.0000|0.0000,5000.0000,120.0000|0.0000,5000.0000,0.0000", rendered)

    def test_airbase_replaces_default_air_group(self) -> None:
        data = {
            "metadata": {"latlong": [0, 0], "month": 1, "timeofday": 12},
            "land_units": [{
                "name": "Airport", "class": "", "position": {"latitude": 1, "longitude": 2},
                "aircraft": [{"name": "F-14 Tomcat", "class": "FIGHTER", "quantity": 2}],
            }, {
                "name": "Airport", "class": "", "position": {"latitude": 3, "longitude": 4},
                "aircraft": [{"name": "Beechcraft Prop", "class": "CIVILIAN AIRCRAFT", "quantity": 0}],
            }],
        }
        rendered = Generator(data, self.resolver).render()
        self.assertEqual(rendered.count("CustomAirGroup=True"), 2)
        self.assertIn("usn_f-14a=Default,2", rendered)
        self.assertIn("civ_c340=Default,0", rendered)
        self.assertNotIn("usn_f-14a=Default,0", rendered)

    def test_autodetect_entities_are_revealed_at_mission_start(self) -> None:
        data = {
            "metadata": {"latlong": [0, 0], "month": 1, "timeofday": 12},
            "land_units": [{
                "name": "SAM", "class": "", "alliance_role": "enemy", "autodetect": True,
                "position": {"latitude": 1, "longitude": 1},
            }],
        }
        rendered = Generator(data, self.resolver).render()
        self.assertIn("NumberOfTriggers=1", rendered)
        self.assertIn("Name=Fleet Command autodetect", rendered)
        self.assertIn("Action_Units=Taskforce2LandUnit1", rendered)
        self.assertIn("Action_UnitRevealToTaskforce=Taskforce1|Identify", rendered)

    def test_parser_normalizes_autodetect_to_boolean(self) -> None:
        data = parse(default_scenario_dir() / " Single06.scs")
        values = [item["autodetect"] for item in data["land_units"] if "autodetect" in item]
        self.assertIn(True, values)
        self.assertIn(False, values)

    def test_nearby_buildings_and_sam_sites_are_merged(self) -> None:
        data = {
            "metadata": {"latlong": [0, 0], "month": 1, "timeofday": 12},
            "land_units": [
                {"name": "Building4", "position": {"latitude": 1, "longitude": 1}},
                {"name": "Factory", "position": {"latitude": 1.0005, "longitude": 1.0005}},
                {"name": "Building5", "position": {"latitude": 2, "longitude": 2}},
                {"name": "SAM", "position": {"latitude": 3, "longitude": 3}},
                {"name": "SAM", "position": {"latitude": 3.0005, "longitude": 3.0005}},
            ],
        }
        rendered = Generator(data, self.resolver).render()
        self.assertIn("NumberOfTaskforce1LandUnits=3", rendered)
        self.assertEqual(rendered.count("# FleetCommandMergedLandUnits="), 2)

    def test_pirate_conversion_keeps_embarked_aircraft_internal(self) -> None:
        data = parse(default_scenario_dir() / " Single04.scs")
        rendered = Generator(data, self.resolver).render()
        self.assertIn("NumberOfTaskforce1Helicopters=0", rendered)
        self.assertNotIn("[Taskforce1Helicopter", rendered)
        self.assertIn("GeoPosition=-1.42040270,123.39540434,low", rendered)
        self.assertNotIn("Taskforce2_Formation1=", rendered)
        self.assertGreaterEqual(rendered.count("Waypoints="), 8)
        self.assertIn("Condition_Condition1_PositionNM=183.6635,0.0000,58.5288", rendered)
        self.assertIn("SpawnChance=0.500", rendered)
        self.assertIn("RelativePositionInNM=200.2212,2500.0000,49.4711\nHeading=0.00\nTelegraph=2", rendered)
        self.assertIn("[Trigger10]\nName=End mission.\nAction_ObjectivesCompleted=End_mission\nAction_EndMission=True\nAction_Victory=Taskforce2\nCondition_Condition1_Type=Time\nCondition_Condition1_Time=54000", rendered)

    def test_pirate_probability_group_is_runtime_random(self) -> None:
        data = parse(default_scenario_dir() / " Single04.scs")
        rendered = Generator(data, self.resolver).render()
        self.assertIn("NumberOfTaskforce1Vessels=1", rendered)
        self.assertIn("[Taskforce1Vessel1]", rendered)
        self.assertIn("Type=usn_ffg_oliver_hazard_perry", rendered)
        self.assertIn("# FleetCommandRandomGroup=5; selected=", rendered)
        self.assertIn("SpawnChance=1.000000", rendered)
        self.assertNotIn("Type=usn_cg_ticonderoga", rendered)
        self.assertNotIn("Type=usn_ffg_oliver_hazard_perry,usn_cg_ticonderoga", rendered)
        self.assertNotIn("Taskforce1_Formation1=", rendered)

    def test_single01_preserves_probabilities_decks_and_formation(self) -> None:
        data = parse(default_scenario_dir() / " Single01.scs")
        rendered = Generator(data, self.resolver).render()
        self.assertEqual(rendered.count("[NeutralAircraft"), 6)
        self.assertEqual(rendered.count("SpawnChance=0.200"), 5)
        self.assertIn("SpawnChance=0.250", rendered)
        self.assertIn("usn_f-14a=Default,11", rendered)
        self.assertIn("FlightDeck_ReadyUpTasks=2", rendered)
        self.assertIn("FlightDeck_ReadyUpTask1=usn_f-14a,Default,AirToAirLongRange,2,0", rendered)
        self.assertIn("FlightDeck_ReadyUpTask2=usn_e-2c,Default,AEW,1,900", rendered)
        self.assertIn("usn_fa-18a=Default,35", rendered)
        self.assertIn("usn_ea-6b=Default,4", rendered)
        self.assertIn("usn_e-2c=Default,2", rendered)
        self.assertIn("Taskforce1_Formation1=Taskforce1Vessel1,Taskforce1Vessel2,Taskforce1Vessel3|Group CVN 76", rendered)
        self.assertIn("Condition_Condition1_Type=HasNoUnitsOfType", rendered)
        self.assertIn("Condition_Condition1_Taskforce=Taskforce2", rendered)
        self.assertIn("Condition_Condition1_UnitType=Aircraft", rendered)
        self.assertIn("Taskforce1IntelMessage=Intel|New intelligence received|Acknowledge", rendered)
        self.assertIn("Taskforce1ScenarioIntel=Expect attack by TU-22M Backfire", rendered)
        self.assertIn("Action_Taskforce1_Intel=Taskforce1ScenarioIntel", rendered)
        self.assertIn("Special_Force_Group_A=0,-100,Complete,Main", Generator(parse(default_scenario_dir() / " Single02.scs"), self.resolver).render())

    def test_objectives_have_display_names(self) -> None:
        rendered = Generator(parse(default_scenario_dir() / " Single02.scs"), self.resolver).render()
        self.assertIn("Objective_Special_Force_Group_A=Special Force Group A", rendered)
        self.assertIn("Objective_Special_Forces_Stopped=Special Forces Stopped", rendered)
        self.assertIn("Objective_Mission_Over=Mission Over", rendered)

    def test_single17_maps_kuznetsov_to_soviet_carrier(self) -> None:
        rendered = Generator(parse(default_scenario_dir() / " Single17.scs"), self.resolver).render()
        self.assertIn("Type=wp_cv_orel", rendered)
        self.assertNotIn("Type=usn_cv_kitty_hawk", rendered)

    def test_single02_patrol_group_selects_one_enemy_boat(self) -> None:
        rendered = Generator(parse(default_scenario_dir() / " Single02.scs"), self.resolver).render()
        self.assertIn("NumberOfTaskforce2Vessels=1", rendered)
        self.assertIn("SpawnChance=1.000000", rendered)
        self.assertNotIn("Condition_Condition1_Type=UnitsInTheArea", rendered)
        self.assertIn("[Trigger11]\nName=Special Forces Stopped\nAction_ObjectivesCompleted=Special_Forces_Stopped\nAction_EndMission=True\nAction_Victory=Taskforce1\nCondition_Condition1_Type=UnitDestroyed", rendered)
        self.assertIn("[Trigger12]\nName=Mission Over\nAction_ObjectivesCompleted=Mission_Over\nAction_EndMission=True\nAction_Victory=Taskforce1\nCondition_Condition1_Type=Time\nCondition_Condition1_Time=18000", rendered)

    def test_region_scc_files_are_parseable(self) -> None:
        for source in sorted((default_scenario_dir()).glob("*.scc")):
            with self.subTest(source=source.name):
                rendered = Generator(parse(source), self.resolver).render()
                parser = configparser.ConfigParser(interpolation=None, strict=True)
                parser.read_string(rendered)
                self.assertIn("Mission", parser)

    def test_region1_keeps_explicit_allied_submarines_and_maps_invincible(self) -> None:
        rendered = Generator(parse(default_scenario_dir() / " Region1.scc"), self.resolver).render()
        self.assertIn("NumberOfTaskforce1Submarines=2", rendered)
        self.assertEqual(rendered.count("Type=usn_cv_kitty_hawk"), 0)
        self.assertEqual(rendered.count("Type=wp_takr_kiev"), 3)

    def test_soviet_installations_use_soviet_definitions(self) -> None:
        data = parse(default_scenario_dir() / " Single31.scs")
        rendered = Generator(data, self.resolver).render()
        self.assertGreater(rendered.count("Type=wp_airbase_1"), 0)
        self.assertGreater(rendered.count("Type=wp_sam_site_sa-2"), 0)
        self.assertNotIn("Type=nato_small_airbase", rendered)
        self.assertNotIn("Type=nato_sam_skyguard", rendered)


if __name__ == "__main__":
    unittest.main()


class ResolverMappingTests(unittest.TestCase):
    def test_country_aware_rules_are_loaded_from_json(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic"):
                (root / category).mkdir()
            for definition in ("wp_airbase_1", "nato_small_airbase", "wp_sam_site_sa-2", "nato_sam_skyguard"):
                (root / "land_units" / f"{definition}.ini").touch()
            resolver = Resolver(root, MAPPING, lambda _: None)
            self.assertEqual(resolver.resolve({"name": "Airport", "country": "Russia"}, "land_units"), "wp_airbase_1")
            self.assertEqual(resolver.resolve({"name": "Airport", "country": "United States"}, "land_units"), "nato_small_airbase")
            self.assertEqual(resolver.resolve({"name": "SAM", "country": "USSR"}, "land_units"), "wp_sam_site_sa-2")


if __name__ == "__main__":
    unittest.main()
