# Ante Bellum: Actum mission map

Context for Claude Code, carried over from the cloud session that built this tool.

## What this is

A generator for a static web page modelled on Chewy's Post Finem mission map
(https://post-finem-content.chewyshoot.workers.dev/). It's for **Ante Bellum: Actum (ABA)**, an
EU4 submod of Ante Bellum (AB) that was formerly named Ante Bellum Flavor (ABF). The page shows the 1444 map
with map modes, bookmark-style shields, and a side panel showing each country's details and a
preview of its mission tree.

- `build_map.py` takes no arguments. Its settings are constants at the top: `ROOTS` (`D:/ab`, which is
  vanilla with AB merged in), `MOD_ROOT` (`D:/modding/aba`), `SUBMOD_FILE`, `ADDITIONAL_FILE`,
  `FAVORITES_FILE`, `AB_FILE`, `AB_MISSION_FILES` (`AB_*.txt`), `CULTURE_COLORS_FILE`, `OUTPUT`
  (`index.html`), `TITLE` (user set it to `Ante Bellum`), `SUBTITLE`, `SCALE` (user set it to 1),
  `CACHE_DIR` (`.cache`) and `LORE_DEFINED_TEXT`.
  Relative paths resolve from the script's own folder. Run it with `python build_map.py`, which needs
  `pip install pillow numpy`.
- `template.html` holds the page's HTML, CSS and JS. The script injects JSON data at `/*__DATA__*/null`
  and replaces `__TITLE__` and `__SUBTITLE__`.
- `country_setup/` holds the tag lists (`#` starts a comment):
  - `submod.txt` (was `tags.txt`): tags with a dedicated Actum mission tree, drawn highlighted.
  - `additional.txt`: tags with new content but no new tree, drawn striped.
  - `favorites.txt`: the shields shown at the bottom middle of the map.
  - `ab.txt`: tags with an Ante Bellum tree, one per line, optionally `TAG = file.txt file2.txt`
    (`read_tag_map`). The first three are space/comma/newline separated (`read_tags`).
- `culture_setup/culture.txt`: base colour per culture group (`group = R G B`). The build writes it
  when missing and never overwrites it.
- The output is a single standalone `index.html` (full `<!doctype>` + `<meta charset="utf-8">`, so it
  also renders correctly when served without a charset header, as on the user's Cloudflare Worker). All
  images (province-id PNG, WebP atlases) are embedded as data URIs.

## User's preferences and decisions

- **Never push to GitHub.** The user stopped that step. Their stop hook complains about unpushed
  commits; ignore it.
- Settings live in the script, not in CLI arguments. Tag lists come from the txt files only.
- Only two visual states exist for missions: highlighted (dedicated tree) or greyed out (none), plus
  striped for additional content. There is no "hatched/shared" category; that was removed on purpose.
- Actum dedicated tags are curated by hand in `submod.txt`, not detected automatically.
- AB tags are curated by hand in `ab.txt` (generated once on 2026-11-02 from AB-file matching; 57 tags
  that only vanilla files mention are there commented out). The user maps shared trees themselves,
  e.g. Persian tags → `AB_Persia_PER.txt`.
- Show proposed changes for approval before editing files in this folder (plan mode is used for that).

## How it works (build_map.py)

- **Paradox parser:** `parse()` returns a list of (key, value) pairs. `history_at()` applies dated
  blocks up to 1444.11.11. `Layers` merges folders, with later roots overriding earlier ones by
  relative path.
- **Cache:** `.cache/parse.pkl` holds parsed script files (`parse_file()`), `.cache/images.pkl` holds
  resized images as PNG bytes (`open_image(path, size)`). Keys include path, mtime and size, so edited
  files are re-read; entries not used in a run are dropped. Delete `.cache/` to force a full rebuild.
- **Map data:** `map/provinces.bmp` and `definition.csv` produce a province-id image, downscaled by
  `SCALE` and encoded into a PNG as R=id>>8, G=id&255. Water comes from `default.map`
  (`sea_starts` and `lakes`) and wasteland from the `impassable` list in `climate.txt`.
- **Province history** supplies owner, culture, religion and development
  (tax + production + manpower).
- **Tag colours** come from `common/country_tags` and then `common/countries`. Tags missing from the
  tag list fall back to a file guessed from the country's name.
- **Cultures and religions:** groups are read from `common/cultures` and `common/religions`; religion
  entries carry their own colour and icon. Cultures have no colours in the game files: each group's
  base colour comes from `culture.txt` (fallback `group_base_color`, a hue from a hash), and
  `culture_color` varies it per culture (hue ±10°, saturation/lightness ±0.1).
- **Mission trees:** non-generic series in `missions/` are matched against each country's 1444
  state using three-valued logic (`ev`, `ev_block`). Tag, culture, culture group, religion, religion
  group, technology group, government, reforms and capital are evaluated. `map_setup` counts as
  historical, `has_dlc` counts as true, and everything else (flags, modifiers, ...) is unknown, so
  every branch variant gets included. A series matches if one of its identity conditions outside a
  `NOT` holds and its potential isn't False. For speed, each series' identity conditions are a
  precomputed set (`identity_leaves`) intersected with the country's `identity_facts`.
  - The Actum trees use all series from `ROOTS` + `MOD_ROOT`.
  - The AB trees: only series in `AB_MISSION_FILES` from `ROOTS` (`D:/ab` also holds vanilla files),
    and only for tags in `ab.txt`: mapped files → all their series, else the matching series. The
    Ante Bellum mode highlights the `ab.txt` tags on the map (`abTree`).
- **Images:** flags (`gfx/flags/*.tga`), religion icons (64 px frames of `icon_religion.dds`, frame
  number `icon - 1`) and mission icons (sprite name = mission `icon`, resolved through
  `interface/*.gfx`) are packed into WebP atlases.
- **Lore** is the start-screen text from the defined_text `AnteBellumCountryLoreDesc` in
  `customizable_localization/00_country_lore.txt`: the first `text` whose trigger is True wins (ends in
  an `always = yes` fallback). Triggers go through `ev`, extended with `always`, `is_random_new_world`
  and `capital_scope` (area/region/superregion/continent from `map/*.txt`, see `load_geography`).
  `fill_placeholders` fills `[GetYear]`, `[Root.GetName]`, `[Root.GetAdjective]`,
  `[Root.Monarch.Dynasty.GetName]` and `[Root.GovernmentName]` (the last is approximated from government
  type and rank).
  Lore is deduplicated: `D.lore` is the list of distinct texts (176 for 708 tags) and `tags[t].lore` an
  index into it.
- **Religion descriptions** come from `{religion}_religion_desc`. `groupCultures` lists every culture of
  each culture group in use, in file order.

## Page features (template.html)

- **Map modes:** a bar at the top middle of the map, also on number keys, and synced with the URL hash
  (`#ante-bellum`, `#religion`, `#culture`). The first entry in `MODES` is the default.
  1. **Actum:** currently **disabled** (commented out in `MODES`; the user will revisit it after the
     other modes). Its code stays: highlighted, striped or grey; right click toggles a tag's dedicated
     tree (guarded off while the mode is hidden); a textarea with Apply and Reset (saved per viewer in
     localStorage), plus sections for Dedicated / Additional content / No mission tree.
  2. **Ante Bellum** (default now): `ab.txt` tags versus grey.
  3. **Religion:** provinces coloured by religion. The side panel groups countries by religion, with
     religion-group headings and icons.
  4. **Culture:** provinces coloured by culture. The side panel groups countries by culture group.
- **Shields:** shown at the bottom middle for the tags in `favorites.txt`. The shield shape is drawn
  on the flag itself (same as the detail header), so the flag fills it. Clicking one selects that
  country and zooms to it.
- **List rows:** flag, name, code, and "N prov · D dev" on the right. Sections are collapsible
  `<details>` elements; a search opens only the sections that match.
- **Country detail:** flag, name and religion icon; chips (status, culture, religion, provinces,
  development); a Lore section; then a section that depends on the mode: mission tree preview
  (Ante Bellum / Actum), religion icon + group + description (Religion), or the culture group with all
  its cultures as chips, the country's own highlighted (Culture).
  - The preview uses one column per slot. Series in the same slot whose positions overlap become A/B/C
    branch tabs.
  - Hovering a mission card shows its description and highlights the missions it requires.
  - Ante Bellum mode shows the AB tree; Actum mode shows the Actum tree.
- **Map rendering:** per-province colour arrays are drawn onto an offscreen canvas with country
  borders, coastlines and a highlighted outline for the selected tag. Supports zoom (wheel, buttons,
  pinch), drag to pan, R to reset and Esc to close.
- **Design:** theme tokens on `:root` with dark-mode variants. Fonts are Alegreya SC, Source Sans 3
  and JetBrains Mono.

## Known gaps and notes

- Verified against the real `D:/ab` (2026-11-01): 708 tags, every province on the map has history.
  SMO matches its AB series; the earlier miss came from the incomplete cloud files. The 756 ids without
  history are placeholder `definition.csv` slots (id 0, `UnusedLand*`, `RNW`) that never appear on the
  map, so the "no history" warning only counts provinces that are drawn.
- A warm-cache build takes about 8 s (2026-11-02; about 110 s before the caches, the matching index and
  the lore dedupe). The output is about 6.1 MB with `SCALE = 1`: mission series ~2.3 MB (descriptions
  1.56 MB), mission icons 1.4 MB, province image 1 MB, flags 0.75 MB.
