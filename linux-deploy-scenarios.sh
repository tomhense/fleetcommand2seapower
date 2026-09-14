#!/usr/bin/bash

OUT_FOLDER="$(dirname "$0")/SeaPower_Missions_Generated"
TARGET_FOLDER="$HOME/.local/share/Steam/steamapps/common/Sea Power/Sea Power_Data/StreamingAssets/user/missions/user_missions/FleetCommand"

if ! [ -d "$TARGET_FOLDER" ]; then
	mkdir "$TARGET_FOLDER"
fi

#cp "$OUT_FOLDER/_info.ini" "$TARGET_FOLDER"
cp "$(dirname "$0")/fleetcommand2seapower/_info.ini" "$TARGET_FOLDER"
cp "$OUT_FOLDER"/Single*.ini "$TARGET_FOLDER"
cp "$OUT_FOLDER"/Region*.ini "$TARGET_FOLDER"

sed -i -E 's/^(Name=)(Bay of Bengal)$/\1Campaign 1: \2/' "$TARGET_FOLDER/Region1.ini"
sed -i -E 's/^(Name=)(Strait of Malacca)$/\1Campaign 2: \2/' "$TARGET_FOLDER/Region2.ini"
sed -i -E 's/^(Name=)(Taiwan Strait)$/\1Campaign 3: \2/' "$TARGET_FOLDER/Region3.ini"
sed -i -E 's/^(Name=)(Kuril Island)$/\1Campaign 4: \2/' "$TARGET_FOLDER/Region4.ini"
