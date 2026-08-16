# Fleet Command `.scs` scenario format

This document describes the text scenario format used by the Fleet Command
scenario files in this repository. It is an empirical reference assembled from
35 scenarios, not an official specification. Names and field meanings marked
“inferred” should be treated as conventions to preserve and validate against
the game rather than as guaranteed API contracts.

## 1. File model

An SCS file is an ASCII/Latin-1, line-oriented document. Most lines have the
form:

```text
KEY value value ...
```

Values may be unquoted numbers, unquoted words, or double-quoted strings. A
quoted string may contain spaces. Empty quoted strings (`""`) are valid and
represent an absent value. Files commonly use CRLF line endings.

The parser should use a quote-aware lexer. Splitting on whitespace is not
sufficient because names, messages, classes, countries and audio filenames can
contain spaces.

The semicolon line (`;`) is a separator. There is no general comment syntax in
the observed files.

The file has three broad regions:

1. Scenario header and mission text.
2. Alliances, groups, entities, air stations, corridors and goals.
3. `BITMAP`, followed by a palette and bitmap data.

The bitmap region can be skipped when only scenario semantics are required.
Stop parsing semantic records at the first `BITMAP` line.

## 2. Multiline text

Several keys are followed by a text block:

```text
DESCRIPTION
BEGINTEXT
Pirates attack a merchant vessel.
ENDTEXT
```

Everything between `BEGINTEXT` and `ENDTEXT` is message content. The game uses
literal `<R>` markers as line breaks; the converter removes those markers and
collapses consecutive whitespace to one space. Do not tokenize the content as
scenario fields.

Observed header text keys are:

- `INTELMESSAGE`
- `DESCRIPTION`
- `TASKINGMESSAGE`
- `MISSIONTITLE`

Goal text keys use the same block syntax:

- `SUCCESSMESSAGE`
- `FAILUREMESSAGE`
- a bare `BEGINTEXT` after the goal fields, which is the goal's display/message
  text

Some old files contain visually wrapped text where a word is split across two
physical lines. A parser should preserve physical lines rather than trying to
repair prose.

## 3. Header

The header occurs before the first alliance/group/entity record. Observed keys:

| Key | Values | Meaning |
|---|---|---|
| `VERSION` | integer | Scenario format version. |
| `OWNALLIANCE` | integer | Scenario-local alliance ID controlled by the player. This is not a country code. |
| `DIFFICULTY` | integer | Scenario difficulty setting. |
| `TOTALNUMSIDES` | integer | Number of sides/alliances represented. |
| `LATLONG` | latitude longitude | Scenario map reference latitude and longitude in decimal degrees. |
| `SEASTATE` | integer | Sea-state setting. |
| `MONTH` | integer | Scenario month. |
| `BOTTOMTYPE` | integer | Seabed/bottom setting. |
| `TIMEOFDAY` | integer | Start time-of-day setting. |
| `WEATHER` | integer | Weather setting. |
| `CLOUDHEIGHT` | number | Cloud height. |
| `SSP` | integer | Scenario setting; exact meaning is not established. |

A single-value header field should be represented as a scalar in JSON. `LATLONG`
is a two-value coordinate pair and should remain a pair or become an object with
`latitude` and `longitude`.

## 4. Alliances and groups

### Alliance membership

The three alliance declarations have this form:

```text
AlliedAlliance 1 0
ThreatAlliance 7 1 2 3 4 5 6 7
NeutralAlliance 1 8
```

The first number is the number of alliance IDs that follow. The remaining
numbers are alliance IDs. A general representation is:

```json
{
  "type": "ThreatAlliance",
  "member_count": 7,
  "alliance_ids": [1, 2, 3, 4, 5, 6, 7]
}
```

### Group declarations

Top-level group definitions have this form:

```text
GROUP "U.S. SAG" 4 50 5 6 2
```

The observed positional layout is:

```text
GROUP name group_id probability alliance_id formation_id parent_group_id
```

The field names are inferred from repeated scenario relationships. In
particular, `parent_group_id` is `-1` for a root group and another group ID for
a subordinate/optional group. `probability` is normally a percentage such as
`50` or `100`.

An entity's `GROUP n` line is a reference to the top-level group ID, not a new
group declaration.

## 5. Entities

The principal object record is:

```text
ENTITY "model name" ENTITYTYPE
... fields ...
END
```

`NONENTITY` has the same body but marks a non-interactive/static object:

```text
NONENTITY "PowerPlant" BUILDINGENTITY
... fields ...
END
```

The record name is the database/model name. `UNIQUENAME` is the scenario
instance name and is the preferred stable reference when present.

Observed entity types:

- `AIRENTITY` — aircraft.
- `SURFENTITY` — surface ships and civilian vessels.
- `SUBENTITY` — submarines.
- `BUILDINGENTITY` — buildings, airports and land installations.
- `MINEENTITY` — mines.

The converter categorizes `BUILDINGENTITY` and nonentities as `land_units`,
`AIRENTITY` as `aircraft`, and all surface entities as `vessels`. Military
ships and civilian craft are therefore in the same output collection because
“ship” versus “vessel” is not a distinction encoded by the source format.

### Common entity fields

| Field | Typical values | Meaning |
|---|---|---|
| `ALLIANCE` | integer | Alliance ID. |
| `POS` | x y altitude | Initial position in the game's map grid. |
| `POINTS` | number | Damage/hit-point value. |
| `COURSE` | degrees | Initial heading. |
| `SPEED` | number | Initial speed in game units. |
| `PROB` | number | Appearance/probability setting. Some files use values above 100. |
| `COUNTRY` | string | Country/operator name. This is the authoritative country label when present. |
| `CLASS` | string | Database/platform class. |
| `SHIPNAME` | string | Displayed platform name. Empty for many buildings. |
| `UNIQUENAME` | string | Scenario-unique object name. |
| `GROUP` | integer | Top-level group ID. |
| `TACTIC` | word | Movement/tactical behavior, e.g. `TRANSIT`, `RANDOMBOX`. |
| `AUTODETECT` | 0/1 | Building/installation automatic detection flag. |
| `FORMATIONLEADER` | string | Formation leader reference. |
| `COMMSINTERVAL` | number | Communications interval. |
| `SOURCEOBJECT` | string plus optional mode | Object that launched/owns the aircraft. |
| `LAUNCHTIME` | integer | Launch time, usually seconds from scenario start. |
| `LOADOUTTYPE` | word | Loadout selection/type. |

Empty values such as `COUNTRY ""` should become JSON `null`, not `""`.

### Alliance IDs and countries

Values in `ALLIANCE`, `OWNALLIANCE`, `GROUP` alliance fields and alliance
declarations are IDs local to one scenario. They must not be interpreted as a
global country enumeration: alliance `0` is commonly the player side, `1` is
often an opposing side, and `8` is frequently neutral, but these conventions
are not guaranteed and other scenarios use IDs such as `2`, `3` or `5`.

The country of an entity is given separately by its `COUNTRY` field. A side can
contain several countries (for example a coalition), so an alliance ID cannot
always be reduced to one country. The converter therefore keeps the original
numeric `alliance` value and adds an `alliances` table plus resolved entity
attributes:

```json
{
  "id": 1,
  "role": "enemy",
  "countries": ["Russia"],
  "country": "Russia"
}
```

`country` is populated only when exactly one country was observed. For a
coalition it is `null` and `countries` contains all observed members. Entity
records receive `alliance_id`, `alliance_role`, `alliance_country` and
`alliance_countries`. The role is inferred from `OWNALLIANCE` and the
`AlliedAlliance`, `ThreatAlliance` and `NeutralAlliance` declarations; an
undeclared ID remains `unknown`.

### Positions and coordinate conversion

`POS`, `GOAL`, `WAYPOINT` and corridor positions use the same three-number
shape:

```text
x y z
```

`z` is altitude/depth depending on the object. `WAYPOINT` adds a speed:

```text
WAYPOINT x y z speed
```

`BOX` and `RANDOMSTARTBOX` contain four grid values describing a rectangular
movement area:

```text
BOX x1 y1 x2 y2
RANDOMSTARTBOX x1 y1 x2 y2
```

The SCS files do not name the grid projection, but the supplied corpus gives a
useful empirical convention: corridor coordinates repeatedly use `0` and
`1111200`, indicating a 10-degree square grid at `111120` map units per
degree. The grid center is therefore `(555600, 555600)`. `x` increases east and
`y` increases north. `LATLONG` is treated as the geographic location of that
grid center. The current converter applies a configurable local
equirectangular conversion, including the longitude correction for latitude,
and emits geographic latitude/longitude without embedding duplicate raw
coordinates in the JSON. These values should remain configurable for custom
maps.

### Waypoints and movement boxes

An object may have zero or more waypoints. Preserve their order. A `BOX` is
associated with the preceding object and is normally used with
`TACTIC RANDOMBOX`. `RANDOMSTARTBOX` is an initial-placement rectangle.

## 6. Aircraft attachments

`AIRCRAFT` is overloaded. It appears both as an aircraft entity's attached
aircraft inventory and inside air stations/corridors.

The common form is:

```text
AIRCRAFT "database name" "variant/display name" "class" "country" quantity ...
```

For example:

```text
AIRCRAFT "SH-60R Sea Hawk" "SH-60R Seahawk" "Helicopter" "United States" 2 Alert5 0 : Alert15 1 : 0 Alert30 1 : 1
```

The first five semantic fields are:

1. database/model name;
2. variant or display name;
3. aircraft class;
4. country/operator;
5. quantity.

The remainder is a legacy colon-separated alert schedule. Named entries have
the shape `Alert5 0`, `Alert15 1`, or `Alert30 1`, where the number in the
label is the alert stage/time and the following number is its value. Additional
unnamed numeric values are scenario-specific flags and should be preserved as
`alert_flags` rather than discarded.

Some air-station/corridor aircraft lines omit quantity and alert data. In that
case quantity is `null` and `alert_status` is an empty list.

## 7. Air stations

An air station is a nested block:

```text
AIRSTATION "AirStation"
BEGIN
POS x y altitude
SOURCEOBJECT "CVN 69"
AIRCRAFT "F/A-18 Hornet" "F/A-18 Hornet" "Fighter/Attack" "United States"
LOADOUT1 "ASUW"
LOADOUT2 "ASUW"
END
```

Observed fields are `POS`, `SOURCEOBJECT`, repeated `AIRCRAFT`, `LOADOUT1` and
`LOADOUT2`. `LOADOUT1 " "` is a space-filled empty selection; consumers may
normalize whitespace-only loadout names to `null` if desired.

## 8. Air corridors

Corridors occur either between entities as top-level records or inside a
building/airport record:

```text
AIRCORRIDOR x1 y1 x2 y2 speed mode minimum_altitude maximum_altitude "name"
BEGIN
AIRCRAFT ...
END
```

The first four values are the two endpoints of the corridor line. The remaining
numeric fields are speed, mode/type, minimum altitude and maximum altitude.
Their exact unit conventions are game-specific. The nested aircraft list
describes traffic allowed/associated with the corridor.

## 9. Formations

Named formation mappings occur near the end of the semantic section:

```text
FORMATION "U.S. SAG" "CG 51"
```

The first string is the formation name and the second is the leader's unique
name. Entities can independently carry `FORMATIONLEADER` references.

Do not confuse these records with the `AlliedAlliance`, `ThreatAlliance` and
`NeutralAlliance` membership declarations.

## 10. Goals and triggers

A goal starts with a geographic position and is closed by `END`:

```text
GOAL x y z
BEGIN
GOALNAME "Kill Pirate Ship"
GOALID 1
PARENTGOALID 9
SIDE -1
GOALTYPE 2
DAMAGE 95
STARTTIME 0
ENDTIME 72000
POINTS 0
DEPTH 0
RETURNTOBASE 0
UNIQUENAME "Trawler"
COUNTRY ""
CLASS ""
SHIPNAME ""
TAGNAME ""
SUCCESSMESSAGE
BEGINTEXT
...
ENDTEXT
FAILUREMESSAGE
BEGINTEXT
...
ENDTEXT
MESSAGETYPEID 0
BEGINTEXT
Pirate Ship Sunk
ENDTEXT
END
```

Observed goal fields:

| Field | Meaning |
|---|---|
| `GOALNAME` | Display name. |
| `GOALID` | Numeric goal type/identifier used by the scenario. Repeated values occur. |
| `PARENTGOALID` | Parent goal reference; `-1` means no parent. |
| `SIDE` | Side/alliance applicability. |
| `GOALTYPE` | Goal behavior/type code. |
| `DAMAGE` | Required damage percentage for damage goals. |
| `STARTTIME`, `ENDTIME` | Activation window, normally seconds from scenario start. |
| `POINTS` | Score awarded. |
| `DEPTH` | Goal nesting/depth setting. |
| `RETURNTOBASE` | Return-to-base condition/flag. |
| `AUDIO` | Audio filename played for the goal. |
| `RETURNAUDIO` | Audio used for return/failure handling. |
| `RETURNMESSAGE` | Return/failure message text or reference. |
| `MESSAGETYPEID` | Message presentation/type code. |
| `UNIQUENAME` | Object targeted by the goal. |
| `COUNTRY`, `CLASS`, `SHIPNAME`, `TAGNAME` | Optional target filters. Empty values mean no filter. |
| `SUCCESSMESSAGE` | Multiline success text block. |
| `FAILUREMESSAGE` | Multiline failure text block. |

The numeric codes for `GOALTYPE`, `SIDE`, `MESSAGETYPEID` and related fields
are not enumerated by the supplied scenarios. A parser should preserve their
numeric values and avoid inventing labels without a game-data lookup table.

## 11. Bitmap tail

The tail begins with a header similar to:

```text
BITMAP 180 180 8 180
PALETTE
...
```

It contains palette words and encoded bitmap data. It is not required for
scenario entities, movement or goals. A semantic parser should stop at
`BITMAP`; a complete file decoder may parse it separately.

## 12. Recommended parser strategy

1. Read as Latin-1 and normalize line endings.
2. Lex each line with quote awareness.
3. Parse header text blocks before structural records.
4. Dispatch on `ENTITY`, `NONENTITY`, `GOAL`, `AIRSTATION`, `AIRCORRIDOR`,
   alliance declarations, groups and formations.
5. Treat `BEGIN`/`END` as block delimiters, remembering that corridors and air
   stations have nested blocks and therefore nested `END` markers.
6. Keep repeated keys as lists (`WAYPOINT`, `AIRCRAFT`, text attachments), but
   emit one-value scalar attributes as scalars.
7. Convert empty strings to `null`.
8. Stop at `BITMAP` unless bitmap extraction is explicitly requested.
9. Preserve numeric codes and unrecognized future fields rather than failing
   the whole scenario.

The supplied converter follows this strategy in `scs_converter.py` and has
been batch-tested against all 35 scenarios in this repository.
