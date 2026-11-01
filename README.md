# Ante Bellum: Actum mission map

Builds a single standalone HTML page: the 1444 political map with map modes, bookmark-style
shields for suggested countries, and a side panel with each country's flag, religion, culture,
development, lore and a preview of its mission tree.

## Map modes

The bar at the top of the map (number keys):

1. **Ante Bellum**: tags listed in `country_setup/ab.txt` in full colour, everyone else greyed out.
   The country panel shows the tag's Ante Bellum mission tree.
2. **Religion**: provinces coloured by religion; the side panel groups countries by religion, and
   the country panel shows the religion's description.
3. **Culture**: provinces coloured by culture; the side panel groups countries by culture group, and
   the country panel lists every culture of the country's group.

A fourth mode, **Ante Bellum: Actum** (tags from `submod.txt` in full colour, `additional.txt`
striped), is switched off for now in `template.html` (`MODES`).

Click a country for details, wheel to zoom, drag to pan, `R` to reset, `Esc` to close.

## Requirements

Python 3 with Pillow and NumPy:

```
pip install pillow numpy
```

## Setup

1. Edit the settings at the top of `build_map.py`:
   - `ROOTS`: the game folder (vanilla with Ante Bellum's files merged in). It needs `map/`,
     `history/`, `common/` (country_tags, countries, cultures, religions), `missions/`,
     `localisation/`, `customizable_localization/`, `gfx/flags/`, `gfx/interface/` and `interface/`.
   - `MOD_ROOT`: the Actum mod folder. Its missions, localisation and flags override the game folder.
   - `OUTPUT`, `TITLE`, `SUBTITLE`, `SCALE`, `CACHE_DIR`, `LORE_DEFINED_TEXT`, `AB_MISSION_FILES`.
2. Fill in the tag lists in `country_setup/` (separate tags with spaces, commas or new lines; `#`
   starts a comment):
   - `submod.txt`: tags with a dedicated mission tree in Actum.
   - `additional.txt`: tags with other new content but no new tree.
   - `favorites.txt`: suggested countries shown as shields at the bottom of the map, in order.
   - `ab.txt`: tags with an Ante Bellum mission tree, one per line. The script finds each tag's
     tree in the `AB_*.txt` mission files; to choose the files yourself, write
     `TAG = AB_Persia_PER.txt AB_Other.txt`.
3. Optionally edit `culture_setup/culture.txt`: the base colour (R G B) of each culture group in the
   Culture mode. The first build writes it with generated colours; it is never overwritten after that.
4. Run:

   ```
   python build_map.py
   ```

The script reports tags from the lists that don't exist on the map at 1444, `ab.txt` tags whose tree
it couldn't find, unknown mission file names and culture groups without a colour.

Parsed game files and resized images are cached in `.cache/`, so later builds are faster. Delete the
folder to force a full rebuild.

## How mission trees are matched

For each country the script checks each non-generic mission series' `potential` against the
country's 1444 history (tag, culture, culture group, religion, religion group, technology group,
government, reforms, capital). A series matches when one of those conditions holds and the potential
as a whole isn't false. Flags, modifiers and other runtime conditions are treated as "could be true",
so branch variants (locked, unlocked, per religion, ...) all show up. For the Ante Bellum mode only the
`AB_*.txt` files count, and only for tags in `ab.txt`. In the preview, series in the same slot that
overlap become switchable branches (A, B, C).
