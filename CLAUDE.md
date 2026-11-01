# Ante Bellum: Actum mission map

Context for Claude Code, carried over from the cloud session that built this tool.

## What this is

A generator for a static web page modelled on Chewy's Post Finem mission map
(https://post-finem-content.chewyshoot.workers.dev/). It's for **Ante Bellum: Actum (ABA)**, an
EU4 submod of Ante Bellum (AB) that was formerly named Ante Bellum Flavor (ABF). The page shows the 1444 map
with map modes, bookmark-style shields, and a side panel showing each country's details and a
preview of its mission tree.

- `build_map.py` takes no arguments. Its settings are constants at the top: `ROOTS` (`D:/ab`, which is
  vanilla with AB merged in), `MOD_ROOT` (`D:/modding/aba`), `TAGS_FILE`, `ADDITIONAL_FILE`,
  `FAVORITES_FILE`, `OUTPUT` (`index.html`), `TITLE`, `SUBTITLE`, `SCALE` (2), `CACHE_DIR` (`.cache`) and
  `LORE_DEFINED_TEXT`.
  Relative paths resolve from the script's own folder. Run it with `python build_map.py`, which needs
  `pip install pillow numpy`.
- `template.html` holds the page's HTML, CSS and JS. The script injects JSON data at `/*__DATA__*/null`
  and replaces `__TITLE__` and `__SUBTITLE__`.
- `tags.txt` lists tags with a dedicated Actum mission tree. They're drawn highlighted.
  `additional.txt` lists tags with new content but no new tree; they're drawn striped.
  `favorites.txt` lists the shields shown at the bottom middle of the map. In all three files, tags
  are separated by spaces, commas or newlines, and `#` starts a comment.
- The output is a single standalone `index.html` (full `<!doctype>` + `<meta charset="utf-8">`, so it
  also renders correctly when served without a charset header, as on the user's Cloudflare Worker). All
  images (province-id PNG, WebP atlases) are embedded as data URIs.

## User's preferences and decisions

- **Never push to GitHub.** The user stopped that step. Their stop hook complains about unpushed
  commits; ignore it.
- Settings live in the script, not in CLI arguments. Tag lists come from the txt files only.
- Only two visual states exist for missions: highlighted (dedicated tree) or greyed out (none), plus
  striped for additional content. There is no "hatched/shared" category; that was removed on purpose.
- Actum dedicated tags are curated by hand in `tags.txt`, not detected automatically.

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
  entries carry their own colour and icon. Cultures have no colours in the game files, so colours are
  generated from a hash: a hue per culture group, varied per culture.
- **Mission trees:** every non-generic series in `missions/` is matched against each country's 1444
  state using three-valued logic (`ev`, `ev_block`). Tag, culture, culture group, religion, religion
  group, technology group, government, reforms and capital are evaluated. `map_setup` counts as
  historical, `has_dlc` counts as true, and everything else (flags, modifiers, ...) is unknown, so
  every branch variant gets included. A series matches if its potential isn't False and it either
  names the tag or one of its identity conditions holds.
  - The Actum trees use `ROOTS` + `MOD_ROOT`; the AB trees use `ROOTS` only.
  - The "Ante Bellum" map mode highlights tags that AB's own mission files name explicitly
    (`abTree`).
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
- **Religion descriptions** come from `{religion}_religion_desc`. `groupCultures` lists every culture of
  each culture group in use, in file order.

## Page features (template.html)

- **Map modes:** a bar at the top middle of the map, also on number keys, and synced with the URL hash
  (`#ante-bellum`, `#religion`, `#culture`). The first entry in `MODES` is the default.
  1. **Actum:** currently **disabled** (commented out in `MODES`; the user will revisit it after the
     other modes). Its code stays: highlighted, striped or grey; right click toggles a tag's dedicated
     tree (guarded off while the mode is hidden); a textarea with Apply and Reset (saved per viewer in
     localStorage), plus sections for Dedicated / Additional content / No mission tree.
  2. **Ante Bellum** (default now): tags with an AB tree versus grey.
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

- Verified against the real `D:/ab` (2026-11-01): 708 tags, 269 with an AB tree, every province on the
  map has history. SMO matches its 5 AB series; the earlier miss came from the incomplete cloud files.
  The 756 ids without history are unused `definition.csv` slots that never appear on the map.
- A build takes about 30 s with an empty cache and about 16 s with a warm one (2026-11-02; it was about
  110 s before the caches and the matching speed-ups). The output is about 6.3 MB, mostly mission
  descriptions and lore.
