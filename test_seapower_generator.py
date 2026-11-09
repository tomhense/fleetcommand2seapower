#!/usr/bin/env python3
"""Regression tests for Fleet Command JSON to Sea Power mission conversion."""

from __future__ import annotations

import configparser
import json
import re
import unittest
from unittest.mock import patch
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
if not ASSETS.is_dir():
    ASSETS = ROOT / "SeaPower_StreamingAssets" / "original"
SCENARIOS = default_scenario_dir()
if not SCENARIOS.is_dir():
    SCENARIOS = ROOT / "FleetCommand_scenarios"
MAPPING = ROOT / "fleetcommand2seapower" / "seapower_mapping.json"


@unittest.skipUnless(SCENARIOS.is_dir() and ASSETS.is_dir(), "Fleet Command and Sea Power Steam fixtures are not installed")
class SeaPowerGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.resolver = Resolver(ASSETS, MAPPING, lambda _: None)
        cls.sources = sorted(path for path in SCENARIOS.iterdir()
                             if path.suffix.lower() in {".scs", ".sct", ".scm", ".scc"})

    def test_generator_accepts_converter_json_string_intermediate(self) -> None:
        source = SCENARIOS / " Single06.scs"
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
        data = parse(SCENARIOS / " Single04.scs")
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
        data = parse(SCENARIOS / " Single06.scs")
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
        data = parse(SCENARIOS / " Single04.scs")
        rendered = Generator(data, self.resolver).render()
        self.assertIn("NumberOfTaskforce1Helicopters=0", rendered)
        self.assertNotIn("[Taskforce1Helicopter", rendered)
        self.assertIn("GeoPosition=-1.42040270,123.39540434,low", rendered)
        self.assertNotIn("Taskforce2_Formation1=", rendered)
        self.assertGreaterEqual(rendered.count("Waypoints="), 8)
        self.assertIn("Name=Pirates Neutralized\nAction_ObjectivesCompleted=Pirates_Neutralized\nAction_EndMission=True\nAction_Victory=Taskforce1\nCondition_Condition1_Type=TriggerCompleted", rendered)
        self.assertIn("SpawnChance=0.500", rendered)
        self.assertIn("RelativePositionInNM=200.2212,2500.0000,49.4711\nHeading=0.00\nTelegraph=2", rendered)
        self.assertIn("[Trigger10]\nName=End mission.\nAction_ObjectivesCompleted=End_mission\nAction_EndMission=True\nAction_Victory=Taskforce2\nCondition_Condition1_Type=Time\nCondition_Condition1_Time=54000", rendered)

    def test_pirate_probability_group_is_runtime_random(self) -> None:
        data = parse(SCENARIOS / " Single04.scs")
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
        data = parse(SCENARIOS / " Single01.scs")
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
        self.assertIn("Condition_Condition1_Type=TriggerCompleted", rendered)
        self.assertIn("Condition_Condition2_Taskforce=Taskforce2", rendered)
        self.assertIn("Taskforce1IntelMessage=Intel|New intelligence received|Acknowledge", rendered)
        self.assertIn("Taskforce1ScenarioIntel=Expect attack by TU-22M Backfire", rendered)
        self.assertIn("Action_Taskforce1_Intel=Taskforce1ScenarioIntel", rendered)
        self.assertIn("Special_Force_Group_A=0,-100,Complete,Main", Generator(parse(SCENARIOS / " Single02.scs"), self.resolver).render())

    def test_objectives_have_display_names(self) -> None:
        rendered = Generator(parse(SCENARIOS / " Single02.scs"), self.resolver).render()
        self.assertIn("Objective_Special_Force_Group_A=Special Force Group A", rendered)
        self.assertIn("Objective_Special_Forces_Stopped=Special Forces Stopped", rendered)
        self.assertIn("Objective_Mission_Over=Mission Over", rendered)

    def test_single17_maps_kuznetsov_to_soviet_carrier(self) -> None:
        rendered = Generator(parse(SCENARIOS / " Single17.scs"), self.resolver).render()
        self.assertIn("Type=wp_cv_orel", rendered)
        self.assertNotIn("Type=usn_cv_kitty_hawk", rendered)

    def test_single02_patrol_group_selects_one_enemy_boat(self) -> None:
        rendered = Generator(parse(SCENARIOS / " Single02.scs"), self.resolver).render()
        self.assertIn("NumberOfTaskforce2Vessels=1", rendered)
        self.assertIn("SpawnChance=1.000000", rendered)
        self.assertNotIn("Condition_Condition1_Type=UnitsInTheArea", rendered)
        self.assertIn("[Trigger11]\nName=Special Forces Stopped\nAction_ObjectivesCompleted=Special_Forces_Stopped\nAction_EndMission=True\nAction_Victory=Taskforce1\nCondition_Condition1_Type=TriggerCompleted", rendered)
        self.assertIn("[Trigger12]\nName=Mission Over\nAction_ObjectivesCompleted=Mission_Over\nAction_EndMission=True\nAction_Victory=Taskforce1\nCondition_Condition1_Type=Time\nCondition_Condition1_Time=18000", rendered)

    def test_region_scc_files_are_parseable(self) -> None:
        for source in sorted(SCENARIOS.glob("*.scc")):
            with self.subTest(source=source.name):
                rendered = Generator(parse(source), self.resolver).render()
                parser = configparser.ConfigParser(interpolation=None, strict=True)
                parser.read_string(rendered)
                self.assertIn("Mission", parser)

    def test_region1_airport_buildings_are_consolidated(self) -> None:
        data = parse(SCENARIOS / " Region1.scc")
        generator = Generator(data, self.resolver)
        clusters = generator.land_clusters()
        airport = next(cluster for cluster in clusters if cluster[0].get("uniquename") == "Airport 2")
        self.assertEqual(len(airport), 29)
        self.assertEqual(sum(1 for cluster in clusters if generator.taskforce_for(cluster[0]) == "Taskforce2"), 4)
        self.assertTrue(all(member["name"] != "SAM" for member in airport))
        rendered = generator.render()
        parsed = configparser.ConfigParser(interpolation=None)
        parsed.read_string(rendered)
        self.assertEqual(parsed["Taskforce2LandUnit1"]["GeoPosition"], "8.58965010,77.85303426,low")
        target = next(parsed[section] for section in parsed.sections() if parsed[section].get("Name") == "Airport Destroyed")
        self.assertEqual(target["Condition_Condition1_Units"], "Taskforce2LandUnit1")

    def test_region1_keeps_explicit_allied_submarines_and_maps_invincible(self) -> None:
        rendered = Generator(parse(SCENARIOS / " Region1.scc"), self.resolver).render()
        self.assertIn("NumberOfTaskforce1Submarines=2", rendered)
        self.assertEqual(rendered.count("Type=usn_cv_kitty_hawk"), 0)
        self.assertEqual(rendered.count("Type=wp_takr_kiev"), 1)
        self.assertIn("Type=wp_ss_foxtrot\nSpawnChance=0.083\nVariantReference=Variant58", rendered)
        self.assertIn("Type=wp_ss_kilo\nSpawnChance=0.083\nVariantReference=Variant17", rendered)
        self.assertEqual(rendered.count("Type=pla_h-6a"), 2)
        self.assertEqual(rendered.count("Type=wp_bdk_ropucha"), 2)
        self.assertIn("Type=wp_vt_boris_chilikin", rendered)
        self.assertNotIn("Type=pns_ss_hangor", rendered)
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(rendered)
        assault = config["Trigger9"]
        self.assertEqual(assault["ConditionsCompleted"], "<Condition1> AND <Condition2> AND <Condition3> AND <Condition4>")
        self.assertEqual([assault[f"Condition_Condition{i}_Triggers"] for i in range(1, 5)],
                         ["Trigger5", "Trigger6", "Trigger7", "Trigger8"])
        self.assertNotIn("Action_EndMission", assault)
        final = config["Trigger1"]
        self.assertEqual(final["Condition_Condition2_Triggers"], "Trigger9")
        active_carriers = [f"Trigger{i}" for i in (11, 12, 13) if not config[f"Trigger{i}"].getboolean("Disabled", fallback=False)]
        self.assertEqual(len(active_carriers), 1)
        self.assertEqual(final["Condition_Condition3_Triggers"], active_carriers[0])
        carrier = next(config[s] for s in config.sections() if config[s].get("Type") == "wp_takr_kiev")
        self.assertEqual(carrier["SpawnChance"], "1.000000")
        self.assertEqual(carrier["wp_yak-38"], "Default,12")
        self.assertNotIn("usmc_av-8a", carrier)
        self.assertIn("wp_yak-38", carrier["FlightDeck_ReadyUpTask1"])
        self.assertNotIn("Action_DespawnUnits=True", rendered)
        self.assertEqual(carrier["Nation"], "India")
        self.assertTrue(all(config[s].get("Nation") == "India" for s in config.sections()
                            if config[s].get("Type") == "wp_bdk_ropucha"))
        self.assertEqual(config["Taskforce2LandUnit1"]["Nation"], "India")
        self.assertEqual(config["Taskforce2Aircraft2"]["Nation"], "China")
        self.assertEqual(config["Taskforce2Aircraft3"]["Nation"], "Soviet")
        self.assertEqual(config["Taskforce2Aircraft4"]["Nation"], "India")

    def test_soviet_installations_use_soviet_definitions(self) -> None:
        data = parse(SCENARIOS / " Single31.scs")
        rendered = Generator(data, self.resolver).render()
        self.assertGreater(rendered.count("Type=wp_airbase_1"), 0)
        self.assertGreater(rendered.count("Type=wp_sam_site_sa-2"), 0)
        self.assertNotIn("Type=nato_small_airbase", rendered)
        self.assertNotIn("Type=nato_sam_skyguard", rendered)


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


class LandStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for category in ("land_units", "vessels", "aircraft", "biologic"):
            (root / category).mkdir()
        definitions = {
            "nato_small_airbase": "[coll_runway]\nPosition=0,0,0\nRotation=0,0,0\nScale=2,1,12\n",
            "tgt_industry_buildings_1": "[coll_selection]\nPosition=0,0,0\nScale=1,1,1\n",
            "wp_sam_site_sa-2": "[coll_selection]\nPosition=0,0,0\nScale=1,1,1\n",
            "nato_sam_skyguard": "[coll_selection]\nPosition=0,0,0\nScale=1,1,1\n",
        }
        for definition, contents in definitions.items():
            (root / "land_units" / f"{definition}.ini").write_text(contents)
        self.resolver = Resolver(root, MAPPING, lambda _: None)

    def test_airbase_absorbs_nearby_buildings_and_keeps_its_position(self) -> None:
        base = {"name": "Airport", "uniquename": "Base", "alliance_role": "enemy",
                "position": {"latitude": 0, "longitude": 0}}
        building = {"name": "Building4", "uniquename": "Hangar", "alliance_role": "enemy",
                    "position": {"latitude": 0.001, "longitude": 0}}
        more_buildings = [{"name": "Building4", "uniquename": f"Building {i}", "alliance_role": "enemy",
                           "position": {"latitude": lat, "longitude": 0}}
                          for i, lat in enumerate((0.004, -0.005), 1)]
        sam = {"name": "SAM", "alliance_role": "enemy", "position": {"latitude": 0.001, "longitude": 0}}
        other_side = {"name": "Building5", "position": {"latitude": 0.001, "longitude": 0}}
        generator = Generator({"land_units": [building, base, *more_buildings, sam, other_side]}, self.resolver)
        clusters = generator.land_clusters()
        self.assertEqual([[item.get("uniquename", item["name"]) for item in cluster] for cluster in clusters],
                         [["Base", "Hangar", "Building 1", "Building 2"], ["SAM"], ["Building5"]])
        representatives = generator.land_cluster_items(clusters)
        self.assertEqual(representatives[0][1]["position"], base["position"])
        rendered = generator.render()
        self.assertIn("# FleetCommandMergedLandUnits=Base,Hangar,Building 1,Building 2", rendered)
        self.assertEqual(building["position"], {"latitude": 0.001, "longitude": 0})

    def test_dense_buildings_merge_without_transitive_chain(self) -> None:
        items = [{"name": "Building4", "position": {"latitude": lat, "longitude": 0}}
                 for lat in (0, 0.003, 0.006)]
        clusters = Generator({"land_units": items}, self.resolver).land_clusters()
        self.assertEqual([len(cluster) for cluster in clusters], [2, 1])

    def test_rectangular_airbase_does_not_force_building_into_a_ring(self) -> None:
        base = {"name": "Airport", "position": {"latitude": 0, "longitude": 0}}
        building = {"name": "Building4", "position": {"latitude": 0, "longitude": 0.004}}
        generator = Generator({"land_units": [building, base]}, self.resolver)
        arranged = generator.land_cluster_items(generator.land_clusters())
        representative = next(item for _, item in arranged if item["name"] == "Building4")
        self.assertEqual(representative["position"], building["position"])
        self.assertEqual(base["position"], {"latitude": 0, "longitude": 0})

    def test_overlapping_rotated_footprints_are_separated(self) -> None:
        base = {"name": "Airport", "course": 45, "position": {"latitude": 0, "longitude": 0}}
        building = {"name": "Building4", "position": {"latitude": 0.0015, "longitude": 0}}
        generator = Generator({"land_units": [building, base]}, self.resolver)
        arranged = generator.land_cluster_items(generator.land_clusters())
        positions = {item["name"]: item for _, item in arranged}
        boxes = []
        for item in positions.values():
            east, _, north = generator.local(item["position"])
            boxes.append((east, north, generator.collision_boxes_nm(item)[0]))
        self.assertFalse(generator.land_boxes_overlap(boxes[0], boxes[1], 2 / 1852))
        self.assertEqual(positions["Airport"]["position"], base["position"])
        self.assertNotEqual(positions["Building4"]["position"], building["position"])

    def test_targeted_buildings_remain_individual(self) -> None:
        base = {"name": "Airport", "uniquename": "Base", "position": {"latitude": 0, "longitude": 0}}
        buildings = [{"name": "Building4", "uniquename": f"Building {i}",
                      "position": {"latitude": 0.003, "longitude": i * 0.0001}} for i in range(4)]
        generator = Generator({"land_units": [base, *buildings],
                               "triggers_goals": [{"uniquename": "Building 0"}]}, self.resolver)
        clusters = generator.land_clusters()
        self.assertEqual([len(cluster) for cluster in clusters], [4, 1])
        self.assertEqual(clusters[1][0]["uniquename"], "Building 0")

    def test_model_scale_and_collider_rotation(self) -> None:
        airport = self.resolver.root / "land_units" / "nato_small_airbase.ini"
        airport.write_text("[coll_runway]\nPosition=1,0,0\nRotation=0,90,0\nScale=10,1,1\n")
        generator = Generator({}, self.resolver)
        box = generator.collision_boxes_nm({"name": "Airport", "position": {"latitude": 0, "longitude": 0}})[0]
        self.assertAlmostEqual(box[0] * 2, 1000 / 1852)
        self.assertAlmostEqual(box[2], 100 / 1852)
        self.assertAlmostEqual(box[4], 3.141592653589793 / 2)

    def test_country_specific_sam_never_falls_back_to_opposing_system(self) -> None:
        self.assertEqual(self.resolver.resolve({"name": "SAM", "country": "Poland"}, "land_units"), "wp_sam_site_sa-2")
        self.assertEqual(self.resolver.resolve({"name": "SAM", "alliance_country": "East Germany"}, "land_units"), "wp_sam_site_sa-2")
        self.assertEqual(self.resolver.resolve({"name": "SAM", "alliance_countries": ["Poland", "USSR"]}, "land_units"), "wp_sam_site_sa-2")
        self.assertEqual(self.resolver.resolve({"name": "SAM", "country": "United States"}, "land_units"), "nato_sam_skyguard")
        self.resolver.files["land_units"].remove("wp_sam_site_sa-2")
        self.assertIsNone(self.resolver.resolve({"name": "SAM", "country": "Poland"}, "land_units"))


class HelicopterTaskTests(unittest.TestCase):
    def test_embarked_helicopter_task_and_evacuation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic"):
                (root / category).mkdir()
            (root / "aircraft" / "usn_sh-2f.ini").touch()
            (root / "vessels" / "usn_cv_nimitz.ini").write_text("[FlightDeck]\n")
            resolver = Resolver(root, MAPPING, lambda _: None)
            data = {
                "metadata": {"latlong": [5.6, 75.5], "timeofday": 2},
                "vessels": [{"name": "CVN 71", "class": "Nimitz CVN", "alliance_role": "friendly",
                             "position": {"latitude": 5, "longitude": 76},
                             "aircraft": [{"name": "SH-60F Sea Hawk", "variant": "SH-60F Sea Hawk",
                                           "class": "Helicopter", "quantity": 4}]}],
                "triggers_goals": [
                    {"goalname": "Helo Task", "goaltype": 3, "messagetypeid": 1, "starttime": 12600,
                     "position": {"latitude": 0.83, "longitude": 78.47}, "message": "Send a helicopter."},
                    {"goalname": "Evacuation", "goaltype": 0, "class": "Helicopter",
                     "shipname": "SH-60F Seahawk", "tagname": "SH-60F Sea Hawk",
                     "position": {"latitude": 6.13871, "longitude": 80.301313},
                     "message": "Personnel recovered. The helo has landed on the aircraft carrier."},
                ],
            }
            rendered = Generator(data, resolver).render()
            parsed = configparser.ConfigParser(interpolation=None)
            parsed.read_string(rendered)
            task, evacuation = parsed["Trigger1"], parsed["Trigger2"]
            self.assertEqual(task["Condition_Condition1_Type"], "Time")
            self.assertEqual(task["Condition_Condition1_Time"], "5400")
            self.assertEqual(task["Action_Taskforce1_Message"], "Taskforce1Goal1Message")
            self.assertNotIn("Action_EndMission", task)
            self.assertNotIn("Action_ObjectivesCompleted", task)
            self.assertIn("Send a helicopter.", parsed["Language_en"]["Taskforce1Goal1Message"])
            self.assertEqual(evacuation["Condition_Condition1_UnitType"], "Helicopter")
            self.assertEqual(evacuation["Condition_Condition1_UnitTypes"], "usn_sh-2f")
            self.assertEqual(evacuation["Action_Taskforce1_Message"], "Taskforce1Goal2Message")
            self.assertEqual(evacuation["Action_ObjectivesCompleted"], "Evacuation")
            self.assertIn("reached the objective area", parsed["Language_en"]["Taskforce1Goal2Message"])
            self.assertNotIn("landed", parsed["Language_en"]["Taskforce1Goal2Message"])
            self.assertEqual(parsed["Mission"]["NumberOfTaskforce1Helicopters"], "0")
            self.assertNotIn("[Taskforce1Helicopter", rendered)
            self.assertIn("usn_sh-2f=Default,4", rendered)
            self.assertEqual(Generator(data, resolver).goal_message({
                "goaltype": 0, "class": "ship", "position": {"latitude": 1, "longitude": 2},
                "message": "A different scenario-specific message."}), "Units have reached the objective area.")


class ParentGoalTests(unittest.TestCase):
    def test_parent_waits_for_children_and_allows_unspawned_targets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic"):
                (root / category).mkdir()
            (root / "vessels" / "wp_ptg_osa2.ini").touch()
            resolver = Resolver(root, MAPPING, lambda _: None)
            data = {
                "metadata": {"latlong": [10, 20]},
                "groups": [{"group_id": 3, "probability": 40}],
                "vessels": [
                    {"name": "Osa II PCFG", "uniquename": "required", "alliance_role": "enemy"},
                    {"name": "Osa II PCFG", "uniquename": "optional", "alliance_role": "enemy", "group": 3},
                ],
                "triggers_goals": [
                    {"goalid": 1, "parentgoalid": -1, "goaltype": 3, "goalname": "Campaign"},
                    {"goalid": 2, "parentgoalid": 1, "goaltype": 3, "goalname": "Group destroyed"},
                    {"goalid": 3, "parentgoalid": 2, "goaltype": 2, "uniquename": "required"},
                    {"goalid": 4, "parentgoalid": 2, "goaltype": 2, "uniquename": "optional"},
                ],
            }
            parsed = configparser.ConfigParser(interpolation=None)
            parsed.read_string(Generator(data, resolver).render())
            group = parsed["Trigger2"]
            self.assertEqual(group["Condition_Condition1_Triggers"], "Trigger3")
            self.assertEqual(group["Condition_Condition2_Triggers"], "Trigger4")
            self.assertEqual(group["Condition_Condition3_Type"], "UnitsInTheArea")
            self.assertEqual(group["Condition_Condition3_Units"], "Taskforce2Vessel2")
            self.assertEqual(group["Condition_Condition3_Taskforce"], "Taskforce2")
            self.assertEqual(group["ConditionsCompleted"], "<Condition1> AND (<Condition2> OR NOT <Condition3>)")
            self.assertNotIn("Action_EndMission", group)
            self.assertEqual(parsed["Trigger1"]["Condition_Condition1_Triggers"], "Trigger2")
            self.assertEqual(parsed["Taskforce2Vessel2"]["SpawnChance"], "0.400")


class AlternativeGroupsTests(unittest.TestCase):
    def test_one_sibling_group_is_emitted_and_its_goal_is_active(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic"):
                (root / category).mkdir()
            (root / "vessels" / "wp_ptg_osa2.ini").touch()
            resolver = Resolver(root, MAPPING, lambda _: None)
            data = {
                "groups": [{"group_id": 1, "probability": 100}, *[
                    {"group_id": number, "parent_group_id": 1, "probability": 33}
                    for number in (2, 3, 4)]],
                "vessels": [
                    {"name": "Osa II PCFG", "uniquename": f"leader {number}", "group": number,
                     "prob": 100, "alliance_role": "enemy"} for number in (2, 3, 4)],
                "triggers_goals": [
                    {"goalid": 1, "parentgoalid": -1, "goaltype": 3, "goalname": "Campaign"},
                    *[{"goalid": number, "parentgoalid": 1, "goaltype": 2,
                       "group": number, "uniquename": f"leader {number}"} for number in (2, 3, 4)],
                ],
            }
            for choice in range(3):
                with self.subTest(choice=choice), patch("fleetcommand2seapower.generator_parts.render.random.choices",
                                                       side_effect=lambda family, **_: [family[choice]]):
                    rendered = Generator(data, resolver).render()
                parsed = configparser.ConfigParser(interpolation=None)
                parsed.read_string(rendered)
                self.assertEqual(parsed["Mission"]["NumberOfTaskforce2Vessels"], "1")
                self.assertEqual(parsed["Taskforce2Vessel1"]["SpawnChance"], "1.000000")
                self.assertEqual(parsed["Trigger1"]["Condition_Condition1_Triggers"], f"Trigger{choice + 2}")
                self.assertFalse(parsed[f"Trigger{choice + 2}"].getboolean("Disabled", fallback=False))
                self.assertEqual(sum(parsed[f"Trigger{i}"].getboolean("Disabled", fallback=False) for i in (2, 3, 4)), 2)
                self.assertNotIn("Action_DespawnUnits=True", rendered)

    def test_carrier_aircraft_override_is_platform_specific(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic"):
                (root / category).mkdir()
            for definition in ("usmc_av-8a", "wp_yak-38"):
                (root / "aircraft" / f"{definition}.ini").touch()
            generator = Generator({}, Resolver(root, MAPPING, lambda _: None))
            ship = {"aircraft": [{"name": "FA-2 Sea Harrier", "quantity": 2,
                                  "alert_status": [{"alert": "Alert5", "value": 1}]}]}
            kiev: list[str] = []
            other: list[str] = []
            generator.add_flight_deck(kiev, ship, "wp_takr_kiev")
            generator.add_flight_deck(other, ship, "another_carrier")
            self.assertIn("wp_yak-38=Default,2", kiev)
            self.assertTrue(any(line.startswith("FlightDeck_ReadyUpTask1=wp_yak-38,") for line in kiev))
            self.assertIn("usmc_av-8a=Default,2", other)


class NationalUnitTests(unittest.TestCase):
    def test_air_group_selects_squadrons_by_nation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic"):
                (root / category).mkdir()
            (root / "aircraft" / "wp_tu-16.ini").touch()
            (root / "aircraft" / "wp_tu-16_squadrons.ini").write_text(
                "[Default]\nNation=Soviet\n[Squadron7]\nNation=China\n")
            resolver = Resolver(root, MAPPING, lambda _: None)
            generator = Generator({}, resolver)
            lines: list[str] = []
            generator.add_flight_deck(lines, {"country": "India", "aircraft": [
                {"name": "Badger Gen", "country": "China", "quantity": 2,
                 "alert_status": [{"alert": "Alert5", "value": 1}]},
                {"name": "Badger Gen", "country": "Russia", "quantity": 3},
            ]})
            self.assertIn("wp_tu-16=Squadron7,2|Default,3", lines)
            self.assertTrue(any(line.startswith("FlightDeck_ReadyUpTask1=wp_tu-16,Squadron7,") for line in lines))

    def test_land_nation_inference_requires_unambiguous_source(self) -> None:
        with TemporaryDirectory() as directory:
            resolver = Resolver(Path(directory), MAPPING, lambda _: None)
            self.assertEqual(resolver.nation({"country": "United States"}), "US")
            self.assertEqual(resolver.nation({"country": "Russia"}), "Soviet")
            self.assertIsNone(resolver.nation({"country": "NEUTRALS"}))
            self.assertEqual(resolver.nation({"aircraft": [{"country": "India"}, {"country": "India"},
                                                             {"country": "China"}]}, infer_land=True), "India")
            self.assertIsNone(resolver.nation({"aircraft": [{"country": "India"}, {"country": "China"}]}, infer_land=True))

    def test_indian_and_chinese_units_use_available_national_assets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic", "language_en"):
                (root / category).mkdir()
            for definition in ("wp_ss_foxtrot", "pns_ss_hangor", "wp_bdk_ropucha", "wp_vt_boris_chilikin"):
                (root / "vessels" / f"{definition}.ini").touch()
            for definition in ("pla_h-6a", "wp_tu-16"):
                (root / "aircraft" / f"{definition}.ini").touch()
            (root / "vessels" / "wp_ss_foxtrot_variants.ini").write_text(
                "[Default]\nNation=Soviet\n[Variant58]\nNation=India\n[Variant59]\nNation=India\n")
            (root / "language_en" / "vessel_names.ini").write_text(
                "[wp_ss_foxtrot]\nVariant58=S20 Kursura, Kursura\nVariant59=S21 Karanj, Karanj\n")
            (root / "aircraft" / "pla_h-6a_squadrons.ini").write_text("[Default]\nNation=China\n")
            resolver = Resolver(root, MAPPING, lambda _: None)
            foxtrot = {"name": "Foxtrot SS", "country": "India", "shipname": "Karanj SS21"}
            self.assertEqual(resolver.resolve(foxtrot, "vessels"), "wp_ss_foxtrot")
            self.assertEqual(resolver.national_reference(foxtrot, "wp_ss_foxtrot", "vessels"), "Variant59")
            self.assertEqual(resolver.resolve({"name": "Badger Gen", "country": "China"}, "aircraft"), "pla_h-6a")
            self.assertEqual(resolver.resolve({"name": "Magar LST", "country": "India"}, "vessels"), "wp_bdk_ropucha")
            self.assertEqual(resolver.resolve({"name": "Supply AOE", "country": "India"}, "vessels"), "wp_vt_boris_chilikin")
            self.assertEqual(resolver.national_reference({"country": "China"}, "pla_h-6a", "aircraft"), "Default")

            generator = Generator({"vessels": [{"name": "Magar LST", "country": "India",
                                                  "uniquename": "LST 20", "aircraft": [
                                                      {"name": "SH-60F Sea Hawk", "quantity": 1}]}]}, resolver)
            rendered = generator.render()
            self.assertIn("Type=wp_bdk_ropucha", rendered)
            self.assertNotIn("CustomAirGroup=True", rendered)
            self.assertTrue(any("no flight deck" in warning for warning in generator.warnings))

    def test_alliance_members_share_taskforce_and_other_alliances_remain_separate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for category in ("land_units", "vessels", "aircraft", "biologic"):
                (root / category).mkdir()
            resolver = Resolver(root, MAPPING, lambda _: None)
            data = {"alliances": [{"id": 1, "role": "enemy"}, {"id": 2, "role": "enemy"}]}
            generator = Generator(data, resolver)
            self.assertEqual(generator.taskforce_for({"alliance_role": "enemy", "alliance_id": 1, "country": "India"}), "Taskforce2")
            self.assertEqual(generator.taskforce_for({"alliance_role": "enemy", "alliance_id": 1, "country": "China"}), "Taskforce2")
            self.assertEqual(generator.taskforce_for({"alliance_role": "enemy", "alliance_id": 2, "country": "Russia"}), "Taskforce3")
            self.assertIn("Taskforce2,Taskforce3=Neutral", generator.render())


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
