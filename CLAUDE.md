# Ante Bellum: Actum mission map

Context for Claude Code, carried over from the cloud session that built this tool.

## What this is

A generator for a static web page modelled on Chewy's Post Finem mission map
(https://post-finem-content.chewyshoot.workers.dev/). It's for **Ante Bellum: Actum (ABA)**, an
EU4 submod of Ante Bellum (AB) that was formerly named Ante Bellum Flavor (ABF). The page shows the 1444 map
with map modes, bookmark-style shields, and a side panel showing each country's details and a
preview of its mission tree.

- `build_map.py` takes no arguments. Its settings are constants at the top: `ROOTS` (`D:/ab`, which is
  vanilla with AB merged in), `MOD_ROOT` (`D:/modding/aba`), `ACTUM_FILE`, `ADDITIONAL_FILE`,
  `FAVORITES_FILE`, `ACTUM_FAVORITES_FILE`, `AB_FILE`, `AB_MISSION_FILES` (`AB_*.txt`), `CULTURE_COLORS_FILE`, `OUTPUT`
  (`index.html`), `TITLE` (user set it to `Ante Bellum`), `SUBTITLE`, `SCALE` (user set it to 1),
  `CACHE_DIR` (`.cache`), `MUSIC_FILE` (`theme.ogg`), `MUSIC_BITRATE` (`64k`: re-encoded to Opus with ffmpeg,
  the user's choice; `''` = as is), `ATLAS_FORMAT`/`ATLAS_QUALITY` (`AVIF`, 60) and
  `LORE_DEFINED_TEXT`.
  Relative paths resolve from the script's own folder. Run it with `python build_map.py`, which needs
  `pip install pillow numpy`.
- `template.html` holds the page's HTML, CSS and JS. The script replaces `/*__DATA__*/` with the text
  data (JSON → gzip -9 → base64), `/*__BINARY__*/null` with `{img, flags, relIcons, msnIcons, music}`
  (base64 / data URIs), and `__TITLE__` / `__SUBTITLE__`. Both data placeholders sit in
  `<script type="text/plain" id="aba-data">` / `id="aba-bin"` blocks *outside* the module (so the JS
  engine never parses or keeps megabytes of string literals; −10 % startup, −2 MB heap); the module reads
  their `textContent` (`takeText`) and removes them. The main script is `<script type="module">`
  so it can top-level `await`: `unpack()` decodes base64 with `fetch('data:…')`, `DecompressionStream`
  unzips the JSON. Module scope means page functions aren't globals (test probes go inside the module).
- `country_setup/` holds the tag lists (`#` starts a comment):
  - `actum.txt` (replaced `submod.txt` on 2026-11-11): tags with an Actum mission tree, highlighted in
    the Actum mode. Same syntax as `ab.txt` (auto-detect, `TAG = files`, `|` variants, "found:" comments).
    15 tags: WLS BAV STY LIT LVA BOH MVA PLN LUD NOV YUA ROB SLH KRR KUC.
  - `additional.txt`: tags with new Actum content but no new tree, drawn striped in the Actum mode
    (NOR DAN SWE SCO).
  - `favorites.txt`: the shields at the bottom middle of the map in every mode but Actum.
  - `actum_favorites.txt`: the shields in the Actum mode (CAP NCI PLN ZAZ SLH).
  - `ab.txt`: tags with an Ante Bellum tree, one per line, optionally `TAG = file.txt file2.txt`
    (`read_tag_map`). Switchable trees are separated by `|`, each `Label: files`; the first is the
    original (label optional, empty files = auto-detect). A label that is a tag shows as "Form <country>"
    with its flag, e.g. `ODA = | JAP: AB_Asia_JAP.txt`, `YUA = Chinese: … | Horde: … | Nestorian: …`.
    All daimyo tags have Form Japan; all `AB_German_(BAV|FKN|HAS|HLV|PRU|RHL|SAX|STY|SWA)` tags have
    `GER: AB_German_ZZZ.txt`. The first three files are space/comma/newline separated (`read_tags`).
- `mission_check/`: the user's working copies of AB trees that need fixing (formables, branching). Not
  read by the build: trees come from `D:/ab/missions`.
- `culture_setup/culture.txt`: base colour per culture group (`group = R G B`). The build writes it
  when missing and never overwrites it.
- The output is a single standalone `index.html` (full `<!doctype>` + `<meta charset="utf-8">`, so it
  also renders correctly when served without a charset header, as on the user's Cloudflare Worker). All
  images (province-id lossless WebP, AVIF atlases) and the music (Ogg Opus) are embedded. On load the
  atlases become `blob:` URLs: with data URIs in `var(--flags)` every sprite re-parsed megabytes, which
  made selecting a country take 2.9 s. The music is unpacked in the background. Missions are embedded
  compact (`[index, icon, pos, [required indexes], title, desc]`, numeric ids) and expanded into objects
  right after loading. Google Fonts load via `rel=preload` + `onload` so they don't block the first paint.

## User's preferences and decisions

- **Never push to GitHub.** The user stopped that step. Their stop hook complains about unpushed
  commits; ignore it.
- Settings live in the script, not in CLI arguments. Tag lists come from the txt files only.
- Only two visual states exist for missions: highlighted (dedicated tree) or greyed out (none), plus
  striped for additional content. There is no "hatched/shared" category; that was removed on purpose.
- Actum tree tags are curated by hand in `actum.txt`, not detected automatically.
- **One world:** every map mode shows AB with the submod layered on top (starting countries, the
  Americas especially, histories, cultures, religions, flags, lore). The modes differ only in the trees:
  Ante Bellum = vanilla AB trees, Actum = the submod's trees. (A two-world design was rejected.)
- AB tags are curated by hand in `ab.txt` (generated once on 2026-11-02 from AB-file matching; 57 tags
  that only vanilla files mention are there commented out). The user maps shared trees themselves,
  e.g. Persian tags → `AB_Persia_PER.txt`.
- Show proposed changes for approval before editing files in this folder (plan mode is used for that).

## How it works (build_map.py)

- **Paradox parser:** `parse()` returns a list of (key, value) pairs. `history_at()` applies dated
  blocks up to 1444.11.11. `Layers` merges folders, with later roots overriding earlier ones by
  relative path.
- **Cache** (`.cache/`, keys include path, mtime and size; entries not used in a run are dropped; delete
  the folder to force a full rebuild):
  - `game.pkl`: everything `load_game()` reads from the game files (localisation, colours, histories,
    cultures, religions, geography, lore texts, mission series, sprites) plus `game_texts()`: cleaned
    country names, each country's chosen lore, mission titles/descriptions and province names. Keyed by
    every input file and this script itself. With unchanged game files nothing is parsed (`parse.pkl` is
    only opened on a miss). File dates come from the directory listing (`walk()` fills `STATS`, free on
    Windows), so checking ~10,500 files needs no separate `os.stat` calls; each folder is listed once per
    run (`WALKED`) and `texture()` looks files up in one cached folder listing (`LISTED`).
    `slim_texts()` keeps only the localisation and defined_texts the page build reads afterwards (tag
    names/`_ADJ`, cultures, religions, flag/modifier names, and what `mission_raw` placeholders reach
    through defined_texts): 30 MB → 7 MB, ~0.15 s off every warm build.
  - `parse.pkl`: parsed script files (`parse_file()`), used when `game.pkl` misses.
  - `images.pkl`: resized images as PNG bytes (`open_image`), the province image + per-province bboxes
    (`province_image`), the encoded atlases (`atlas()`, keyed by its entries' cache keys, so an unchanged
    sheet opens no images) and the Opus music (`music_data`).
- **Map data:** `map/provinces.bmp` and `definition.csv` produce a province-id image, downscaled by
  `SCALE` and encoded as lossless WebP (R=id>>8, G=id&255; exact pixels, 40 % of a PNG). Per-country
  bounding boxes (`D.bbox`) come from per-province boxes computed with numpy. Water comes from `default.map`
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
  - The world (`L`) is always `ROOTS` + `MOD_ROOT`; only the AB mission files come from `ROOTS` alone
    (`Lab`). The submod's culture files predate AB 1.10, so e.g. Toledan has no group until they are
    updated; its `punic_g` group has no colour in culture.txt (generated).
  - The Actum trees: only for tags in `actum.txt`; candidates are the merged layer's `AB_*` series (the
    submod's file wins over AB's of the same name) plus every file in `MOD_ROOT/missions` (`ABMP_*`,
    `TLW_*`). Both tree kinds go through one loop (`KINDS`) and the `tree()` helper; Actum data is
    `tags[t].act`, `actForms`, `actBr`, `actMt` (AB: `ab`, `abForms`, `br`, `mt`).
  - The AB trees: only series in `AB_MISSION_FILES` from `ROOTS` (`D:/ab` also holds vanilla files),
    and only for tags in `ab.txt`: mapped files → all their series, else the matching series. The
    Ante Bellum mode highlights the `ab.txt` tags on the map (`abTree`). Variants from `|` go to
    `tags[t].abForms = [{label, tag?, ab, mt?, br?}]` (variant 0 = the original tree).
  - **Branching paths** (`branch_packs`): series whose potentials check the same country flags or
    modifiers (`has_country_flag` / `has_country_modifier`, linked by co-occurrence) form a pack. Every
    True/False assignment is evaluated (`Ctx.switch`); the distinct sets of visible series that don't
    share a slot position are the options; subsets and series in every option drop out. The option with
    the fewest flags set is first, labelled "Default"; others are named from the flag's localisation or
    the flag name minus the pack's shared prefix/suffix. Stored as `br: [[[label, [series]], …], …]` per
    tree. Formed trees evaluate as the formed tag (`Ctx.was` = {formed, original} for `was_tag`).
  - **Dynamic localisation** (`resolve_loc`, also used for lore): `[..]` placeholders are filled per
    country: defined_texts (first True text, else first non-False non-empty), name/adjective/government/
    religion/capital, `<province id>.GetName`, monarch name/title/pronouns, `<TAG>.…` scopes. Unknown ones
    become empty (`[6.Capital.GetName]`, `[8.GetAdjective]` in AB's Swedish loc are AB bugs). Texts are
    resolved for the first country using a series; other countries get overrides in `tags[t].mt`
    (`{mission index: [title, desc]}`).
- **Images:** flags (`gfx/flags/*.tga`), religion icons (64 px frames of `icon_religion.dds`, frame
  number `icon - 1`) and mission icons (sprite name = mission `icon`, resolved through
  `interface/*.gfx`) are packed into WebP atlases.
- **Lore** is the start-screen text from the defined_text `AnteBellumCountryLoreDesc` in
  `customizable_localization/00_country_lore.txt`: the first `text` whose trigger is True wins (ends in
  an `always = yes` fallback). Triggers go through `ev`, extended with `always`, `is_random_new_world`
  and `capital_scope` (area/region/superregion/continent from `map/*.txt`, see `load_geography`).
  Placeholders are filled by `resolve_loc` (see Dynamic localisation; `[Root.GovernmentName]` is
  approximated from government type and rank).
  Lore is deduplicated: `D.lore` is the list of distinct texts (176 for 708 tags) and `tags[t].lore` an
  index into it.
- **Religion descriptions** come from `{religion}_religion_desc`. `groupCultures` lists every culture of
  each culture group in use, in file order.

## Page features (template.html)

- **Map modes:** a bar at the top middle of the map, also on number keys, and synced with the URL hash
  (`#ante-bellum`, `#religion`, `#culture`). The first entry in `MODES` is the default.
  1. **Ante Bellum** (default): `ab.txt` tags versus grey; vanilla AB trees.
  2. **Actum** (restored 2026-11-11): `actum.txt` tags highlighted, `additional.txt` striped, otherwise
     grey; the submod's trees. The old in-page editor (textarea, right-click toggle, localStorage) is gone.
  3. **Religion:** provinces coloured by religion. The side panel groups countries by religion, with
     religion-group headings and icons.
  4. **Culture:** provinces coloured by culture. The side panel groups countries by culture group.
- **Shields:** shown at the bottom middle for the tags in `favorites.txt` (`actum_favorites.txt` in the
  Actum mode; redrawn only when the list changes, `favorites()`). The shield shape is drawn
  on the flag itself (same as the detail header), so the flag fills it. Clicking one selects that
  country and zooms to it.
- **Shared list** (Ante Bellum and Actum modes): sections Ante Bellum mission tree / Actum mission tree /
  No mission tree (neither); a country can be in both tree sections. A row in a tree section opens its
  country in that section's mode (`data-mode`, `openRow`).
- **List rows:** flag, name, code, and "N prov · D dev" on the right. Sections are collapsible
  `<details>` elements; a search opens only the sections that match. Closed sections get their rows
  only when first opened (`lazyRows`).
- **Country detail:** flag, name and religion icon; chips (status, culture, religion, provinces,
  development); a Lore section; then a section that depends on the mode: mission tree preview
  (Ante Bellum / Actum), religion icon + group + description (Religion), or the culture group with all
  its cultures as chips, the country's own highlighted (Culture).
  - The preview is laid out like EU4: one column per slot, always at least 5 (unused slots stay empty),
    one row per position, no "Slot N" labels.
  - Status chips "Ante Bellum mission tree" / "Actum mission tree" are buttons that switch mode
    (`data-go`); "Actum additional content" is a plain chip. A country with only the other mode's tree
    shows a note with a "Show it" button. `TREE[mode](x)` picks the tree fields.
  - Form buttons (from `abForms` / `actForms`) sit above the tree; the choice is kept per mode and tag in
    memory (`formState`, `branchState`).
  - Branching paths are switched from a **rail** (`#rail`) pinned to the sidebar's left edge over the map
    (below the zoom buttons), so it stays put while the tree scrolls (modelled on AB's in-game mission
    preview tabs). Groups are headed "Branching 1", "Branching 2", …; the no-flag option is "Default".
    Flag packs come first; then slots whose series still overlap without a flag (Germany's state fifth
    groups), labelled by the words their series names don't share (`seriesLabels`). A series that overlaps
    nothing in its slot is always shown. At phone width the rail is a horizontal strip above the panel.
    Choices are kept in memory (`branchState`, `treeState`).
  - `drawArrows` draws SVG arrows behind the cards from each required mission to the missions needing
    it, routed by `arrowRoutes` the way EU4 does (the user compared against EU4's Andalusia tree and
    rejected a staggered-lane version as unbalanced): every arrow turns once, in the middle of the
    fixed 24 px gap below its source. Exit/entry points (`edgePoints`) are on the side the arrow heads
    to / comes from: left ones spread over 15–40 % of the card width (farther ones further out), right
    ones over 60–85 %, same-column arrows at the centre as one straight line. If an entry would line up
    with an unrelated exit of the card directly above (which reads as one line), it is moved outwards.
    A `ResizeObserver` redraws the arrows when the panel width changes. Known quirk (like EU4): an arrow
    that skips a row runs behind any card in between.
  - Hovering a mission card shows its description and "Requires: …" (required missions are no longer
    highlighted; the user prefers EU4's arrows).
  - Testing tip: headless Edge with SwiftShader (`--use-angle=swiftshader`) crashed the user's laptop
    twice; test panel/tree changes with `--disable-gpu` (no globe, light) and one browser at a time.
    Launch Edge from PowerShell (`Start-Process … -RedirectStandardOutput … -Wait`): from the Bash tool it
    printed nothing.
- **Map rendering: WebGL 2 globe** (the user chose a 3D globe over a flat horizontal wrap).
  - Pass 1, `paint()`, runs on mode change, select, theme change and tag edits. `provinceColors()` fills
    per-province data textures (256 wide: colour + stripe flag, stripe colour, owner + water). A shader
    draws the flat W×H map into a framebuffer texture with country borders and coasts (60 % border),
    province borders (12 %), the selected country's accent outline and the stripes, then mipmaps it.
  - Pass 2, `render()`, runs every frame: a full-screen shader ray-casts an orthographic sphere and maps
    lon/lat to the texture. u covers 360°. v runs linearly from `LAT_TOP` to `LAT_BOTTOM` (75 / −58);
    these are tunable, and because EU4's map isn't really equirectangular the far north looks stretched.
    Beyond that band it draws sea. `textureGrad` with a seam-moved copy of u avoids a line at the date
    line. NEAREST when zoomed in, mipmapped when out.
  - View = `{lon, lat, R}`. The functions are `rotateBy` (drag), `zoomAt` (wheel/buttons/pinch, keeps the
    cursor point), `flyTo` (eased), `focusTag` (country bbox → lon/lat and size), `reset` (first
    favourite, whole globe), and `screenToLonLat` / `pick` for hover and click. `pick` reads the one
    pixel under the cursor from a 2D canvas of the id image (`idAt`, ~0.03 ms) instead of keeping a
    copy of all 11.5 M pixels; that 2D canvas is only created on the first hover/click. The id image is
    decoded off the main thread with `createImageBitmap(…, {colorSpaceConversion: 'none'})` (exact ids).
  - If `MAX_TEXTURE_SIZE < W`, the id image is downscaled nearest-neighbour for the GPU only.
  - No WebGL 2 → a message in the hint box.
- **Theme button** (sun/moon, next to the title): sets `data-theme` on `<html>`, saved in localStorage
  `aba-mission-map-theme`. A small `<head>` script applies it before first paint, and `retheme()`
  re-reads the colour tokens (`theme()` caches them).
- **Music button**: loops `BINARY.music` at volume 0.5. Autoplay is usually refused, so it starts on the first
  click or key press. A pause is saved in localStorage `aba-mission-map-music`.
- **Design:** theme tokens on `:root` with dark-mode variants. Fonts are Alegreya SC, Source Sans 3
  and JetBrains Mono.

## Known gaps and notes

- Verified against the real `D:/ab` (2026-11-01): 708 tags, every province on the map has history.
  SMO matches its AB series; the earlier miss came from the incomplete cloud files. The 756 ids without
  history are placeholder `definition.csv` slots (id 0, `UnusedLand*`, `RNW`) that never appear on the
  map, so the "no history" warning only counts provinces that are drawn.
- A warm-cache build takes about 0.6 s in `main()`, 0.8 s with Python start (2026-11-07; ~110 s
  originally, 4 s before the game cache, 1.7 s before caching the per-country texts, 0.94 s before the
  2026-11-07 pass). A build after a game file or build_map.py change takes ~2.2 s while `parse.pkl` is
  valid (~11–16 s with no cache). The output is about 4.7 MB with `SCALE = 1` (was 11.2 MB): music ~2 MB
  (Opus 64k base64; the user chose embedding over a separate file), gzipped text data ~1 MB (mission
  descriptions are 524 KB of the 600 KB of series text), atlases ~1.2 MB, province image 0.4 MB.
- With Actum restored (2026-11-11): 700 countries in the merged world, page 5.1 MB, warm build 0.75 s
  (`main()`). Page timings in headless Edge with `--disable-gpu` (no globe paint): startup ~220 ms
  (~110 ms of it is the browser parsing the HTML), select ~5 ms, back to list ~8 ms, mode switch ~2 ms
  (Religion/Culture), ~8 ms (Ante Bellum, 300+ list rows), ~20 ms into Actum (new shields). Benchmark copies go in the scratchpad; compare against an older page with
  `git show <commit>:index.html`.
- Older timings with SwiftShader (CPU-emulated GPU, noisy): startup script ~0.8 s; map ready ~2.7 s,
  dominated by emulated GPU setup; select ~15–50 ms, back to list ~50 ms, mode switch ~5–15 ms. Use real time (`--timeout`), not
  `--virtual-time-budget`, when timing: the virtual clock gave 0 ms readings inside module code.
  `--dump-dom` / `--screenshot` capture at the page's load event, and the module's async work (data
  unpack, ~1 MB `fetch('data:…')`) can finish just after it, so a probe never runs. In test copies, delay
  the load event, e.g. `<img src="https://httpbin.org/delay/4" hidden>` in `<body>`.
- Safari plays Ogg only from Safari 17 (macOS 14 / iOS 17).
