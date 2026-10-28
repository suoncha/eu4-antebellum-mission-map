# Ante Bellum: Actum mission map

Builds a single standalone HTML page: the 1444 political map with four map modes, bookmark-style
shields for suggested countries, and a side panel with each country's flag, religion, culture,
development, lore and a preview of its mission tree.

## Map modes

The bar at the top of the map (keys `1`–`4`):

1. **Ante Bellum: Actum**: tags from `tags.txt` in full colour (dedicated mission tree), tags from
   `additional.txt` striped (new content, no new tree), everyone else greyed out.
2. **Ante Bellum**: tags that Ante Bellum's own mission files name explicitly, everyone else greyed out.
3. **Religion**: provinces coloured by religion; the side panel groups countries by religion.
4. **Culture**: provinces coloured by culture; the side panel groups countries by culture group.

Click a country for details, wheel to zoom, drag to pan, `R` to reset, `Esc` to close.
In the Actum mode, right click a country to toggle its dedicated tree (saved in your browser only;
**Reset** goes back to `tags.txt`).

## Requirements

Python 3 with Pillow and NumPy:

```
pip install pillow numpy
```

## Setup

1. Edit the settings at the top of `build_map.py`:
   - `ROOTS`: the game folder (vanilla with Ante Bellum's files merged in). It needs `map/`,
     `history/`, `common/` (country_tags, countries, cultures, religions), `missions/`,
     `localisation/`, `gfx/flags/`, `gfx/interface/` and `interface/`.
   - `MOD_ROOT`: the Actum mod folder. Default `../..` is this repository. Its missions,
     localisation and flags override the game folder.
   - `OUTPUT`, `TITLE`, `SUBTITLE`, `SCALE`, `LORE_KEYS`.
2. Fill in the tag lists (separate tags with spaces, commas or new lines; `#` starts a comment):
   - `tags.txt`: tags with a dedicated mission tree in Actum.
   - `additional.txt`: tags with other new content but no new tree.
   - `favorites.txt`: suggested countries shown as shields at the bottom of the map, in order.
3. Run:

   ```
   python build_map.py
   ```

The script reports tags from the lists that don't exist on the map at 1444, and how many flags,
icons and lore texts it found.

## How mission trees are matched

For each country the script reads every non-generic mission series and checks its `potential`
against the country's 1444 history (tag, culture, culture group, religion, religion group,
technology group, government, reforms, capital). Flags, modifiers and other runtime conditions are
treated as "could be true", so branch variants (locked, unlocked, per religion, ...) all show up.
In the preview, series in the same slot that overlap become switchable branches (A, B, C).
