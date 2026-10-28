#!/usr/bin/env python3
"""Build the Ante Bellum: Actum mission map: one standalone HTML page.

Edit the settings below, fill in tags.txt / additional.txt / favorites.txt, then run:
    python build_map.py
"""
import base64
import csv
import io
import json
import os
import re
import sys
import numpy as np
from PIL import Image

# ---------------------------------------------------------------- settings
# Relative paths are resolved from this script's folder.

# Game folder(s) with the map setup: vanilla with Ante Bellum's files merged in.
# To layer folders like the game does instead, list them lowest priority first.
ROOTS = [
    r'D:/ab',
]
# The Ante Bellum: Actum mod folder (its missions, localisation and flags override ROOTS).
MOD_ROOT = 'D:/modding/aba'

TAGS_FILE = 'tags.txt'              # tags with a dedicated mission tree in Actum
ADDITIONAL_FILE = 'additional.txt'  # tags with other new content but no new tree
FAVORITES_FILE = 'favorites.txt'    # tags shown as shields at the bottom of the map

OUTPUT = 'mission_map.html'
TITLE = 'Ante Bellum: Actum'
SUBTITLE = 'Mission Map'
SCALE = 2                           # map downscale factor; 1 = full resolution (about 4x file size)

# Localisation keys tried, in order, for each tag's lore text. {TAG} is replaced by the tag.
LORE_KEYS = ['{TAG}_lore', '{TAG}_LORE', '{TAG}_history', '{TAG}_HISTORY']

START_DATE = (1444, 11, 11)

# ---------------------------------------------------------------- parsing

TOKEN_RE = re.compile(r'"[^"]*"|[{}]|[<>]=?|!=|==|=|[^\s{}=<>"#]+|#[^\n]*')


def read_text(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass


def parse(text):
    """Parse Paradox script into a list of (key, value); value is str or list."""
    toks = [t for t in TOKEN_RE.findall(text) if not t.startswith('#')]
    pos = 0

    def block():
        nonlocal pos
        out = []
        while pos < len(toks):
            t = toks[pos]
            if t == '}':
                pos += 1
                return out
            pos += 1
            if pos < len(toks) and toks[pos] in ('=', '<', '>', '<=', '>=', '!=', '=='):
                pos += 1
                if pos >= len(toks):
                    break
                v = toks[pos]
                pos += 1
                if v == '{':
                    v = block()
                out.append((t.strip('"'), v.strip('"') if isinstance(v, str) else v))
            elif t == '{':
                out.append(('', block()))
            else:
                out.append((t.strip('"'), None))  # bare value in a list
        return out

    return block()


def get(items, key, default=None):
    for k, v in items:
        if k == key:
            return v
    return default


def parse_date(s):
    m = re.fullmatch(r'(-?\d+)\.(\d+)\.(\d+)', s)
    return tuple(int(x) for x in m.groups()) if m else None


def history_at(items, date=START_DATE):
    """Flatten a history file: base entries plus dated blocks <= date, in order."""
    base, dated = [], []
    for k, v in items:
        d = parse_date(k)
        if d is None:
            base.append((k, v))
        elif d <= date and isinstance(v, list):
            dated.append((d, v))
    dated.sort(key=lambda x: x[0])
    for _, v in dated:
        base.extend(v)
    return base


# ---------------------------------------------------------------- layered file access

class Layers:
    def __init__(self, roots):
        self.roots = roots

    def files(self, rel_dir, ext=('.txt',)):
        """Merged {filename: path}, later roots overriding earlier ones."""
        out = {}
        for r in self.roots:
            d = os.path.join(r, rel_dir)
            if not os.path.isdir(d):
                continue
            for dp, _, fns in os.walk(d):
                for fn in fns:
                    if fn.lower().endswith(ext):
                        p = os.path.join(dp, fn)
                        out[os.path.relpath(p, d)] = p
        return dict(sorted(out.items()))

    def file(self, rel):
        for r in reversed(self.roots):
            p = os.path.join(r, rel)
            if os.path.isfile(p):
                return p


# ---------------------------------------------------------------- game data

def load_localisation(L):
    loc = {}
    for _, p in L.files('localisation', ('.yml',)).items():
        if 'english' not in p.lower():
            continue
        for line in read_text(p).splitlines():
            m = re.match(r'\s*([\w.\-]+):\d*\s*"(.*)"\s*(#.*)?$', line)
            if m:
                loc[m.group(1)] = m.group(2)
    return loc


def clean_loc(s):
    s = (s or '').replace('\\n', '\n')
    s = re.sub(r'§.', '', s)
    s = re.sub(r'\$[^$]*\$|\[[^\]]*\]|£\w+£?|@[A-Z0-9]{3}\b', '', s)
    return re.sub(r'[ \t]+', ' ', s).strip()


def load_tag_colors(L, loc):
    tag_files = {}
    for _, p in L.files('common/country_tags').items():
        for k, v in parse(read_text(p)):
            if isinstance(v, str) and re.fullmatch(r'[A-Z0-9]{3}', k):
                tag_files[k] = v
    lower = {k.lower(): v for k, v in L.files('common/countries').items()}
    colors = {}
    for tag in set(tag_files) | {t for t in loc if re.fullmatch(r'[A-Z][A-Z0-9]{2}', t)}:
        p = None
        if tag in tag_files:
            p = lower.get(tag_files[tag].replace('countries/', '', 1).lower())
        if p is None and tag in loc:  # tags missing from the tag list: guess the file by name
            p = lower.get(re.sub(r'[^\w ]', '', loc[tag]).lower() + '.txt')
        if p:
            c = get(parse(read_text(p)), 'color')
            if isinstance(c, list):
                try:
                    colors[tag] = [int(float(k)) for k, _ in c][:3]
                except ValueError:
                    pass
    return colors


def load_country_history(L):
    out = {}
    for fn, p in L.files('history/countries').items():
        m = re.match(r'([A-Z0-9]{3})', os.path.basename(fn))
        if not m:
            continue
        st = {'reforms': set()}
        for k, v in history_at(parse(read_text(p))):
            if k == 'add_government_reform':
                st['reforms'].add(v)
            elif isinstance(v, str):
                st[k] = v
        out[m.group(1)] = st
    return out


def load_province_history(L):
    prov = {}
    for fn, p in L.files('history/provinces').items():
        m = re.match(r'\s*(\d+)', os.path.basename(fn))
        if not m:
            continue
        st = {'owner': None, 'dev': 0}
        for k, v in history_at(parse(read_text(p))):
            if k in ('owner', 'culture', 'religion'):
                st[k] = v
            elif k in ('base_tax', 'base_production', 'base_manpower'):
                st[k] = v
        for k in ('base_tax', 'base_production', 'base_manpower'):
            try:
                st['dev'] += int(float(st.pop(k, 0)))
            except ValueError:
                pass
        prov[int(m.group(1))] = st
    return prov


NAME_LISTS = {'male_names', 'female_names', 'dynasty_names', 'country', 'province'}


def load_cultures(L):
    """{culture: group} and [group, ...] in file order."""
    groups, order = {}, []
    for _, p in L.files('common/cultures').items():
        for g, body in parse(read_text(p)):
            if not isinstance(body, list):
                continue
            if g not in order:
                order.append(g)
            for k, v in body:
                if isinstance(v, list) and k not in NAME_LISTS:
                    groups[k] = g
    return groups, order


def load_religions(L):
    """{religion: {group, color, icon}} in file order."""
    out = {}
    for _, p in L.files('common/religions').items():
        for g, body in parse(read_text(p)):
            if not isinstance(body, list):
                continue
            for k, v in body:
                if isinstance(v, list) and (get(v, 'color') is not None or get(v, 'icon') is not None):
                    c = get(v, 'color')
                    rgb = None
                    if isinstance(c, list):
                        try:
                            nums = [float(x) for x, _ in c][:3]
                            rgb = [int(x * 255) if all(n <= 1 for n in nums) else int(x) for x in nums]
                        except ValueError:
                            pass
                    icon = get(v, 'icon')
                    out[k] = {'group': g, 'color': rgb, 'icon': int(icon) if icon and icon.isdigit() else None}
    return out


def load_ids_list(items, key):
    v = get(items, key)
    return {int(k) for k, _ in v if k.isdigit()} if isinstance(v, list) else set()


def load_sprites(L):
    """{sprite name: texture path} from interface/*.gfx."""
    out = {}
    for _, p in L.files('interface', ('.gfx',)).items():
        for m in re.finditer(r'name\s*=\s*"?([\w.]+)"?\s*texturefile\s*=\s*"([^"]+)"', read_text(p)):
            out[m.group(1)] = m.group(2).replace('//', '/')
    return out


# ---------------------------------------------------------------- missions

class Ctx:
    def __init__(self, tag, st, cgroups, religions):
        self.tag, self.st, self.cg, self.rel = tag, st, cgroups, religions


U = None  # unknown in three-valued logic
IDENTITY_KEYS = {'tag', 'was_tag', 'primary_culture', 'culture_group', 'religion', 'religion_group',
                 'technology_group', 'government', 'has_reform', 'capital'}


def tv_and(vals):
    vals = list(vals)
    if any(v is False for v in vals):
        return False
    return True if all(v is True for v in vals) else U


def tv_or(vals):
    vals = list(vals)
    if any(v is True for v in vals):
        return True
    return False if vals and all(v is False for v in vals) else U


def ev_block(items, c):
    return tv_and(ev(k, v, c) for k, v in items)


def ev(k, v, c):
    """Evaluate a trigger against a country's 1444 state. Unknown triggers give U."""
    kl = k.lower()
    if isinstance(v, list):
        if kl == 'and':
            return ev_block(v, c)
        if kl == 'or':
            return tv_or(ev(a, b, c) for a, b in v)
        if kl == 'not':
            r = tv_or(ev(a, b, c) for a, b in v)
            return U if r is U else not r
        return U
    st = c.st
    if kl in ('tag', 'was_tag'):
        return v == c.tag if re.fullmatch(r'[A-Z0-9]{3}', v) else U
    if kl == 'primary_culture':
        return st.get('primary_culture') == v
    if kl == 'culture_group':
        return U if re.fullmatch(r'[A-Z0-9]{3}', v) else c.cg.get(st.get('primary_culture')) == v
    if kl == 'religion':
        return st.get('religion') == v
    if kl == 'religion_group':
        return c.rel.get(st.get('religion'), {}).get('group') == v
    if kl in ('technology_group', 'government'):
        return st.get(kl) == v
    if kl == 'has_reform':
        return v in st.get('reforms', ())
    if kl == 'capital' and v.isdigit():
        return st.get('capital') == v
    if kl == 'map_setup':
        return v != 'map_setup_random'
    if kl == 'has_dlc':
        return True
    return U


def identity_hits(items, c):
    """True if some identity condition outside a NOT holds for this country."""
    for k, v in items:
        kl = k.lower()
        if isinstance(v, list):
            if kl in ('and', 'or') and identity_hits(v, c):
                return True
        elif kl in IDENTITY_KEYS and ev(k, v, c) is True:
            return True
    return False


def explicit_tags(items):
    """Tags named outside a NOT via tag / was_tag."""
    out = set()
    for k, v in items:
        kl = k.lower()
        if isinstance(v, list):
            if kl in ('and', 'or'):
                out |= explicit_tags(v)
        elif kl in ('tag', 'was_tag') and re.fullmatch(r'[A-Z0-9]{3}', v):
            out.add(v)
    return out


def load_series(L):
    series = []
    for fn, p in L.files('missions').items():
        for name, body in parse(read_text(p)):
            if not isinstance(body, list) or get(body, 'generic') == 'yes':
                continue
            missions, last = [], 0
            for mk, mv in body:
                if isinstance(mv, list) and get(mv, 'icon') is not None:
                    pos = get(mv, 'position')
                    pos = int(pos) if pos and pos.isdigit() else last + 1
                    last = pos
                    req = get(mv, 'required_missions') or []
                    missions.append({'id': mk, 'icon': get(mv, 'icon'), 'pos': pos,
                                     'req': [r for r, _ in req] if isinstance(req, list) else []})
            if not missions:
                continue
            pot = get(body, 'potential') or []
            slot = get(body, 'slot')
            series.append({'name': name, 'file': os.path.basename(fn),
                           'slot': int(slot) if slot and slot.isdigit() else 0,
                           'potential': pot, 'tags': explicit_tags(pot), 'missions': missions})
    return series


def series_for(tag, series, ctx):
    """Indexes of the series a tag gets at start, plus whether any names it explicitly."""
    out, named = [], False
    for i, s in enumerate(series):
        if ev_block(s['potential'], ctx) is False:
            continue
        if tag in s['tags']:
            out.append(i)
            named = True
        elif identity_hits(s['potential'], ctx):
            out.append(i)
    return out, named


# ---------------------------------------------------------------- images

def build_province_image(L, scale):
    bmp = Image.open(L.file('map/provinces.bmp')).convert('RGB')
    W, H = bmp.size
    w, h = W // scale, H // scale
    arr = np.asarray(bmp)[::scale, ::scale][:h, :w].astype(np.uint32)
    key = (arr[..., 0] << 16) | (arr[..., 1] << 8) | arr[..., 2]
    lut, names = {}, {}
    with open(L.file('map/definition.csv'), encoding='cp1252', errors='replace') as f:
        for row in csv.reader(f, delimiter=';'):
            if row and row[0].isdigit() and len(row) >= 4:
                try:
                    lut[(int(row[1]) << 16) | (int(row[2]) << 8) | int(row[3])] = int(row[0])
                    names[int(row[0])] = row[4] if len(row) > 4 else ''
                except ValueError:
                    pass
    uniq, inv = np.unique(key, return_inverse=True)
    ids = np.array([lut.get(int(u), 0) for u in uniq], dtype=np.uint16)[inv].reshape(h, w)
    return ids, names


def png_b64(ids):
    rgb = np.zeros(ids.shape + (3,), np.uint8)
    rgb[..., 0] = ids >> 8
    rgb[..., 1] = ids & 255
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, 'PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def open_image(path):
    try:
        im = Image.open(path)
        im.load()
        return im.convert('RGBA')
    except Exception:
        return None


def atlas(images, size, cols=32):
    """Pack {key: image} into one WebP sheet. Returns (data uri, {key: index}, cols)."""
    keys = [k for k, im in images.items() if im is not None]
    if not keys:
        return None, {}, cols
    rows = (len(keys) + cols - 1) // cols
    sheet = Image.new('RGBA', (cols * size, rows * size), (0, 0, 0, 0))
    for i, k in enumerate(keys):
        sheet.paste(images[k].resize((size, size), Image.LANCZOS), ((i % cols) * size, (i // cols) * size))
    buf = io.BytesIO()
    sheet.save(buf, 'WEBP', quality=82, method=6)
    return 'data:image/webp;base64,' + base64.b64encode(buf.getvalue()).decode(), {k: i for i, k in enumerate(keys)}, cols


def find_files(roots, rel_dir, exts):
    """{UPPERCASE stem: path} for files in rel_dir across roots, later roots winning."""
    out = {}
    for r in roots:
        d = os.path.join(r, rel_dir)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                stem, ext = os.path.splitext(fn)
                if ext.lower() in exts:
                    out[stem.upper()] = os.path.join(d, fn)
    return out


def texture(roots, rel):
    """Find a texture referenced from a .gfx file, case-insensitively on the extension."""
    for r in reversed(roots):
        p = os.path.join(r, rel)
        for cand in (p, os.path.splitext(p)[0] + '.dds', os.path.splitext(p)[0] + '.tga'):
            if os.path.isfile(cand):
                return cand


# ---------------------------------------------------------------- main

def here(p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), p))


def read_tags(path):
    """Tags separated by spaces, commas or new lines; '#' starts a comment. Missing file = empty."""
    tags = []
    if not os.path.isfile(path):
        return tags
    for line in open(path, encoding='utf-8-sig'):
        for t in re.split(r'[\s,;]+', line.split('#')[0].upper()):
            if t and t not in tags:
                tags.append(t)
    return tags


def hsl(h, s, l):
    import colorsys
    return [int(x * 255) for x in colorsys.hls_to_rgb(h % 1, l, s)]


def name_hash(s):
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


def main():
    roots = [here(r) for r in ROOTS]
    mod = here(MOD_ROOT)
    for r in roots:
        if not os.path.isdir(r):
            sys.exit(f'Game folder not found: {r}\nSet ROOTS at the top of {os.path.basename(__file__)}.')
    full_roots = roots + ([mod] if os.path.isdir(mod) else [])
    L, Lab = Layers(full_roots), Layers(roots)

    dedicated = read_tags(here(TAGS_FILE))
    additional = [t for t in read_tags(here(ADDITIONAL_FILE)) if t not in dedicated]
    favorites = read_tags(here(FAVORITES_FILE))

    print('reading game files...', file=sys.stderr)
    loc = load_localisation(L)
    colors = load_tag_colors(L, loc)
    hist = load_country_history(L)
    prov = load_province_history(L)
    cgroups, corder = load_cultures(L)
    religions = load_religions(L)
    dm = parse(read_text(L.file('map/default.map')))
    water = load_ids_list(dm, 'sea_starts') | load_ids_list(dm, 'lakes')
    wasteland = load_ids_list(parse(read_text(L.file('map/climate.txt'))), 'impassable')

    print('reading mission trees...', file=sys.stderr)
    series_aba = load_series(L)
    series_ab = load_series(Lab)

    owners = {pid: st['owner'] for pid, st in prov.items()}
    alive = sorted({o for o in owners.values() if o})
    provs, dev = {}, {}
    for st in prov.values():
        if st['owner']:
            provs[st['owner']] = provs.get(st['owner'], 0) + 1
            dev[st['owner']] = dev.get(st['owner'], 0) + st['dev']

    # mission series: keep only those some tag gets, deduplicated across the two layers
    out_series, sid = [], {}

    def keep(s):
        key = (s['file'], s['name'], json.dumps(s['missions']))
        if key not in sid:
            sid[key] = len(out_series)
            out_series.append({
                'name': s['name'], 'file': s['file'], 'slot': s['slot'],
                'missions': [{**m, 'title': clean_loc(loc.get(m['id'] + '_title')) or m['id'],
                              'desc': clean_loc(loc.get(m['id'] + '_desc'))} for m in s['missions']],
            })
        return sid[key]

    tags, ab_tree = {}, []
    for t in alive:
        st = hist.get(t, {})
        ctx = Ctx(t, st, cgroups, religions)
        aba_idx, _ = series_for(t, series_aba, ctx)
        ab_idx, ab_named = series_for(t, series_ab, ctx)
        if ab_named:
            ab_tree.append(t)
        lore = ''
        for k in LORE_KEYS:
            if k.replace('{TAG}', t) in loc:
                lore = clean_loc(loc[k.replace('{TAG}', t)])
                break
        tags[t] = {
            'name': clean_loc(loc.get(t)) or t,
            'color': colors.get(t),
            'culture': st.get('primary_culture'), 'religion': st.get('religion'),
            'provs': provs.get(t, 0), 'dev': dev.get(t, 0), 'lore': lore,
            'aba': [keep(series_aba[i]) for i in aba_idx],
            'ab': [keep(series_ab[i]) for i in ab_idx],
        }

    print('rendering map...', file=sys.stderr)
    ids, pnames = build_province_image(L, SCALE)
    nprov = int(ids.max()) + 1
    p_owner, p_kind, p_cul, p_rel = [''] * nprov, [0] * nprov, [''] * nprov, [''] * nprov
    for pid in range(nprov):  # kind: 0 land no owner, 1 owned, 2 water, 3 wasteland, 4 no history
        st = prov.get(pid)
        if pid in water:
            p_kind[pid] = 2
        elif pid in wasteland:
            p_kind[pid] = 3
        elif st is None:
            p_kind[pid] = 4
        else:
            p_kind[pid] = 1 if st['owner'] else 0
            p_owner[pid] = st['owner'] or ''
            p_cul[pid] = st.get('culture', '')
            p_rel[pid] = st.get('religion', '')

    # cultures and religions in use, with display names and colours
    used_cul = {c for c in p_cul if c} | {t['culture'] for t in tags.values() if t['culture']}
    used_rel = {r for r in p_rel if r} | {t['religion'] for t in tags.values() if t['religion']}
    cul_out = {}
    for c in sorted(used_cul):
        g = cgroups.get(c, '')
        hg, hc = name_hash(g or c), name_hash(c)
        cul_out[c] = {'name': clean_loc(loc.get(c)) or c, 'group': g,
                      'color': hsl((hg % 360) / 360 + ((hc % 21) - 10) / 360, 0.45 + (hc >> 8) % 25 / 100,
                                   0.42 + (hc >> 16) % 22 / 100)}
    grp_out = {g: clean_loc(loc.get(g)) or g.replace('_', ' ').title() for g in corder}
    rel_out = {}
    for r in sorted(used_rel):
        info = religions.get(r, {})
        g = info.get('group', '')
        rel_out[r] = {'name': clean_loc(loc.get(r)) or r, 'group': g,
                      'gname': clean_loc(loc.get(g)) or g.replace('_', ' ').title(),
                      'color': info.get('color') or hsl(name_hash(r) % 360 / 360, 0.5, 0.5)}

    print('packing flags and icons...', file=sys.stderr)
    flag_files = find_files(full_roots, 'gfx/flags', {'.tga', '.png', '.dds'})
    want = set(alive) | set(dedicated) | set(additional) | set(favorites)
    flags_uri, flag_idx, flag_cols = atlas({t: open_image(flag_files[t]) for t in sorted(want) if t in flag_files}, 64)

    sprites = load_sprites(L)
    strip_path = texture(full_roots, sprites.get('GFX_icon_religion', 'gfx/interface/icon_religion.dds'))
    strip = open_image(strip_path) if strip_path else None
    rel_imgs = {}
    if strip is not None:
        fh = strip.size[1]
        frames = strip.size[0] // fh
        for r in used_rel:
            n = religions.get(r, {}).get('icon')
            if n and 1 <= n <= frames:
                rel_imgs[r] = strip.crop(((n - 1) * fh, 0, n * fh, fh))
    rel_uri, rel_idx, rel_cols = atlas(rel_imgs, 48)

    icons = {}
    for s in out_series:
        for m in s['missions']:
            ic = m['icon']
            if ic not in icons:
                tp = sprites.get(ic) or sprites.get('GFX_' + ic)
                p = texture(full_roots, tp) if tp else None
                icons[ic] = open_image(p) if p else None
                if icons[ic] is not None and icons[ic].size[0] > icons[ic].size[1] * 1.5:  # strip: first frame
                    h = icons[ic].size[1]
                    icons[ic] = icons[ic].crop((0, 0, h, h))
    icon_uri, icon_idx, icon_cols = atlas(icons, 48)
    for s in out_series:
        for m in s['missions']:
            m['icon'] = icon_idx.get(m['icon'], -1)

    data = {
        'title': TITLE,
        'w': ids.shape[1], 'h': ids.shape[0],
        'img': png_b64(ids),
        'owner': p_owner, 'kind': p_kind, 'pcul': p_cul, 'prel': p_rel,
        'pname': [clean_loc(loc.get(f'PROV{i}')) or pnames.get(i, '') for i in range(nprov)],
        'tags': tags, 'series': out_series,
        'cultures': cul_out, 'cgroups': grp_out, 'religions': rel_out,
        'dedicated': dedicated, 'additional': additional, 'favorites': favorites, 'abTree': ab_tree,
        'flags': {'uri': flags_uri, 'idx': flag_idx, 'cols': flag_cols},
        'relIcons': {'uri': rel_uri, 'idx': rel_idx, 'cols': rel_cols},
        'msnIcons': {'uri': icon_uri, 'cols': icon_cols},
    }
    tpl = open(here('template.html'), encoding='utf-8').read()
    html = tpl.replace('/*__DATA__*/null', json.dumps(data, separators=(',', ':'), ensure_ascii=False))
    html = html.replace('__TITLE__', TITLE).replace('__SUBTITLE__', SUBTITLE)
    out = here(OUTPUT)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    def report(label, lst):
        missing = [t for t in lst if t not in tags]
        if missing:
            print(f'  {label}: not on the map at start: {" ".join(missing)}', file=sys.stderr)

    print(f'wrote {out} ({os.path.getsize(out) // 1024} KB): {len(alive)} tags, {len(dedicated)} dedicated, '
          f'{len(additional)} additional content, {len(ab_tree)} with an Ante Bellum tree, '
          f'{len(flag_idx)} flags, {len(rel_idx)} religion icons, {len(icon_idx)} mission icons, '
          f'{sum(1 for t in tags.values() if t["lore"])} with lore', file=sys.stderr)
    report('tags.txt', dedicated)
    report('additional.txt', additional)
    report('favorites.txt', favorites)
    nh = sum(1 for k in p_kind if k == 4)
    if nh:
        print(f'  {nh} province ids have no history file (shown as "no data")', file=sys.stderr)


if __name__ == '__main__':
    main()
