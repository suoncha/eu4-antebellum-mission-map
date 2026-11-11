#!/usr/bin/env python3
"""Build the Ante Bellum: Actum mission map: one standalone HTML page.

Edit the settings below, fill in the tag lists in country_setup/ and the colours in culture_setup/, then run:
    python build_map.py
"""
import base64
import colorsys
import csv
import fnmatch
import gzip
import hashlib
import io
import json
import os
import pickle
import re
import shutil
import subprocess
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
# The Ante Bellum: Actum mod folder. It is layered over ROOTS for the whole map (starting countries,
# histories, cultures, religions, flags, lore, ...); only the Ante Bellum trees come from ROOTS alone.
MOD_ROOT = 'D:/modding/aba'

ACTUM_FILE = 'country_setup/actum.txt'            # tags with an Actum tree, optionally mapped to files
ADDITIONAL_FILE = 'country_setup/additional.txt'  # tags with other new Actum content but no new tree
FAVORITES_FILE = 'country_setup/favorites.txt'    # shields at the bottom of the map (all modes but Actum)
ACTUM_FAVORITES_FILE = 'country_setup/actum_favorites.txt'  # shields in the Actum mode
AB_FILE = 'country_setup/ab.txt'                  # tags with an Ante Bellum tree, optionally mapped to files
AB_MISSION_FILES = 'AB_*.txt'                     # mission files (in ROOTS) that hold Ante Bellum trees
# Actum trees: these files in ROOTS + MOD_ROOT (the mod's versions win), plus every file in MOD_ROOT/missions
CULTURE_COLORS_FILE = 'culture_setup/culture.txt' # base colour per culture group; written if missing

OUTPUT = 'index.html'
TITLE = 'Ante Bellum'
SUBTITLE = 'Mission Map'
SCALE = 1                           # map downscale factor; 1 = full resolution (about 4x file size)
CACHE_DIR = '.cache'                # parsed game files and resized images; delete it to force a full rebuild
MUSIC_FILE = 'theme.ogg'            # music embedded in the page, with a play/pause button; '' for none
MUSIC_BITRATE = '64k'               # re-encode the music to Opus at this bitrate (needs ffmpeg); '' to embed as is
ATLAS_FORMAT, ATLAS_QUALITY = 'AVIF', 60  # flag / icon sheets (AVIF is about 40 % smaller than WebP)

# defined_text in customizable_localization/ that picks each country's start-screen lore.
LORE_DEFINED_TEXT = 'AnteBellumCountryLoreDesc'

START_DATE = (1444, 11, 11)

# ---------------------------------------------------------------- parsing

TOKEN_RE = re.compile(r'"[^"]*"|[{}]|[<>]=?|!=|==|=|[^\s{}=<>"#]+|#[^\n]*')
TAG_RE = re.compile(r'[A-Z0-9]{3}')


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


# ---------------------------------------------------------------- cache
# Pickled {key: value}, keyed by file path + mtime + size so edited files are re-read.

class Cache:
    def __init__(self, name):
        self.path = os.path.join(here(CACHE_DIR), name)
        self.used, self.added = set(), False
        try:
            with open(self.path, 'rb') as f:
                self.data = pickle.load(f)
        except Exception:
            self.data = {}

    @staticmethod
    def key(path, *extra):
        st = STATS.get(path) or os.stat(path)
        return (os.path.normcase(path), st.st_mtime_ns, st.st_size) + extra

    def get(self, key, make):
        self.used.add(key)
        if key not in self.data:
            self.data[key] = make()
            self.added = True
        return self.data[key]

    def save(self):
        """Write back only the entries used in this run, dropping those of changed or deleted files."""
        if self.added or self.used != set(self.data):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'wb') as f:
                pickle.dump({k: self.data[k] for k in self.used}, f, protocol=pickle.HIGHEST_PROTOCOL)


PARSED = None  # Cache of parsed script files, set up in main()


def parse_file(path):
    return PARSED.get(Cache.key(path), lambda: parse(read_text(path)))


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

STATS = {}  # path -> stat from the directory listings (free on Windows), so Cache.key needs no os.stat


def walk(d):
    """Paths of all files under d, recording their stats in STATS."""
    for e in os.scandir(d):
        if e.is_dir():
            yield from walk(e.path)
        else:
            STATS[e.path] = e.stat()
            yield e.path


WALKED = {}  # directory -> its file paths, so a folder is listed once per run


class Layers:
    def __init__(self, roots):
        self.roots = roots

    def files(self, rel_dir, ext=('.txt',)):
        """Merged {filename: path}, later roots overriding earlier ones."""
        out = {}
        for r in self.roots:
            d = os.path.join(r, rel_dir)
            if d not in WALKED:
                WALKED[d] = list(walk(d)) if os.path.isdir(d) else []
            n = len(d) + 1  # walk() paths are d + separator + relative path
            for p in WALKED[d]:
                if p.lower().endswith(ext):
                    out[p[n:]] = p
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
        for k, v in parse_file(p):
            if isinstance(v, str) and TAG_RE.fullmatch(k):
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
            c = get(parse_file(p), 'color')
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
        for k, v in history_at(parse_file(p)):
            if k == 'add_government_reform':
                st['reforms'].add(v)
            elif k == 'monarch' and isinstance(v, list):
                st['dynasty'] = get(v, 'dynasty', st.get('dynasty'))
                st['monarch_name'], st['monarch_female'] = get(v, 'name'), get(v, 'female') == 'yes'
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
        for k, v in history_at(parse_file(p)):
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
    """{culture: group}, [group, ...] and {group: [culture, ...]}, all in file order."""
    groups, order, members = {}, [], {}
    for _, p in L.files('common/cultures').items():
        for g, body in parse_file(p):
            if not isinstance(body, list):
                continue
            if g not in order:
                order.append(g)
            for k, v in body:
                if isinstance(v, list) and k not in NAME_LISTS:
                    if groups.get(k) != g:
                        members.setdefault(g, []).append(k)
                    groups[k] = g
    for g, cs in members.items():  # a culture redefined in a later file belongs to its last group only
        members[g] = [c for c in dict.fromkeys(cs) if groups[c] == g]
    return groups, order, members


def load_religions(L):
    """{religion: {group, color, icon}} in file order."""
    out = {}
    for _, p in L.files('common/religions').items():
        for g, body in parse_file(p):
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
        for m in re.finditer(r'name\s*=\s*"?([^"\s{}]+)"?\s*texturefile\s*=\s*"([^"]+)"', read_text(p)):
            out[m.group(1)] = m.group(2).replace('//', '/')
    return out


def load_geography(L):
    """{province: {'area', 'region', 'superregion', 'continent'}} from map/*.txt."""
    def members(items):
        return [k for k, v in items if v is None]

    def blocks(rel):
        p = L.file(rel)
        return [(k, v) for k, v in parse_file(p) if isinstance(v, list)] if p else []

    region_of = {}
    for r, body in blocks('map/region.txt'):
        for a in members(get(body, 'areas') or []):
            region_of[a] = r
    super_of = {r: s for s, body in blocks('map/superregion.txt') for r in members(body)}
    geo = {}
    for a, body in blocks('map/area.txt'):
        for pid in members(body):
            if pid.isdigit():
                r = region_of.get(a)
                geo[int(pid)] = {'area': a, 'region': r, 'superregion': super_of.get(r)}
    for c, body in blocks('map/continent.txt'):
        for pid in members(body):
            if pid.isdigit():
                geo.setdefault(int(pid), {})['continent'] = c
    return geo


def load_defined_texts(L):
    """{name: [(localisation key, trigger items), ...]} of every defined_text in customizable_localization/
    (the first definition of a name wins)."""
    out = {}
    for _, p in L.files('customizable_localization').items():
        for k, body in parse_file(p):
            if k == 'defined_text' and isinstance(body, list):
                name = get(body, 'name')
                if name and name not in out:
                    out[name] = [(get(t, 'localisation_key'), get(t, 'trigger') or [])
                                 for k2, t in body if k2 == 'text' and isinstance(t, list)]
    return out


# [Root.GovernmentName] really comes from scripted government names; this approximates it by type and rank.
GOV_NAMES = {'monarchy': {'1': 'Duchy', '2': 'Kingdom', '3': 'Empire'}, 'republic': 'Republic',
             'theocracy': 'Theocracy', 'tribal': 'Tribe', 'native': 'Tribe'}
MONARCH_TITLES = {'1': 'Duke', '2': 'King', '3': 'Emperor'}
PRONOUNS = {'getshehe': ('she', 'he'), 'getherhim': ('her', 'him'), 'getherhis': ('her', 'his'),
            'gethershis': ('hers', 'his'), 'getherselfhimself': ('herself', 'himself'),
            'getwomanman': ('woman', 'man'), 'getqueenking': ('queen', 'king')}


def resolve_loc(s, ctx, name, adj, g, depth=0):
    """Fill the [..] placeholders of a localisation text with the country's 1444 values: defined_texts
    (first text whose trigger is True, else the first one not False that isn't empty), country, capital,
    province and monarch values. ctx gives the country's state; name and adj its displayed name and adjective
    (a formed country's for formable trees). Unknown placeholders become empty, and clean_loc later strips colour codes."""
    st, loc = ctx.st, g['loc']
    female = st.get('monarch_female')
    prov = lambda pid: g['prov_names'].get('PROV' + str(pid), '')

    def value(expr):
        parts = expr.strip().split('.')
        if len(parts) > 1 and parts[0].lower() in ('root', 'this'):
            parts = parts[1:]
        if len(parts) > 1 and TAG_RE.fullmatch(parts[0]) and parts[0] in g['hist'] and depth < 4:  # [YUA.GetName]
            t = parts[0]
            n = g['names'].get(t) or clean_loc(loc.get(t)) or t
            c = Ctx(t, g['hist'][t], ctx.cg, ctx.rel, ctx.geo)
            return resolve_loc('[' + '.'.join(parts[1:]) + ']', c, n, clean_loc(loc.get(t + '_ADJ')) or n, g, depth + 1)
        if len(parts) == 1 and parts[0] in g['defined_texts']:
            return defined(parts[0])
        low = '.'.join(parts).lower()
        if low.endswith('cap'):
            v = value('.'.join(parts)[:-3])
            return v[:1].upper() + v[1:]
        gov = GOV_NAMES.get(st.get('government'), 'Realm')
        simple = {
            'getyear': lambda: str(START_DATE[0]), 'getname': lambda: name, 'getusablename': lambda: name,
            'getadjective': lambda: adj or name, 'gettag': lambda: ctx.tag,
            'getcapitalname': lambda: prov(st.get('capital')),
            'governmentname': lambda: gov.get(st.get('government_rank'), 'Kingdom') if isinstance(gov, dict) else gov,
            'monarch.dynasty.getname': lambda: st.get('dynasty') or name,
            'monarch.getname': lambda: st.get('monarch_name') or '',
            'monarch.gettitle': lambda: (MONARCH_TITLES.get(st.get('government_rank'), 'King')
                                         if st.get('government') == 'monarchy' else 'Ruler'),
            'religion.getname': lambda: clean_loc(loc.get(st.get('religion') or '')),
            'capital.getname': lambda: prov(st.get('capital')),
            'capital.getcapitalname': lambda: prov(st.get('capital')),
        }
        if low in simple:
            return simple[low]()
        if low.startswith('monarch.') and low[8:] in PRONOUNS:
            return PRONOUNS[low[8:]][0 if female else 1]
        m = re.fullmatch(r'(\d+)\.getname', low)
        return prov(m.group(1)) if m else ''

    def defined(dt):
        if depth >= 4:
            return ''
        texts = [(k, ev_block(trig, ctx)) for k, trig in g['defined_texts'][dt]]
        true = next((k for k, v in texts if v is True), None)
        if true is not None:
            return resolve_loc(loc.get(true), ctx, name, adj, g, depth + 1)
        for k, v in texts:
            if v is not False:
                out = resolve_loc(loc.get(k), ctx, name, adj, g, depth + 1)
                if clean_loc(out):
                    return out
        return ''

    return re.sub(r'\[([^\]]+)\]', lambda m: value(m.group(1)), s or '')


# ---------------------------------------------------------------- missions

class Ctx:
    """A country's 1444 state for ev(). was: the tags it counts as for was_tag (a formed country adds its
    old tag); tags: the tags it counts as for tag = (a '>TAG' chain counts as all of them at once);
    switch: {(has_country_flag | has_country_modifier, name): bool} assumed by branch_packs() or a txt line;
    strict: for a chain, the same country as only its final tag (decides which of two colliding series stays)."""
    def __init__(self, tag, st, cgroups, religions, geo=None, was=None, switch=None, tags=None):
        self.tag, self.st, self.cg, self.rel, self.geo = tag, st, cgroups, religions, geo or {}
        self.was, self.switch, self.tags, self.strict = was or {tag}, switch or {}, tags or {tag}, None

    def copy(self, **changes):
        c = Ctx(self.tag, self.st, self.cg, self.rel, self.geo, self.was, self.switch, self.tags)
        c.strict = self.strict
        for k, v in changes.items():
            setattr(c, k, v)
        return c


U = None  # unknown in three-valued logic
IDENTITY_KEYS = {'tag', 'was_tag', 'primary_culture', 'culture_group', 'religion', 'religion_group',
                 'technology_group', 'government', 'has_reform', 'capital'}
GEO_KEYS = {'area', 'region', 'superregion', 'continent'}


def tv_and(vals):
    all_true = True
    for v in vals:
        if v is False:
            return False
        all_true = all_true and v is True
    return True if all_true else U


def tv_or(vals):
    seen = unknown = False
    for v in vals:
        if v is True:
            return True
        seen = True
        unknown = unknown or v is not False
    return U if unknown or not seen else False


def ev_block(items, c):
    return tv_and(ev(k, v, c) for k, v in items)


def ev_province(items, pid, geo):
    """Evaluate a province-scope block (capital_scope) against geography. Unknown triggers give U."""
    def one(k, v):
        kl = k.lower()
        if isinstance(v, list):
            if kl == 'and':
                return ev_province(v, pid, geo)
            if kl in ('or', 'not'):
                r = tv_or(one(a, b) for a, b in v)
                return r if kl == 'or' or r is U else not r
            return U
        if kl in GEO_KEYS:
            return geo.get(pid, {}).get(kl) == v
        if kl == 'province_id' and v.isdigit():
            return pid == int(v)
        return U
    return tv_and(one(k, v) for k, v in items)


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
        if kl == 'capital_scope':
            cap = c.st.get('capital')
            return ev_province(v, int(cap), c.geo) if cap and cap.isdigit() and c.geo else U
        return U
    if kl == 'always':
        return {'yes': True, 'no': False}.get(v, U)
    if kl == 'is_random_new_world':  # historical map only
        return {'yes': False, 'no': True}.get(v, U)
    st = c.st
    if kl == 'tag':
        return v in c.tags if TAG_RE.fullmatch(v) else U
    if kl == 'was_tag':
        return v in c.was if TAG_RE.fullmatch(v) else U
    if kl in SWITCH_KEYS or kl in ASSUMABLE:
        return c.switch.get((kl, v), U)
    if kl == 'primary_culture':
        return st.get('primary_culture') == v
    if kl == 'culture_group':
        return U if TAG_RE.fullmatch(v) else c.cg.get(st.get('primary_culture')) == v
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


SWITCH_KEYS = ('has_country_flag', 'has_country_modifier')
ASSUMABLE = ('has_government_attribute',)  # also set by '+name' in a txt line, but never a path choice


def switch_vars(items):
    """{(key, name)} of the country flags and modifiers a trigger block checks, at any depth."""
    out = set()
    for k, v in items:
        if isinstance(v, list):
            out |= switch_vars(v)
        elif k.lower() in SWITCH_KEYS and isinstance(v, str):
            out.add((k.lower(), v))
    return out


def with_switch(ctx, assume):
    """ctx with the flags of an ab.txt / actum.txt line assumed ({name: set?}, for flags and modifiers)."""
    if not assume:
        return ctx
    sw = {(k, n): v for n, v in assume.items() for k in SWITCH_KEYS + ASSUMABLE}
    c = ctx.copy(switch={**ctx.switch, **sw})
    if c.strict:
        c.strict = c.strict.copy(switch=c.switch)
    return c


def with_chain(ctx, chain):
    """ctx for a '>TAG >TAG' line: the country counts as its own tag and every formed one at once (so its
    base, formable and final trees all show), plus a strict copy as only the final tag."""
    if not chain:
        return ctx
    every = {ctx.tag, *chain}
    c = ctx.copy(was=set(ctx.was) | every, tags=every)
    c.strict = ctx.copy(tag=chain[-1], was=set(ctx.was) | every, tags={chain[-1]})
    return c


SERIES_VARS = {}  # id(series) -> (series, its switch_vars), computed once per run


def series_switch_vars(s):
    hit = SERIES_VARS.get(id(s))
    if hit is None or hit[0] is not s:
        hit = SERIES_VARS[id(s)] = (s, switch_vars(s['potential']))
    return hit[1]


def pretty_switch(names, loc):
    """Button labels for switch variables: their localisation if any, else the names without the prefix and
    suffix they all share (fra_monarchy_focus, fra_communist_focus -> Monarchy, Communist)."""
    parts = [n.split('_') for n in names]
    pre = suf = 0
    if len(parts) > 1:
        while all(len(p) > pre + suf + 1 for p in parts) and len({p[pre] for p in parts}) == 1:
            pre += 1
        while all(len(p) > pre + suf + 1 for p in parts) and len({p[-1 - suf] for p in parts}) == 1:
            suf += 1
    return {n: clean_loc(loc.get(n)) or ' '.join(w.capitalize() for w in p[pre:len(p) - suf])
            for n, p in zip(names, parts)}


def branch_packs(series, ctx, loc, max_vars=10):
    """Branching paths of a tree: series whose potentials check the same country flags/modifiers form a
    pack, and choosing a path (setting a flag) swaps the whole pack at once. Every flag assignment is tried;
    the distinct sets of visible series (none sharing a slot position) are the options, named after the
    smallest assignment giving each. Returns (packs, hidden): packs = [[(label, [series positions]), ...]]
    with the default (fewest flags set, labelled Default) first; hidden = series positions no option shows."""
    vars_of = [series_switch_vars(s) - ctx.switch.keys() for s in series]  # assumed flags aren't choices
    if not any(vars_of):  # most trees: no flag-switched paths
        return [], set()
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x
    for vs in vars_of:
        vs = sorted(vs)
        for a in vs[1:]:
            parent[find(a)] = find(vs[0])
        if vs:
            find(vs[0])
    # series that sit on the same slot positions are alternatives, even when their flags never meet in one
    # potential (TLW_Rome: one flag per tree): their flags form one choice
    cells = [{(s['slot'], m['pos']) for m in s['missions']} for s in series]
    for i in range(len(series)):
        for j in range(i + 1, len(series)):
            if vars_of[i] and vars_of[j] and cells[i] & cells[j]:
                parent[find(min(vars_of[j]))] = find(min(vars_of[i]))
    comps = {}
    for v in parent:
        comps.setdefault(find(v), []).append(v)
    packs, hidden = [], set()
    for comp in comps.values():
        comp = sorted(comp)
        members = [i for i, vs in enumerate(vars_of) if vs & set(comp)]
        if len(comp) > max_vars:
            continue
        options = {}  # frozenset(visible) -> the smallest assignments (lists of True variables) giving it
        for bits in range(1 << len(comp)):
            on = [v for j, v in enumerate(comp) if bits >> j & 1]
            c = ctx.copy(switch={**ctx.switch, **{v: v in on for v in comp}})
            vis = [i for i in members if ev_block(series[i]['potential'], c) is not False]
            cells = [c for i in vis for c in {(series[i]['slot'], m['pos']) for m in series[i]['missions']}]
            if not vis or len(cells) != len(set(cells)):  # two visible series on one slot position
                continue
            key = frozenset(vis)
            if key not in options or len(on) < len(options[key][0]):
                options[key] = [on]
            elif len(on) == len(options[key][0]):
                options[key].append(on)
        # two choices at once whose series are just both choices together (Persian + Chinese) aren't a choice
        def combined(k, ons):
            a = set(ons[0])
            parts = [o for o, ov in options.items() if o != k and set(ov[0]) < a]
            return len(a) > 1 and bool(parts) and frozenset().union(*parts) == k
        options = {k: v for k, v in options.items() if not combined(k, v)}
        # a set that is part of another one comes from flags that hide one path without unlocking another
        # (two religions picked at once): not a real choice
        options = {k: v for k, v in options.items() if not any(k < o for o in options)}
        if not options:
            continue
        shown = set().union(*options)
        hidden |= set(members) - shown
        if len(options) < 2:
            continue
        always = frozenset.intersection(*options)  # in every option: not part of the choice
        options = {k - always: v for k, v in options.items()}
        opts = sorted(options.items(), key=lambda o: (len(o[1][0]), min(o[0], default=-1)))
        rest = [set(on) for _, ons in opts[1:] for on in ons]
        common = set.intersection(*rest) if len(rest) > 1 else set()  # set by every choice: not a label
        names = pretty_switch(sorted({v[1] for _, ons in opts for on in ons for v in on if v not in common}), loc)
        pack = []
        for vis, ons in opts:
            alts = [' + '.join(names[v[1]] for v in on if v not in common) for on in ons]
            pack.append((' / '.join(a for a in alts if a) or 'Default', sorted(vis)))
        packs.append(pack)
    return packs, hidden


def identity_leaves(items):
    """{(key, value)} of the identity conditions outside a NOT, for matching against identity_facts()."""
    out = set()
    for k, v in items:
        kl = k.lower()
        if isinstance(v, list):
            if kl in ('and', 'or'):
                out |= identity_leaves(v)
        elif kl in IDENTITY_KEYS and v is not None:
            out.add((kl, v))
    return out


def identity_facts(c):
    """{(key, value)} that are True for this country: the identity conditions ev() would answer True."""
    st = c.st
    facts = {('tag', c.tag), ('was_tag', c.tag)}
    facts |= {(k, st[k]) for k in ('primary_culture', 'religion', 'technology_group', 'government', 'capital')
              if st.get(k)}
    facts |= {('has_reform', r) for r in st.get('reforms', ())}
    group = c.cg.get(st.get('primary_culture'))
    rgroup = c.rel.get(st.get('religion'), {}).get('group')
    if group:
        facts.add(('culture_group', group))
    if rgroup:
        facts.add(('religion_group', rgroup))
    return facts


def load_series(L):
    series = []
    for fn, p in L.files('missions').items():
        for name, body in parse_file(p):
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
                           'potential': pot, 'ids': identity_leaves(pot), 'missions': missions})
    return series


def series_for(series, ctx, facts):
    """Indexes of the series a country gets at start: one of its identity conditions holds
    (facts = identity_facts(ctx)) and the whole potential isn't False."""
    return [i for i, s in enumerate(series)
            if s['ids'] & facts and ev_block(s['potential'], ctx) is not False]


# ---------------------------------------------------------------- images

def province_image(L, scale):
    """{'webp': base64 lossless WebP with R = id >> 8, G = id & 255, 'w', 'h', 'on_map': sorted ids drawn,
    'bbox': {id: [x0, y0, x1, y1]}, 'names'}. Cached, keyed by provinces.bmp, definition.csv and the scale."""
    bmp, csv_path = L.file('map/provinces.bmp'), L.file('map/definition.csv')

    def make():
        ids, names = build_province_image(bmp, csv_path, scale)
        h, w = ids.shape
        flat, n = ids.ravel(), int(ids.max()) + 1
        xs, ys = np.tile(np.arange(w), h), np.repeat(np.arange(h), w)
        box = [np.full(n, w), np.full(n, h), np.full(n, -1), np.full(n, -1)]
        for b, f, v in zip(box, (np.minimum, np.minimum, np.maximum, np.maximum), (xs, ys, xs, ys)):
            f.at(b, flat, v)
        on_map = np.unique(flat).tolist()
        return {'webp': webp_b64(ids), 'w': w, 'h': h, 'on_map': on_map, 'names': names,
                'bbox': {p: [int(b[p]) for b in box] for p in on_map}}
    return IMAGES.get(Cache.key(bmp, scale, 'webp') + Cache.key(csv_path), make)


def build_province_image(bmp_path, csv_path, scale):
    bmp = Image.open(bmp_path).convert('RGB')
    W, H = bmp.size
    w, h = W // scale, H // scale
    arr = np.asarray(bmp)[::scale, ::scale][:h, :w].astype(np.uint32)
    key = (arr[..., 0] << 16) | (arr[..., 1] << 8) | arr[..., 2]
    lut, names = {}, {}
    with open(csv_path, encoding='cp1252', errors='replace') as f:
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


def webp_b64(ids):
    rgb = np.zeros(ids.shape + (3,), np.uint8)
    rgb[..., 0] = ids >> 8
    rgb[..., 1] = ids & 255
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, 'WEBP', lossless=True, method=4)  # exact pixels, ~40 % of the PNG
    return base64.b64encode(buf.getvalue()).decode()


IMAGES = None  # Cache of resized images as PNG bytes, set up in main()


def png_bytes(im):
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return buf.getvalue()


def from_png(data):
    return Image.open(io.BytesIO(data)) if data else None


def open_image(path, size=None, square=False):
    """RGBA image; square crops a sprite strip to its first frame; size resizes. Cached when size is given."""
    def make():
        try:
            im = Image.open(path)
            im.load()
            im = im.convert('RGBA')
        except Exception:
            return None
        if square and im.size[0] > im.size[1] * 1.5:
            im = im.crop((0, 0, im.size[1], im.size[1]))
        return im.resize((size, size), Image.LANCZOS) if size else im

    def make_png():
        im = make()
        return png_bytes(im) if im else None

    return from_png(IMAGES.get(Cache.key(path, size, square), make_png)) if size else make()


def image_entry(path, size, square=False):
    """An atlas entry for open_image(path, size, square)."""
    return Cache.key(path, size, square), lambda: open_image(path, size, square)


def music_data(path):
    """{'type', 'b64'} of the music, re-encoded to Opus at MUSIC_BITRATE when ffmpeg is available. Cached."""
    if MUSIC_BITRATE and shutil.which('ffmpeg'):
        def encode():
            cmd = ['ffmpeg', '-v', 'error', '-i', path, '-vn', '-c:a', 'libopus', '-b:a', MUSIC_BITRATE,
                   '-vbr', 'on', '-f', 'ogg', 'pipe:1']
            return subprocess.run(cmd, capture_output=True, check=True).stdout
        data, kind = IMAGES.get(Cache.key(path, 'opus', MUSIC_BITRATE), encode), 'audio/ogg; codecs=opus'
    else:
        if MUSIC_BITRATE:
            print('  music: ffmpeg not found, embedding it without re-encoding', file=sys.stderr)
        data, kind = open(path, 'rb').read(), 'audio/ogg'
    return {'type': kind, 'b64': base64.b64encode(data).decode()}


def atlas(entries, size, cols=32):
    """Pack {key: (cache key, load)} into one sheet, where load() returns the image or None.
    Returns {'uri': data uri, 'idx': {key: index}, 'cols'}. Cached by the entries' cache keys, so an
    unchanged sheet doesn't open its images at all."""
    def make():
        images = {k: load() for k, (_, load) in entries.items()}
        keys = [k for k, im in images.items() if im is not None]
        if not keys:
            return {'uri': None, 'idx': {}, 'cols': cols}
        sheet = Image.new('RGBA', (cols * size, (len(keys) + cols - 1) // cols * size), (0, 0, 0, 0))
        for i, k in enumerate(keys):
            im = images[k]
            sheet.paste(im if im.size == (size, size) else im.resize((size, size), Image.LANCZOS),
                        ((i % cols) * size, (i // cols) * size))
        buf = io.BytesIO()
        sheet.save(buf, ATLAS_FORMAT, quality=ATLAS_QUALITY)
        uri = f'data:image/{ATLAS_FORMAT.lower()};base64,' + base64.b64encode(buf.getvalue()).decode()
        return {'uri': uri, 'idx': {k: i for i, k in enumerate(keys)}, 'cols': cols}
    key = ('atlas', ATLAS_FORMAT, ATLAS_QUALITY, size, cols, tuple((k, ck) for k, (ck, _) in entries.items()))
    return IMAGES.get(key, make)


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


LISTED = {}  # folder (lower case) -> {file name (lower case): name}, one listing per folder


def texture(roots, rel):
    """Find a texture referenced from a .gfx file, case-insensitively on the extension."""
    for r in reversed(roots):
        d, name = os.path.split(os.path.join(r, rel))  # same path strings as before (they are cache keys)
        key = os.path.normpath(d).lower()
        if key not in LISTED:
            try:
                LISTED[key] = {e.name.lower(): e.name for e in os.scandir(d) if e.is_file()}
            except OSError:
                LISTED[key] = {}
        names = LISTED[key]
        for cand in (name, os.path.splitext(name)[0] + '.dds', os.path.splitext(name)[0] + '.tga'):
            if cand.lower() in names:
                return os.path.join(d, cand)


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


def read_tag_map(path):
    """{TAG: [(label, [mission files] or None, {flag: bool}), ...]}: one tag per line, optionally
    'TAG = file file ...'. Switchable trees are separated by '|', each 'Label: files' (the first is the
    original; its label is optional and empty files mean auto-detect). '+name' / '-name' among the files
    assume that country flag or modifier set / unset for the tree; '>TAG >TAG' shows the tree as if the
    country had formed those tags in turn (all its stages unlocked at once); 'none' = no tree. A label
    'TAG' shows as "Form <TAG's name>", 'TAG "Name"' as "Form Name". Missing file = empty."""
    out = {}
    if not os.path.isfile(path):
        return out
    for line in open(path, encoding='utf-8-sig'):
        tag, eq, rest = line.split('#')[0].partition('=')
        tag = tag.strip().upper()
        if not tag:
            continue
        variants = []
        for part in rest.split('|') if eq else ['']:
            label, colon, files = part.partition(':')
            if not colon:
                label, files = '', part
            words = files.split()
            assume = {w[1:]: w[0] == '+' for w in words if w[0] in '+-' and len(w) > 1}
            chain = [w[1:].upper() for w in words if w[0] == '>' and len(w) > 1]
            names = [w for w in words if w[0] not in '+->']
            files = [] if names == ['none'] else names or None  # "none": no tree for this variant
            variants.append((label.strip(), files, assume, chain))
        out[tag] = variants
    return out


def hsl(h, s, l):
    return [int(x * 255) for x in colorsys.hls_to_rgb(h % 1, l, s)]


def group_base_color(group):
    """Generated base colour of a culture group, used when culture.txt doesn't list it."""
    return hsl(name_hash(group) % 360 / 360, 0.57, 0.52)


def culture_color(base, culture):
    """A culture's colour: a small, stable variation of its group's base colour."""
    h, l, s = colorsys.rgb_to_hls(*(x / 255 for x in base))
    hc = name_hash(culture)
    clamp = lambda x: min(0.95, max(0.05, x))
    return hsl(h + ((hc % 21) - 10) / 360, clamp(s + ((hc >> 8) % 25 - 12) / 100), clamp(l + ((hc >> 16) % 22 - 11) / 100))


def read_culture_colors(path):
    """{group: [r, g, b]} from lines like 'iranian = 196 92 140'; '#' starts a comment."""
    out = {}
    for line in open(path, encoding='utf-8-sig'):
        m = re.match(r'\s*([^\s=#]+)\s*=\s*(\d+)\s+(\d+)\s+(\d+)', line.split('#')[0])
        if m:
            out[m.group(1)] = [min(255, int(x)) for x in m.groups()[1:]]
    return out


def write_culture_colors(path, groups, names):
    lines = ['# Base colour of each culture group in the Culture map mode, as R G B (0-255).',
             '# Every culture gets a small variation of its group\'s colour. Groups not listed get a generated colour.',
             '# This file was generated with the current colours; edit it freely, the build never overwrites it.', '']
    for g in groups:
        entry = '{} = {} {} {}'.format(g, *group_base_color(g))
        lines.append(f'{entry:<44}# {names.get(g, g)}')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def name_hash(s):
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


GAME_DIRS = [('localisation', ('.yml',)), ('common/country_tags',), ('common/countries',), ('history/countries',),
             ('history/provinces',), ('common/cultures',), ('common/religions',), ('customizable_localization',),
             ('missions',), ('interface', ('.gfx',))]
GAME_MAP_FILES = ['map/default.map', 'map/climate.txt', 'map/area.txt', 'map/region.txt', 'map/superregion.txt',
                  'map/continent.txt']


def game_texts(g):
    """Display texts that depend only on the game files, so they are cached with them: country names and
    lore (the lore choice evaluates every lore trigger for every country), mission titles and
    descriptions, province names."""
    loc, lore = g['loc'], {}
    owners = sorted({st['owner'] for st in g['prov'].values() if st['owner']})
    names = {t: clean_loc(loc.get(t)) or t for t in owners}
    prov_names = {k: clean_loc(v) for k, v in loc.items() if k.startswith('PROV') and k[4:].isdigit()}
    g = {**g, 'names': names, 'prov_names': prov_names}  # what resolve_loc reads
    lore_texts = g['defined_texts'].get(LORE_DEFINED_TEXT, [])
    for t in owners:
        st = g['hist'].get(t, {})
        ctx = Ctx(t, st, g['cgroups'], g['religions'], g['geo'])
        key = next((k for k, trig in lore_texts if ev_block(trig, ctx) is True), None)
        lore[t] = clean_loc(resolve_loc(loc.get(key), ctx, names[t], clean_loc(loc.get(t + '_ADJ')) or names[t], g))
    missions = {m['id'] for s in g['series_aba'] + g['series_ab'] for m in s['missions']}
    raw = {i: (loc.get(i + '_title'), loc.get(i + '_desc')) for i in missions}
    return {'names': names, 'lore': lore, 'prov_names': prov_names,
            'mission_texts': {i: (clean_loc(t) or i, clean_loc(d)) for i, (t, d) in raw.items()},
            # texts with [..] placeholders are filled per country when the page data is built
            'mission_raw': {i: td for i, td in raw.items() if '[' in (td[0] or '') + (td[1] or '')}}


def slim_texts(g):
    """Keep only the localisation and defined_texts that the page build reads after loading the cache:
    every build unpickles game.pkl, and the full English localisation was most of it (lore, names and
    the plain mission texts are already resolved by game_texts)."""
    loc, dts = g['loc'], g['defined_texts']
    keys = {k for k in loc if TAG_RE.fullmatch(k) or (k.endswith('_ADJ') and TAG_RE.fullmatch(k[:-4]))}
    keys |= set(g['cgroups']) | set(g['cgroups'].values()) | set(g['corder'])
    keys |= {x for r, info in g['religions'].items() for x in (r, info['group'], r + '_religion_desc')}
    keys |= {v for sr in g['series_aba'] + g['series_ab'] for _, v in switch_vars(sr['potential'])}
    # what resolve_loc can reach from the per-country mission texts, through defined_texts
    used_dt, todo, seen = set(), [t for td in g['mission_raw'].values() for t in td if t], set()
    while todo:
        text = todo.pop()
        for expr in re.findall(r'\[([^\]]+)\]', text):
            for part in expr.strip().split('.'):
                if part in dts and part not in used_dt:
                    used_dt.add(part)
                    for k, _ in dts[part]:
                        if k and k not in seen:
                            seen.add(k)
                            keys.add(k)
                            if loc.get(k):
                                todo.append(loc[k])
    g['loc'] = {k: loc[k] for k in keys if k in loc}
    g['defined_texts'] = {n: dts[n] for n in used_dt}


def load_game(L, Lab):
    """Everything read from the game files, cached as one object keyed by every input file (and this
    script), so a build with unchanged game files skips parsing and processing them."""
    paths = {p for d, *ext in GAME_DIRS for p in L.files(d, *ext).values()} | set(Lab.files('missions').values())
    paths |= {p for p in map(L.file, GAME_MAP_FILES) if p}
    key = ('game', Cache.key(os.path.abspath(__file__))) + tuple(Cache.key(p) for p in sorted(paths))

    def make():
        global PARSED
        PARSED = Cache('parse.pkl')
        loc = load_localisation(L)
        cgroups, corder, cmembers = load_cultures(L)
        dm = parse_file(L.file('map/default.map'))
        game = {
            'loc': loc, 'colors': load_tag_colors(L, loc), 'hist': load_country_history(L),
            'prov': load_province_history(L), 'cgroups': cgroups, 'corder': corder, 'cmembers': cmembers,
            'religions': load_religions(L), 'geo': load_geography(L),
            'defined_texts': load_defined_texts(L),
            'water': load_ids_list(dm, 'sea_starts') | load_ids_list(dm, 'lakes'),
            'wasteland': load_ids_list(parse_file(L.file('map/climate.txt')), 'impassable'),
            'series_aba': load_series(L), 'series_ab': load_series(Lab), 'sprites': load_sprites(L),
        }
        game.update(game_texts(game))
        slim_texts(game)
        PARSED.save()
        return game
    cache = Cache('game.pkl')
    game = cache.get(key, make)
    cache.save()
    return game


def main():
    global IMAGES
    IMAGES = Cache('images.pkl')
    roots = [here(r) for r in ROOTS]
    mod = here(MOD_ROOT)
    for r in roots:
        if not os.path.isdir(r):
            sys.exit(f'Game folder not found: {r}\nSet ROOTS at the top of {os.path.basename(__file__)}.')
    full_roots = roots + ([mod] if os.path.isdir(mod) else [])
    L, Lab = Layers(full_roots), Layers(roots)

    actum_map = read_tag_map(here(ACTUM_FILE))
    additional = [t for t in read_tags(here(ADDITIONAL_FILE)) if t not in actum_map]
    favorites = read_tags(here(FAVORITES_FILE))
    actum_favorites = read_tags(here(ACTUM_FAVORITES_FILE))
    ab_map = read_tag_map(here(AB_FILE))

    print('reading game files...', file=sys.stderr)
    game = load_game(L, Lab)
    loc, colors, hist, prov = game['loc'], game['colors'], game['hist'], game['prov']
    cgroups, corder, cmembers = game['cgroups'], game['corder'], game['cmembers']
    religions, geo = game['religions'], game['geo']
    water, wasteland, sprites = game['water'], game['wasteland'], game['sprites']
    series_ab = [s for s in game['series_ab'] if fnmatch.fnmatch(s['file'].lower(), AB_MISSION_FILES.lower())]
    mod_missions = {os.path.basename(p).lower() for p in Layers([mod]).files('missions').values()}
    series_aba = [s for s in game['series_aba'] if s['file'].lower() in mod_missions
                  or fnmatch.fnmatch(s['file'].lower(), AB_MISSION_FILES.lower())]
    def by_file(series):
        out = {}
        for i, s in enumerate(series):
            out.setdefault(s['file'].lower(), []).append(i)
        return out
    ab_by_file, aba_by_file = by_file(series_ab), by_file(series_aba)

    owners = {pid: st['owner'] for pid, st in prov.items()}
    alive = sorted({o for o in owners.values() if o})
    provs, dev = {}, {}
    for st in prov.values():
        if st['owner']:
            provs[st['owner']] = provs.get(st['owner'], 0) + 1
            dev[st['owner']] = dev.get(st['owner'], 0) + st['dev']

    # mission series: keep only those some tag gets, deduplicated across the two layers
    out_series, sid, texts, raw = [], {}, game['mission_texts'], game['mission_raw']
    skeys = {}  # id(series) -> its dedup key; the series objects live for the whole run

    def text(i, lc):
        """(title, desc) of a mission for a country; lc = (ctx, name, adjective)."""
        if i not in raw:
            return texts[i]
        t, d = raw[i]
        return clean_loc(resolve_loc(t, *lc, game)) or i, clean_loc(resolve_loc(d, *lc, game))

    def keep(s, lc, mt):
        """Index of the series in the page data. Its texts are filled for the first country that uses it;
        other countries whose filled texts differ get theirs in mt ({mission id: [title, desc]})."""
        key = skeys.get(id(s))
        if key is None:  # computed once per series (the json.dumps is the costly part)
            key = skeys[id(s)] = (s['file'], s['name'], json.dumps(s['missions']))
        if key not in sid:
            sid[key] = len(out_series)
            out_series.append({
                'name': s['name'], 'file': s['file'], 'slot': s['slot'],
                'missions': [{**m, 'title': tx[0], 'desc': tx[1]} for m in s['missions'] for tx in [text(m['id'], lc)]],
            })
        else:
            for m in out_series[sid[key]]['missions']:
                if m['id'] in raw:
                    tx = text(m['id'], lc)
                    if tx != (m['title'], m['desc']):
                        mt[m['id']] = list(tx)
        return sid[key]

    def tree(src, idx, ctx, lc, mt):
        """(series indexes in the page data, branching packs) of a country's tree. Series no branch
        choice shows are left out; packs = [[label, [series indexes]], ...], default option first."""
        if ctx.strict:  # a '>TAG' chain: only the series the country gets as all its stages at once
            idx = [i for i in idx if ev_block(src[i]['potential'], ctx) is not False]
        elif ctx.switch:  # '+flag' / '-flag' only: drop the series the flags rule out
            plain = Ctx(ctx.tag, ctx.st, ctx.cg, ctx.rel, ctx.geo)
            idx = [i for i in idx if ev_block(src[i]['potential'], ctx) is not False
                   or ev_block(src[i]['potential'], plain) is False]
        if ctx.strict:  # of two series on the same slot positions, keep the one the final tag gets
            ok = {i: ev_block(src[i]['potential'], ctx.strict) is not False for i in idx}
            cells = {i: {(src[i]['slot'], m['pos']) for m in src[i]['missions']} for i in idx}
            idx = [i for i in idx if ok[i] or not any(ok[j] and cells[i] & cells[j] for j in idx if j != i)]
        packs, hidden = branch_packs([src[i] for i in idx], ctx, loc)
        out = {k: keep(src[i], lc, mt) for k, i in enumerate(idx) if k not in hidden}
        return list(out.values()), [[[lb, [out[k] for k in ks]] for lb, ks in p] for p in packs]

    # Trees only for the tags in ab.txt / actum.txt: their mapped files, or else the series they match.
    # Both kinds go through the same code; the page data gets {prefix}, {prefix}Forms, {prefix}Br, {prefix}Mt
    # (prefix 'ab' for Ante Bellum, 'act' for Actum; the AB ones keep their older names br / mt).
    alive_set = set(alive)
    ab_tree = [t for t in ab_map if t in alive_set]
    actum_tree = [t for t in actum_map if t in alive_set]
    bad_files = {name: sorted({f for vs in m.values() for _, fs, *_ in vs if fs for f in fs if f.lower() not in bf})
                 for name, m, bf in (('ab.txt', ab_map, ab_by_file), ('actum.txt', actum_map, aba_by_file))}
    form_label = lambda lb: re.fullmatch(r'([A-Za-z0-9]{3})(?:\s+"([^"]+)")?', lb.strip())  # TAG or TAG "Name"
    form_tags = {m.group(1).upper() for mp in (ab_map, actum_map) for vs in mp.values() for lb, *_ in vs
                 for m in [form_label(lb)] if m and m.group(1).upper() in colors}
    no_tree = {'ab.txt': [], 'actum.txt': []}
    KINDS = (('ab', 'ab.txt', ab_map, series_ab, ab_by_file, 'br', 'mt'),
             ('act', 'actum.txt', actum_map, series_aba, aba_by_file, 'actBr', 'actMt'))

    lore_list, lore_idx = [], {}  # distinct lore texts; many tags share a regional one
    tags = {}
    for t in alive:
        st = hist.get(t, {})
        ctx = Ctx(t, st, cgroups, religions, geo)
        facts = identity_facts(ctx)
        name, lore = game['names'][t], game['lore'][t]
        if lore not in lore_idx:
            lore_idx[lore] = len(lore_list)
            lore_list.append(lore)
        lc = (ctx, name, clean_loc(loc.get(t + '_ADJ')) or name)
        x = tags[t] = {
            'name': name,
            'color': colors.get(t),
            'culture': st.get('primary_culture'), 'religion': st.get('religion'),
            'provs': provs.get(t, 0), 'dev': dev.get(t, 0), 'lore': lore_idx[lore],
        }
        for key, fname, tmap, series, bf, br_key, mt_key in KINDS:
            if t not in tmap:
                continue
            variants = []
            for label, files, assume, chain in tmap[t]:
                vctx = with_switch(with_chain(ctx, chain), assume)  # '>TAG' and '+flag' / '-flag' in the txt line
                variants.append((label, [i for f in files for i in bf.get(f.lower(), [])] if files is not None
                                 else series_for(series, vctx, facts), vctx))
            if not variants[0][1] and tmap[t][0][1] != []:  # [] = "none" on purpose
                no_tree[fname].append(t)
            mt = {}
            x[key], br = tree(series, variants[0][1], variants[0][2], lc, mt)
            if br:
                x[br_key] = br
            if mt:
                x[mt_key] = mt
            if len(variants) > 1:  # switchable trees: a label that is a tag means forming that country
                def form(label, idx, vctx):
                    f, fctx, flc, fmt = {'label': label}, vctx, lc, {}
                    fm = form_label(label)
                    if fm and fm.group(1).upper() in form_tags:  # its texts use the formed country's name
                        f['tag'] = ft = fm.group(1).upper()
                        fname_ = fm.group(2) or clean_loc(loc.get(ft)) or ft  # 'VSG "Suebi"': a custom name
                        f['label'] = 'Form ' + fname_
                        flc = (ctx, fname_, clean_loc(loc.get(ft + '_ADJ')) or fname_)
                        # branch packs are evaluated as the formed country (its series check tag = GER); every
                        # series of the mapped files stays, e.g. all of Germany's state-specific fifth groups
                        # (a '>TAG' chain already makes it the formed country, and keeps its stages)
                        fctx = vctx if vctx.strict else Ctx(ft, st, cgroups, religions, geo, {ft, t}, vctx.switch)
                    f['ab'], fbr = tree(series, idx, fctx, flc, fmt)
                    if fbr:
                        f['br'] = fbr
                    if fmt:
                        f['mt'] = fmt
                    return f
                x[key + 'Forms'] = [form(lb or name, idx, vctx) for lb, idx, vctx in variants]

    print('rendering map...', file=sys.stderr)
    pimg = province_image(L, SCALE)
    pnames, on_map = pimg['names'], set(pimg['on_map'])
    nprov = max(on_map) + 1
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
    used_groups = {cgroups[c] for c in used_cul if c in cgroups}
    group_cultures = {g: cmembers.get(g, []) for g in corder if g in used_groups}
    grp_out = {g: clean_loc(loc.get(g)) or g.replace('_', ' ').title() for g in corder}
    colors_path = here(CULTURE_COLORS_FILE)
    if not os.path.isfile(colors_path):
        write_culture_colors(colors_path, corder, grp_out)
        print(f'wrote {colors_path} with the current culture group colours', file=sys.stderr)
    group_colors = read_culture_colors(colors_path)
    unlisted_groups = sorted(g for g in used_groups if g not in group_colors)
    cul_out = {}
    for c in sorted(used_cul | {c for cs in group_cultures.values() for c in cs}):
        g = cgroups.get(c, '')
        base = group_colors.get(g) or group_base_color(g or c)
        cul_out[c] = {'name': clean_loc(loc.get(c)) or c, 'group': g, 'color': culture_color(base, c)}
    rel_out = {}
    for r in sorted(used_rel):
        info = religions.get(r, {})
        g = info.get('group', '')
        rel_out[r] = {'name': clean_loc(loc.get(r)) or r, 'group': g,
                      'gname': clean_loc(loc.get(g)) or g.replace('_', ' ').title(),
                      'color': info.get('color') or hsl(name_hash(r) % 360 / 360, 0.5, 0.5),
                      'desc': clean_loc(loc.get(r + '_religion_desc'))}

    print('packing flags and icons...', file=sys.stderr)
    flag_files = find_files(full_roots, 'gfx/flags', {'.tga', '.png', '.dds'})
    want = set(alive) | set(actum_map) | set(additional) | set(favorites) | set(actum_favorites) | form_tags
    flags = atlas({t: image_entry(flag_files[t], 64) for t in sorted(want) if t in flag_files}, 64)

    strip_path = texture(full_roots, sprites.get('GFX_icon_religion', 'gfx/interface/icon_religion.dds'))
    rel_entries = {}
    if strip_path:
        strip = []  # the full strip, opened only when a frame is missing from the cache

        def frame(n):
            if not strip:
                strip.append(open_image(strip_path))
            im = strip[0]
            if im is None or n > im.size[0] // im.size[1]:
                return None
            fh = im.size[1]
            return png_bytes(im.crop(((n - 1) * fh, 0, n * fh, fh)).resize((48, 48), Image.LANCZOS))

        for r in sorted(used_rel):
            n = religions.get(r, {}).get('icon')
            if n and n >= 1:
                ck = Cache.key(strip_path, 'religion', n, 48)
                rel_entries[r] = ck, lambda ck=ck, n=n: from_png(IMAGES.get(ck, lambda: frame(n)))
    rel_icons = atlas(rel_entries, 48)

    icons = {}
    for s in out_series:
        for m in s['missions']:
            ic = m['icon']
            if ic not in icons:
                tp = sprites.get(ic) or sprites.get('GFX_' + ic)
                p = texture(full_roots, tp) if tp else None
                icons[ic] = image_entry(p, 48, square=True) if p else None
    msn_icons = atlas({ic: e for ic, e in icons.items() if e}, 48)
    for s in out_series:
        for m in s['missions']:
            m['icon'] = msn_icons['idx'].get(m['icon'], -1)
    # compact missions: [index, icon, position, [required indexes], title, description] instead of objects
    # keyed by long mission ids (the page turns them back into objects)
    mindex = {}
    for s in out_series:
        for m in s['missions']:
            mindex.setdefault(m['id'], len(mindex))
    for s in out_series:
        s['missions'] = [[mindex[m['id']], m['icon'], m['pos'], [mindex.get(r, r) for r in m['req']], m['title'], m['desc']]
                         for m in s['missions']]
    for x in tags.values():  # per-country mission texts, keyed by the same indexes
        for o in [x] + x.get('abForms', []) + x.get('actForms', []):
            if 'mt' in o:
                o['mt'] = {mindex[i]: v for i, v in o['mt'].items()}
        if 'actMt' in x:
            x['actMt'] = {mindex[i]: v for i, v in x['actMt'].items()}
    tag_bbox = {}  # per country, merged from its provinces' boxes
    for pid, b in pimg['bbox'].items():
        t = p_owner[pid] if pid < nprov else ''
        if t:
            tb = tag_bbox.setdefault(t, list(b))
            tb[:] = [min(tb[0], b[0]), min(tb[1], b[1]), max(tb[2], b[2]), max(tb[3], b[3])]

    data = {  # text data, embedded gzipped; images and music go in `binary`
        'title': TITLE,
        'w': pimg['w'], 'h': pimg['h'], 'bbox': tag_bbox,
        'owner': p_owner, 'kind': p_kind, 'pcul': p_cul, 'prel': p_rel,
        'pname': [game['prov_names'].get(f'PROV{i}') or pnames.get(i, '') for i in range(nprov)],
        'tags': tags, 'lore': lore_list, 'series': out_series,
        'cultures': cul_out, 'cgroups': grp_out, 'groupCultures': group_cultures, 'religions': rel_out,
        'additional': additional, 'favorites': favorites, 'actumFavorites': actum_favorites,
        'abTree': ab_tree, 'actumTree': actum_tree,
        'flags': {'idx': flags['idx'], 'cols': flags['cols']},
        'relIcons': {'idx': rel_icons['idx'], 'cols': rel_icons['cols']},
        'msnIcons': {'cols': msn_icons['cols']},
    }
    binary = {
        'img': pimg['webp'], 'flags': flags['uri'], 'relIcons': rel_icons['uri'], 'msnIcons': msn_icons['uri'],
        'music': music_data(here(MUSIC_FILE)) if MUSIC_FILE and os.path.isfile(here(MUSIC_FILE)) else None,
    }
    text = json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode()
    tpl = open(here('template.html'), encoding='utf-8').read()
    html = tpl.replace('/*__DATA__*/', base64.b64encode(gzip.compress(text, 9, mtime=0)).decode())
    html = html.replace('/*__BINARY__*/null', json.dumps(binary, separators=(',', ':')))
    html = html.replace('__TITLE__', TITLE).replace('__SUBTITLE__', SUBTITLE)
    out = here(OUTPUT)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    IMAGES.save()

    def report(label, lst):
        missing = [t for t in lst if t not in tags]
        if missing:
            print(f'  {label}: not on the map at start: {" ".join(missing)}', file=sys.stderr)

    print(f'wrote {out} ({os.path.getsize(out) // 1024} KB): {len(alive)} tags, {len(actum_tree)} with an Actum tree, '
          f'{len(additional)} additional content, {len(ab_tree)} with an Ante Bellum tree, '
          f'{len(flags["idx"])} flags, {len(rel_icons["idx"])} religion icons, {len(msn_icons["idx"])} mission icons, '
          f'{sum(1 for t in tags.values() if lore_list[t["lore"]])} with lore', file=sys.stderr)
    report('actum.txt', list(actum_map))
    report('additional.txt', additional)
    report('favorites.txt', favorites)
    report('actum_favorites.txt', actum_favorites)
    report('ab.txt', list(ab_map))
    for fname, bad in bad_files.items():
        if bad:
            print(f'  {fname}: no such mission file: {" ".join(bad)}', file=sys.stderr)
    for fname, lst in no_tree.items():
        if lst:
            print(f'  {fname}: no mission tree found for: {" ".join(lst)} '
                  f'(map them yourself with "TAG = file.txt")', file=sys.stderr)
    if unlisted_groups:
        print(f'  culture.txt: no colour for these groups (generated instead): {" ".join(unlisted_groups)}',
              file=sys.stderr)
    # on_map skips the UnusedLand / RNW placeholder ids, which are never drawn
    nh = sum(1 for pid, k in enumerate(p_kind) if k == 4 and pid in on_map)
    if nh:
        print(f'  {nh} provinces on the map have no history file (shown as "no data")', file=sys.stderr)


if __name__ == '__main__':
    main()
