#!/usr/bin/env python3
"""Build per-district data for sunny-side-up.

For each Vienna district:
  1. approved Schanigärten (points) from the Stadt Wien WFS, merged per address,
  2. buildings with heights (Baukörpermodell) within ~150 m of those terraces,
  3. venue names from OpenStreetMap (matched by address), overridden by overrides.json,
  4. named street centrelines from OpenStreetMap for map labels.

Writes web/data/districts/<NN>.json and web/data/index.json.
Standard library only, so it runs on a bare GitHub runner.

Usage:
  python pipeline/build.py              # all 23 districts
  python pipeline/build.py 7 1 6        # selected districts
  python pipeline/build.py --fixtures DIR 7   # offline, read cached responses from DIR
  python pipeline/build.py --tiles      # map tiles with every building in Vienna (carries on where it stopped)
"""
import argparse, json, math, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "data")
OVERRIDES = os.path.join(ROOT, "pipeline", "overrides.json")

WFS = "https://data.wien.gv.at/daten/geo"
OVERPASS = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
UA = "sunny-side-up/1.0 (personal open-data project; github.com/sarah-fuchs/sunny-side-up)"

REACH_M = 150          # buildings further than this from every terrace are dropped
CLUSTER_M = 350        # grid size for grouping terraces into building-fetch boxes
MIN_HEIGHT = 2.5       # drop building parts lower than this
SIMPLIFY_M = 0.3       # Douglas–Peucker tolerance
NEAR_NAME_M = 12       # fallback: nearest named OSM venue within this distance

DISTRICTS = {
    1: "Innere Stadt", 2: "Leopoldstadt", 3: "Landstraße", 4: "Wieden", 5: "Margareten",
    6: "Mariahilf", 7: "Neubau", 8: "Josefstadt", 9: "Alsergrund", 10: "Favoriten",
    11: "Simmering", 12: "Meidling", 13: "Hietzing", 14: "Penzing", 15: "Rudolfsheim-Fünfhaus",
    16: "Ottakring", 17: "Hernals", 18: "Währing", 19: "Döbling", 20: "Brigittenau",
    21: "Floridsdorf", 22: "Donaustadt", 23: "Liesing",
}

KIND = {
    "Schanigarten Gehsteig": "Gehsteig",
    "Schanigarten Parkspur": "Parkspur",
    "Schanigarten Fußgänger/Begegnungszone": "Fußgängerzone",
    "Schanigarten Privatgrund": "Privatgrund",
}

FIXTURES = None  # set by --fixtures: read responses from disk instead of the network


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- network

def _cache_name(kind, key):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", key)[:120]
    return os.path.join(FIXTURES, f"{kind}_{safe}.json")


class ServerUnreachable(RuntimeError):
    """The server stopped answering at all (connection timeouts / refused)."""


PAUSE = 0.7   # seconds between requests, to stay polite with the city server
WAITS = [10, 30, 90, 180]  # backoff between attempts


def http_json(url, data=None, kind="get", key=None, tries=len(WAITS) + 1, timeout=90):
    if FIXTURES:
        with open(_cache_name(kind, key or url), encoding="utf-8") as f:
            return json.load(f)
    body = urllib.parse.urlencode(data).encode() if data else None
    detail, unreachable = "", False
    for attempt in range(tries):
        text = ""
        time.sleep(PAUSE)
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
                return json.loads(text)
        except urllib.error.HTTPError as e:
            snippet = e.read().decode("utf-8", "replace")[:300]
            detail, unreachable = f"HTTP {e.code}: {snippet}", False
        except json.JSONDecodeError:
            detail, unreachable = f"not JSON: {text[:300]}", False  # WFS errors come back as XML with status 200
        except Exception as e:  # noqa: BLE001 — retry anything transient
            detail, unreachable = f"{type(e).__name__}: {e}", True
        if attempt < tries - 1:
            wait = WAITS[attempt]
            log(f"  request failed ({detail[:200]}); retry in {wait}s")
            time.sleep(wait)
    short = url if len(url) < 300 else url[:300] + "…"
    log(f"  giving up: {detail[:300]}")
    raise (ServerUnreachable if unreachable else RuntimeError)(f"giving up on {short}: {detail[:300]}")


def wfs(type_name, extra, page=5000, key=None, timeout=90):
    """Fetch all features of a WFS 2.0.0 layer, paging by startIndex."""
    feats, start = [], 0
    while True:
        params = {
            "service": "WFS", "request": "GetFeature", "version": "2.0.0",
            "typeNames": type_name, "srsName": "EPSG:4326", "outputFormat": "json",
            "count": page, "startIndex": start, **extra,
        }
        url = WFS + "?" + urllib.parse.urlencode(params, safe=":,")
        j = http_json(url, kind="wfs", key=f"{key or type_name}_{start}", timeout=timeout)
        got = j.get("features", [])
        feats += got
        if len(got) < page:
            return feats
        start += page


def wfs_all(type_name, key):
    """One plain WFS 1.0.0 request for a whole (small) layer — the URL format Stadt Wien publishes."""
    params = {"service": "WFS", "request": "GetFeature", "version": "1.0.0",
              "typeName": type_name, "srsName": "EPSG:4326", "outputFormat": "json"}
    url = WFS + "?" + urllib.parse.urlencode(params, safe=":")
    return http_json(url, kind="wfs", key=f"{key}_0").get("features", [])


def overpass(query, key):
    return http_json(OVERPASS, data={"data": query}, kind="osm", key=key)


# ---------------------------------------------------------------- geometry

class Proj:
    """Local equirectangular projection in metres around (lon0, lat0)."""
    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.kx = math.cos(math.radians(lat0)) * 111320
        self.ky = 110540

    def xy(self, lon, lat):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def ll(self, x, y):
        return self.lon0 + x / self.kx, self.lat0 + y / self.ky


def dp(pts, tol):
    """Douglas–Peucker on a list of (x, y)."""
    if len(pts) < 3:
        return pts
    ax, ay = pts[0]
    bx, by = pts[-1]
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    best, bi = -1.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        d = abs(dy * px - dx * py + bx * ay - by * ax) / L if L else math.hypot(px - ax, py - ay)
        if d > best:
            best, bi = d, i
    if best <= tol:
        return [pts[0], pts[-1]]
    return dp(pts[: bi + 1], tol)[:-1] + dp(pts[bi:], tol)


def simplify_xy(pts, tol=SIMPLIFY_M):
    """Simplify a ring of (x, y) metres (closed or open); returns an open ring (≥3 points) or None."""
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    # split at the vertex furthest from the first, simplify both halves
    far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    a = dp(pts[: far + 1], tol)
    b = dp(pts[far:] + [pts[0]], tol)
    out = a[:-1] + b[:-1]
    return out if len(out) >= 3 else None


def simplify_ring(ring, proj, tol=SIMPLIFY_M):
    """Simplify a closed ring; returns open ring of [lon, lat] (≥3 points) or None."""
    out = simplify_xy([proj.xy(lon, lat) for lon, lat in ring], tol)
    if not out:
        return None
    return [[round(v, 6) for v in proj.ll(x, y)] for x, y in out]


def outer_rings(geom):
    if not geom:
        return []
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    return []


# ---------------------------------------------------------------- addresses

def norm_street(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower().strip()
    s = s.replace("ß", "ss")
    s = re.sub(r"str\.?$", "strasse", s)
    s = re.sub(r"\bstr\.?\s", "strasse ", s)
    s = re.sub(r"[\s\-\.]", "", s)
    return s


def norm_number(n):
    n = ("" if n is None else str(n)).lower().replace(" ", "")
    return n


def base_number(n):
    m = re.match(r"\d+", norm_number(n))
    return m.group(0) if m else norm_number(n)


def fmt_number(n):
    return ("" if n is None else str(n)).strip()


# ---------------------------------------------------------------- steps

def load_terraces():
    log("Fetching Schanigärten …")
    feats = wfs_all("ogdwien:SCHANIGARTENOGD", key="schani")
    by_addr = {}
    for f in feats:
        p = f.get("properties", {})
        if (p.get("GENEHMIGUNGSSTATUS") or "Genehmigt") != "Genehmigt":
            continue
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        try:
            plz = int(str(p.get("PLZ")).strip())
        except (TypeError, ValueError):
            continue
        d = (plz - 1000) // 10
        if d not in DISTRICTS:
            continue
        street, onr = str(p.get("STRASSE") or "").strip(), fmt_number(p.get("ONR"))
        key = (d, norm_street(street), norm_number(onr))
        lon, lat = g["coordinates"][:2]
        try:
            area = float(str(p.get("FLAECHE") or 0).replace(",", "."))
        except ValueError:
            area = 0.0
        kind = KIND.get(p.get("NAME"), (p.get("NAME") or "").replace("Schanigarten ", ""))
        t = by_addr.get(key)
        if t is None:
            by_addr[key] = {"d": d, "street": street, "onr": onr, "plz": plz,
                            "pts": [(lon, lat, area)], "m2": area, "kinds": [kind]}
        else:
            t["pts"].append((lon, lat, area))
            t["m2"] += area
            if kind not in t["kinds"]:
                t["kinds"].append(kind)
    out = defaultdict(list)
    for t in by_addr.values():
        w = sum(max(a, 1) for _, _, a in t["pts"])
        t["lon"] = sum(lo * max(a, 1) for lo, _, a in t["pts"]) / w
        t["lat"] = sum(la * max(a, 1) for _, la, a in t["pts"]) / w
        out[t["d"]].append(t)
    log(f"  {sum(len(v) for v in out.values())} terraces at distinct addresses")
    annotate("notice", "Schanigärten", f"{len(feats)} rows, {sum(len(v) for v in out.values())} terraces; sample: {json.dumps(feats[0].get('properties', {}), ensure_ascii=False)[:400] if feats else 'none'}")
    return out


def load_osm_venues():
    log("Fetching OSM venues …")
    q = """[out:json][timeout:180];
area["name"="Wien"]["admin_level"="4"]->.a;
nwr["amenity"~"^(cafe|restaurant|bar|pub|biergarten|ice_cream|fast_food)$"]["name"](area.a);
out center tags;"""
    j = overpass(q, "venues")
    venues = []
    for e in j.get("elements", []):
        tags = e.get("tags", {})
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is None or not tags.get("name"):
            continue
        venues.append({
            "name": tags["name"].strip(), "lon": lon, "lat": lat,
            "street": norm_street(tags.get("addr:street")),
            "num": norm_number(tags.get("addr:housenumber")),
            "outdoor": tags.get("outdoor_seating") == "yes",
        })
    log(f"  {len(venues)} named venues")
    return venues


def pick_name(cands):
    if not cands:
        return None
    outdoor = [c for c in cands if c["outdoor"]]
    if outdoor:
        cands = outdoor
    names = []
    for c in cands:
        if c["name"] not in names:
            names.append(c["name"])
    return " / ".join(names[:3])


def load_overrides():
    try:
        with open(OVERRIDES, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    out = {}
    for addr, v in raw.items():
        if addr.startswith("_"):
            continue
        m = re.match(r"^(.*?)\s+(\d+\S*)$", addr.strip())
        if not m:
            continue
        v = {"name": v} if isinstance(v, str) else v
        out[(norm_street(m.group(1)), norm_number(m.group(2)))] = v
    return out


def name_terraces(terraces, venues, overrides):
    exact, base = defaultdict(list), defaultdict(list)
    for v in venues:
        if v["street"] and v["num"]:
            exact[(v["street"], v["num"])].append(v)
            base[(v["street"], base_number(v["num"]))].append(v)
    hits = {"override": 0, "address": 0, "nearby": 0}
    for t in terraces:
        s, n = norm_street(t["street"]), norm_number(t["onr"])
        ov = overrides.get((s, n)) or overrides.get((s, base_number(n)))
        if ov:
            if ov.get("name"):
                t["name"] = ov["name"]
            if ov.get("note"):
                t["note"] = ov["note"]
            hits["override"] += 1
            continue
        nm = pick_name(exact.get((s, n))) or pick_name(base.get((s, base_number(n))))
        if nm:
            t["name"] = nm
            hits["address"] += 1
            continue
        # fallback: a single named venue right next to the terrace point
        proj = Proj(t["lon"], t["lat"])
        near = [v for v in venues
                if abs(v["lat"] - t["lat"]) < 0.0003 and abs(v["lon"] - t["lon"]) < 0.0005
                and math.hypot(*proj.xy(v["lon"], v["lat"])) <= NEAR_NAME_M]
        if len({v["name"] for v in near}) == 1:
            t["name"] = near[0]["name"]
            hits["nearby"] += 1
    return hits


def cluster_boxes(terraces, proj):
    cells = defaultdict(list)
    for t in terraces:
        x, y = proj.xy(t["lon"], t["lat"])
        cells[(int(x // CLUSTER_M), int(y // CLUSTER_M))].append((x, y))
    boxes = []
    for pts in cells.values():
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        lo = proj.ll(min(xs) - REACH_M, min(ys) - REACH_M)
        hi = proj.ll(max(xs) + REACH_M, max(ys) + REACH_M)
        boxes.append((lo[0], lo[1], hi[0], hi[1]))
    return boxes


def load_buildings(d, terraces, proj):
    boxes = cluster_boxes(terraces, proj)
    log(f"  fetching buildings in {len(boxes)} boxes")
    seen, raw = set(), []
    for i, (w, s, e, n) in enumerate(boxes):
        bbox = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f},urn:ogc:def:crs:EPSG::4326"
        feats = wfs("ogdwien:FMZKBKMOGD", {"bbox": bbox, "sortBy": "FMZK_ID"}, page=1000,
                    key=f"bkm_{d:02d}_{i}")
        for f in feats:
            p = f.get("properties", {})
            fid = p.get("FMZK_ID") or f.get("id")
            if fid in seen:
                continue
            seen.add(fid)
            raw.append(f)
    # terrace grid for the 150 m proximity filter
    tgrid = defaultdict(list)
    for t in terraces:
        x, y = proj.xy(t["lon"], t["lat"])
        tgrid[(int(x // REACH_M), int(y // REACH_M))].append((x, y))

    def near_terrace(x0, y0, x1, y1):
        for cx in range(int((x0 - REACH_M) // REACH_M), int((x1 + REACH_M) // REACH_M) + 1):
            for cy in range(int((y0 - REACH_M) // REACH_M), int((y1 + REACH_M) // REACH_M) + 1):
                for tx, ty in tgrid.get((cx, cy), ()):
                    dx = max(x0 - tx, 0, tx - x1)
                    dy = max(y0 - ty, 0, ty - y1)
                    if dx * dx + dy * dy <= REACH_M * REACH_M:
                        return True
        return False

    out = []
    for f in raw:
        p = f.get("properties", {})
        o, t = p.get("O_KOTE"), p.get("T_KOTE")
        if o is None or t is None:
            continue
        o, t = float(o), float(t)
        if o - t < MIN_HEIGHT:
            continue
        for ring in outer_rings(f.get("geometry")):
            xy = [proj.xy(lo, la) for lo, la in ring]
            xs, ys = [q[0] for q in xy], [q[1] for q in xy]
            if not near_terrace(min(xs), min(ys), max(xs), max(ys)):
                continue
            r = simplify_ring(ring, proj)
            if r:
                out.append({"o": round(o, 1), "t": round(t, 1), "r": r})
    log(f"  {len(raw)} building features → {len(out)} parts kept")
    return out


def load_streets(d, terraces, proj):
    xs = [proj.xy(t["lon"], t["lat"])[0] for t in terraces]
    ys = [proj.xy(t["lon"], t["lat"])[1] for t in terraces]
    w, s = proj.ll(min(xs) - REACH_M, min(ys) - REACH_M)
    e, n = proj.ll(max(xs) + REACH_M, max(ys) + REACH_M)
    q = f"""[out:json][timeout:120];
way["highway"~"^(primary|secondary|tertiary|unclassified|residential|living_street|pedestrian)$"]["name"]({s:.6f},{w:.6f},{n:.6f},{e:.6f});
out geom;"""
    j = overpass(q, f"streets_{d:02d}")
    lines = []
    for el in j.get("elements", []):
        g = el.get("geometry") or []
        if len(g) < 2:
            continue
        pts = dp([proj.xy(p["lon"], p["lat"]) for p in g], 1.0)
        lines.append({"n": el["tags"]["name"],
                      "l": [[round(v, 6) for v in proj.ll(x, y)] for x, y in pts]})
    return lines


def build_district(d, terraces, venues, overrides):
    log(f"District {d} {DISTRICTS[d]}: {len(terraces)} terraces")
    lon0 = sum(t["lon"] for t in terraces) / len(terraces)
    lat0 = sum(t["lat"] for t in terraces) / len(terraces)
    proj = Proj(lon0, lat0)
    hits = name_terraces(terraces, venues, overrides)
    log(f"  names: {hits}")
    buildings = load_buildings(d, terraces, proj)
    try:
        streets = load_streets(d, terraces, proj)
    except Exception as e:  # labels are nice-to-have
        log(f"  streets failed: {e}")
        streets = []
    terraces.sort(key=lambda t: (norm_street(t["street"]), base_number(t["onr"]).zfill(5), t["onr"]))
    out_t = []
    for i, t in enumerate(terraces, 1):
        row = {"id": i, "addr": f'{t["street"]} {t["onr"]}'.strip(),
               "kind": " + ".join(t["kinds"]), "m2": round(t["m2"]),
               "lon": round(t["lon"], 6), "lat": round(t["lat"], 6)}
        if t.get("name"):
            row["name"] = t["name"]
        if t.get("note"):
            row["note"] = t["note"]
        out_t.append(row)
    data = {"d": d, "name": DISTRICTS[d], "plz": 1000 + d * 10,
            "center": [round(lon0, 6), round(lat0, 6)],
            "terraces": out_t, "buildings": buildings, "streets": streets}
    path = os.path.join(OUT, "districts", f"{d:02d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  wrote {os.path.relpath(path, ROOT)} ({os.path.getsize(path) / 1e6:.2f} MB)")
    return {"d": d, "name": DISTRICTS[d], "plz": 1000 + d * 10, "terraces": len(out_t),
            "named": sum(1 for t in out_t if t.get("name")), "buildings": len(buildings)}


# ---------------------------------------------------------------- city overview + water

CITY_CENTER = (16.3725, 48.2083)
VIENNA_BBOX = (16.17, 48.11, 16.58, 48.33)
WATER_SIMPLIFY_M = 1.5
WATER_MIN_M2 = 300


def _ring_area(pts):
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        a += x0 * y1 - x1 * y0
    return a / 2


def _join_ways(parts):
    """Join open way geometries (lists of (lon, lat)) into closed rings."""
    parts = [list(p) for p in parts if len(p) >= 2]
    rings = []
    while parts:
        cur = parts.pop()
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, p in enumerate(parts):
                if p[0] == cur[-1]:
                    cur += p[1:]
                elif p[-1] == cur[-1]:
                    cur += p[::-1][1:]
                elif p[-1] == cur[0]:
                    cur = p[:-1] + cur
                elif p[0] == cur[0]:
                    cur = p[::-1][:-1] + cur
                else:
                    continue
                parts.pop(i)
                changed = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


def _clean_ring(ring, proj):
    """Simplify; returns ([[lon, lat], ...], area_m2) or None."""
    pts = [proj.xy(lon, lat) for lon, lat in ring]
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    area = abs(_ring_area(pts))
    if area < WATER_MIN_M2:
        return None
    far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    out = dp(pts[: far + 1], WATER_SIMPLIFY_M)[:-1] + dp(pts[far:] + [pts[0]], WATER_SIMPLIFY_M)[:-1]
    if len(out) < 3:
        return None
    return [[round(v, 5) for v in proj.ll(x, y)] for x, y in out], area


def _in_vienna(ring):
    w, s_, e, n = VIENNA_BBOX
    return any(w <= lon <= e and s_ <= lat <= n for lon, lat in ring)


def load_water():
    """Water areas (Danube, Donaukanal, Wienfluss, ponds) and river/canal centre lines from OSM."""
    log("Fetching water …")
    q = """[out:json][timeout:300];
area["name"="Wien"]["admin_level"="4"]->.a;
(way["natural"="water"](area.a); relation["natural"="water"](area.a);
 way["waterway"="riverbank"](area.a); relation["waterway"="riverbank"](area.a););
out geom;
way["waterway"~"^(river|canal)$"](area.a);
out geom;"""
    j = overpass(q, "water")
    proj = Proj(*CITY_CENTER)
    polys, lines = [], []
    for el in j.get("elements", []):
        tags = el.get("tags", {})
        is_area = tags.get("natural") == "water" or tags.get("waterway") == "riverbank"
        if el["type"] == "way" and not is_area:
            g = [(p["lon"], p["lat"]) for p in el.get("geometry") or []]
            if len(g) >= 2 and _in_vienna(g):
                pts = dp([proj.xy(*p) for p in g], 2.0)
                lines.append([[round(v, 5) for v in proj.ll(x, y)] for x, y in pts])
            continue
        if el["type"] == "way":
            g = [(p["lon"], p["lat"]) for p in el.get("geometry") or []]
            outers, inners = ([g] if len(g) >= 4 and g[0] == g[-1] else []), []
        elif el["type"] == "relation":
            ow = [[(p["lon"], p["lat"]) for p in m.get("geometry") or []] for m in el.get("members", [])
                  if m.get("type") == "way" and m.get("role", "outer") in ("outer", "")]
            iw = [[(p["lon"], p["lat"]) for p in m.get("geometry") or []] for m in el.get("members", [])
                  if m.get("type") == "way" and m.get("role") == "inner"]
            outers, inners = _join_ways(ow), _join_ways(iw)
        else:
            continue
        holes = [c for c in (_clean_ring(r, proj) for r in inners) if c]
        for r in outers:
            if not _in_vienna(r):
                continue
            c = _clean_ring(r, proj)
            if not c:
                continue
            # attach holes that lie inside this outer ring's bbox
            lo = [p[0] for p in c[0]]; la = [p[1] for p in c[0]]
            mine = [h[0] for h in holes if min(lo) <= h[0][0][0] <= max(lo) and min(la) <= h[0][0][1] <= max(la)]
            polys.append([c[0]] + mine)
    log(f"  {len(polys)} water areas, {len(lines)} river lines")
    return {"areas": polys, "lines": lines}


def write_city(fetch_water=True):
    """city.json: every terrace of every built district + water; also district bboxes in index.json."""
    idx_path = os.path.join(OUT, "index.json")
    city_path = os.path.join(OUT, "city.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            index = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log("no index.json, city overview skipped")
        return
    terraces = []
    for e in index.get("districts", []):
        path = os.path.join(OUT, "districts", f"{e['d']:02d}.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        lons, lats = [], []
        for t in data["terraces"]:
            row = {k: t[k] for k in ("id", "addr", "kind", "m2", "lon", "lat")}
            row["d"] = e["d"]
            for k in ("name", "note"):
                if t.get(k):
                    row[k] = t[k]
            terraces.append(row)
            lons.append(t["lon"]); lats.append(t["lat"])
        for b in data["buildings"]:
            for lon, lat in b["r"]:
                lons.append(lon); lats.append(lat)
        if lons:
            e["bbox"] = [round(min(lons), 5), round(min(lats), 5), round(max(lons), 5), round(max(lats), 5)]
            e["center"] = data["center"]
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    try:
        with open(city_path, encoding="utf-8") as f:
            old_water = json.load(f).get("water") or {"areas": [], "lines": []}
    except (FileNotFoundError, json.JSONDecodeError):
        old_water = {"areas": [], "lines": []}
    water = old_water
    if fetch_water:
        try:
            water = load_water()
        except Exception as e:  # keep the old water rather than failing the whole run
            log(f"water failed, keeping previous: {e}")
            annotate("warning", "Water not updated", str(e)[:300])
    if fetch_water:
        write_green()
    city = {"updated": index.get("updated"), "center": list(CITY_CENTER), "terraces": terraces, "water": water}
    with open(city_path, "w", encoding="utf-8") as f:
        json.dump(city, f, ensure_ascii=False, separators=(",", ":"))
    log(f"wrote {os.path.relpath(city_path, ROOT)}: {len(terraces)} terraces, "
        f"{len(water['areas'])} water areas ({os.path.getsize(city_path) / 1e6:.2f} MB)")


# ---------------------------------------------------------------- map tiles: every building in Vienna
#
# The map is cut into TILE_M squares in one projection centred on CITY_CENTER (the same one the
# website and the app use). Each tile file holds every building whose footprint centre lies in it,
# plus the named streets crossing it:
#   {"x": ix, "y": iy, "b": [[roof_dm, ground_dm, x0, y0, dx1, dy1, ...], ...],
#                      "s": [{"i": osm_id, "n": name, "l": [x0, y0, dx1, dy1, ...]}, ...]}
# Coordinates are decimetres from the tile's south-west corner (ix*TILE_M, iy*TILE_M); after the
# first point each pair is the step from the previous point. Rings run counter-clockwise.
# data/tiles.json lists the tiles that have content ({"ix_iy": buildings}) and those that came back
# empty, so a run that stopped half way can carry on with --tiles.

TILE_M = 1000
TILE_WORKERS = 2  # tiles fetched at the same time; kept low to stay polite with the city server
CITY = Proj(*CITY_CENTER)
TILES_DIR = "tiles"


def tile_range():
    w, s, e, n = VIENNA_BBOX
    x0, y0 = CITY.xy(w, s)
    x1, y1 = CITY.xy(e, n)
    return [(ix, iy) for iy in range(math.floor(y0 / TILE_M), math.floor(y1 / TILE_M) + 1)
            for ix in range(math.floor(x0 / TILE_M), math.floor(x1 / TILE_M) + 1)]


def encode_line(pts, ox, oy, scale=10):
    """[(x, y) metres] → [x0, y0, dx1, dy1, …] in decimetres (scale 10) from (ox, oy); repeated points dropped."""
    out, px, py = [], None, None
    for x, y in pts:
        X, Y = round((x - ox) * scale), round((y - oy) * scale)
        if px is None:
            out += [X, Y]
        elif X != px or Y != py:
            out += [X - px, Y - py]
        else:
            continue
        px, py = X, Y
    return out


def decode_line(enc, ox, oy):
    pts, x, y = [], 0, 0
    for i in range(0, len(enc), 2):
        x, y = (enc[0], enc[1]) if i == 0 else (x + enc[i], y + enc[i + 1])
        pts.append((ox + x / 10, oy + y / 10))
    return pts


def fetch_tile_buildings(ix, iy):
    x0, y0 = ix * TILE_M, iy * TILE_M
    w, s = CITY.ll(x0, y0)
    e, n = CITY.ll(x0 + TILE_M, y0 + TILE_M)
    bbox = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f},urn:ogc:def:crs:EPSG::4326"
    try:
        feats = wfs("ogdwien:FMZKBKMOGD", {"bbox": bbox, "sortBy": "FMZK_ID"}, page=1000, key=f"tile_{ix}_{iy}".replace("-", "m"))
    except FileNotFoundError:
        if FIXTURES:
            return []  # offline test: no fixture means an empty tile
        raise
    out, seen = [], set()
    for f in feats:
        p = f.get("properties", {})
        fid = p.get("FMZK_ID") or f.get("id")
        if fid is not None:
            if fid in seen:
                continue
            seen.add(fid)
        o, t = p.get("O_KOTE"), p.get("T_KOTE")
        if o is None or t is None:
            continue
        o, t = float(o), float(t)
        if o - t < MIN_HEIGHT:
            continue
        for ring in outer_rings(f.get("geometry")):
            pts = [CITY.xy(lo, la) for lo, la in ring]
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            # each part belongs to the one tile that holds the centre of its bounding box
            if math.floor((min(xs) + max(xs)) / 2 / TILE_M) != ix or math.floor((min(ys) + max(ys)) / 2 / TILE_M) != iy:
                continue
            r = simplify_xy(pts)
            if not r:
                continue
            if _ring_area(r) < 0:
                r = r[::-1]
            enc = encode_line(r, x0, y0)
            if len(enc) >= 6:
                out.append([round(o * 10), round(t * 10)] + enc)
    return out


def load_city_streets():
    """Every named street in Vienna, as (osm_id, name, [(x, y)], bbox) in the city projection."""
    log("Fetching streets for the whole city …")
    q = """[out:json][timeout:600];
area["name"="Wien"]["admin_level"="4"]->.a;
way["highway"~"^(primary|secondary|tertiary|unclassified|residential|living_street|pedestrian)$"]["name"](area.a);
out geom;"""
    j = overpass(q, "streets_all")
    out = []
    for n, el in enumerate(j.get("elements", [])):
        g = el.get("geometry") or []
        if len(g) < 2 or not el.get("tags", {}).get("name"):
            continue
        pts = dp([CITY.xy(p["lon"], p["lat"]) for p in g], 1.0)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out.append((el.get("id", n), el["tags"]["name"], pts, (min(xs), min(ys), max(xs), max(ys))))
    log(f"  {len(out)} named streets")
    return out


def build_tiles(redo=False, max_minutes=280):
    """Fetch every building in Vienna tile by tile. Returns (done, failed)."""
    tiles_dir = os.path.join(OUT, TILES_DIR)
    os.makedirs(tiles_dir, exist_ok=True)
    idx_path = os.path.join(OUT, "tiles.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        idx = {}
    if redo or idx.get("size") != TILE_M:
        idx = {}
    tiles, empty = dict(idx.get("tiles", {})), set(idx.get("empty", []))

    def save():
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump({"size": TILE_M, "center": list(CITY_CENTER), "updated": time.strftime("%Y-%m-%d"),
                       "tiles": dict(sorted(tiles.items())), "empty": sorted(empty)}, f, separators=(",", ":"))

    todo = [(ix, iy) for ix, iy in tile_range() if f"{ix}_{iy}" not in tiles and f"{ix}_{iy}" not in empty]
    todo.sort(key=lambda k: (k[0] + .5) ** 2 + (k[1] + .5) ** 2)  # city centre first, outskirts last
    log(f"Tiles: {len(todo)} to fetch ({len(tiles)} done, {len(empty)} empty)")
    if not todo:
        save()
        return [], []
    streets = load_city_streets()
    sgrid = defaultdict(list)
    for s in streets:
        x0, y0, x1, y1 = s[3]
        for ix in range(math.floor(x0 / TILE_M), math.floor(x1 / TILE_M) + 1):
            for iy in range(math.floor(y0 / TILE_M), math.floor(y1 / TILE_M) + 1):
                sgrid[(ix, iy)].append(s)

    t_start, done, failed, unreachable, first_logged = time.time(), [], [], 0, False
    # fetch TILE_WORKERS tiles at a time (the server takes ~45 s per tile); results are handled in order
    pool = ThreadPoolExecutor(max_workers=TILE_WORKERS)
    ahead = {}

    def fetch(i):
        if i < len(todo) and i not in ahead:
            ahead[i] = pool.submit(fetch_tile_buildings, *todo[i])

    for n, (ix, iy) in enumerate(todo):
        for i in range(n, n + TILE_WORKERS):
            fetch(i)
        if time.time() - t_start > max_minutes * 60:
            annotate("warning", "Tiles: time is up", f"Stopped after {len(done)} tiles; {len(todo) - n} left. Run again with 'tiles' to carry on.")
            failed += [f"{a}_{b}" for a, b in todo[n:]]
            break
        key = f"{ix}_{iy}"
        try:
            b = ahead.pop(n).result()
            unreachable = 0
        except Exception as e:  # noqa: BLE001
            log(f"Tile {key} FAILED: {e}")
            failed.append(key)
            unreachable += 1  # failures in a row, of any kind
            if unreachable >= 5:
                annotate("error", "Tiles stopped: 5 failures in a row",
                         f"Stopped after {len(done)} tiles. Last error: {str(e)[:500]}. Run the workflow again later with 'tiles'.")
                failed += [f"{a}_{c}" for a, c in todo[n + 1:]]
                break
            continue
        ox, oy = ix * TILE_M, iy * TILE_M
        s = [{"i": sid, "n": name, "l": encode_line(pts, ox, oy)} for sid, name, pts, _ in sgrid.get((ix, iy), [])]
        path = os.path.join(tiles_dir, f"{key}.json")
        if b:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"x": ix, "y": iy, "b": b, "s": s}, f, ensure_ascii=False, separators=(",", ":"))
            tiles[key] = len(b)
            empty.discard(key)
        else:
            if os.path.exists(path):
                os.remove(path)
            empty.add(key)
            tiles.pop(key, None)
        done.append(key)
        if b and not first_logged:  # noqa: SIM102
            first_logged = True
            annotate("notice", "Tiles: first tile", f"Tile {key}: {len(b)} buildings, {len(s)} streets, {os.path.getsize(path) / 1e3:.0f} kB")
        if len(done) % 100 == 0:
            annotate("notice", f"Tiles: {len(done)} of {len(todo)}", f"{len(tiles)} tiles with {sum(tiles.values())} buildings so far, {time.time() - t_start:.0f} s")
        if b:
            log(f"Tile {key}: {len(b)} buildings, {len(s)} streets ({os.path.getsize(path) / 1e3:.0f} kB) [{n + 1}/{len(todo)}]")
        save()
    pool.shutdown(wait=False, cancel_futures=True)
    save()
    total = sum(tiles.values())
    annotate("notice", "Tiles", f"Fetched {len(done)} tiles this run; {len(tiles)} tiles with {total} buildings in all; {len(failed)} not done.")
    return done, failed


# ---------------------------------------------------------------- green areas (OpenStreetMap)
#
# data/green.json: parks, gardens, meadows, forests, vineyards, cemeteries, allotments … for the whole city,
#   {"updated": "YYYY-MM-DD", "areas": [[outer, hole, …], …]}
# each ring flat [x0, y0, dx1, dy1, …] in whole metres in the city projection (centre CITY_CENTER),
# later points as steps from the previous one. Simplified to GREEN_SIMPLIFY_M, pieces under GREEN_MIN_M2 dropped.

GREEN_SIMPLIFY_M = 1.5
GREEN_MIN_M2 = 200
# three smaller queries instead of one big one (the big one timed out on overpass-api.de)
GREEN_FILTERS = {
    "leisure": '["leisure"~"^(park|garden|playground|recreation_ground|nature_reserve|dog_park|golf_course)$"]',
    "landuse": '["landuse"~"^(grass|forest|meadow|recreation_ground|village_green|cemetery|allotments|vineyard|orchard|flowerbed)$"]',
    "natural": '["natural"~"^(wood|scrub|grassland|heath)$"]',
}
OVERPASS_MIRRORS = ["https://overpass.private.coffee/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def overpass_any(query, key):
    """Overpass with fallback to mirrors when the main server is busy."""
    last = None
    for url in [OVERPASS] + OVERPASS_MIRRORS:
        try:
            return http_json(url, data={"data": query}, kind="osm", key=key, tries=2, timeout=900)
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"  {url} failed: {str(e)[:200]}")
            last = e
    raise last


def _green_ring(ring):
    """[(lon, lat)] closed → simplified [(x, y)] metres in the city projection, or None if too small."""
    pts = [CITY.xy(lon, lat) for lon, lat in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3 or abs(_ring_area(pts)) < GREEN_MIN_M2:
        return None
    return simplify_xy(pts, GREEN_SIMPLIFY_M)


def load_green():
    log("Fetching green areas …")
    # Vienna in a 3×3 grid of boxes, every green filter per box: small requests the servers can manage
    w0, s0, e0, n0 = VIENNA_BBOX
    flt = "\n".join(f" nwr{f}({{bbox}});" for f in GREEN_FILTERS.values())
    elements, seen, fails = [], set(), []
    for i in range(3):
        for k in range(3):
            w, e = w0 + (e0 - w0) * i / 3, w0 + (e0 - w0) * (i + 1) / 3
            s_, n = s0 + (n0 - s0) * k / 3, s0 + (n0 - s0) * (k + 1) / 3
            box = f"{s_:.5f},{w:.5f},{n:.5f},{e:.5f}"
            q = f"""[out:json][timeout:900][maxsize:1073741824];
(
{flt.replace("{bbox}", box)}
);
out geom;"""
            try:
                got = overpass_any(q, f"green_{i}_{k}").get("elements", [])
            except FileNotFoundError:
                got = []  # offline test without this box
            except Exception as e:  # noqa: BLE001
                fails.append(f"{i}_{k}: {str(e)[:120]}")
                continue
            log(f"  box {i}_{k}: {len(got)} elements")
            for el in got:
                key = (el["type"], el.get("id"))
                if el.get("id") is None or key not in seen:
                    seen.add(key)
                    elements.append(el)
    if fails:
        raise RuntimeError(f"{len(fails)} of 9 boxes failed: " + " | ".join(fails))
    areas, n_pts = [], 0
    for el in elements:
        if el["type"] == "way":
            g = [(p["lon"], p["lat"]) for p in el.get("geometry") or []]
            outers, inners = ([g] if len(g) >= 4 and g[0] == g[-1] else []), []
        elif el["type"] == "relation":
            ow = [[(p["lon"], p["lat"]) for p in m.get("geometry") or []] for m in el.get("members", [])
                  if m.get("type") == "way" and m.get("role", "outer") in ("outer", "")]
            iw = [[(p["lon"], p["lat"]) for p in m.get("geometry") or []] for m in el.get("members", [])
                  if m.get("type") == "way" and m.get("role") == "inner"]
            outers, inners = _join_ways(ow), _join_ways(iw)
        else:
            continue
        holes = [h for h in (_green_ring(r) for r in inners) if h]
        for r in outers:
            if not _in_vienna(r):
                continue
            o = _green_ring(r)
            if not o:
                continue
            xs, ys = [p[0] for p in o], [p[1] for p in o]
            mine = [h for h in holes if min(xs) <= h[0][0] <= max(xs) and min(ys) <= h[0][1] <= max(ys)]
            areas.append([encode_line(ring, 0, 0, scale=1) for ring in [o] + mine])
            n_pts += sum(len(ring) for ring in [o] + mine)
    log(f"  {len(areas)} green areas, {n_pts} points")
    return areas


def write_green():
    path = os.path.join(OUT, "green.json")
    try:
        areas = load_green()
    except Exception as e:  # keep what we had
        log(f"green failed, keeping previous: {e}")
        annotate("warning", "Green areas not updated", str(e)[:300])
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"updated": time.strftime("%Y-%m-%d"), "areas": areas}, f, separators=(",", ":"))
    mb = os.path.getsize(path) / 1e6
    log(f"wrote {os.path.relpath(path, ROOT)}: {len(areas)} areas ({mb:.2f} MB)")
    annotate("notice", "Green areas", f"{len(areas)} areas, {mb:.2f} MB")


def main():
    global FIXTURES, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("districts", nargs="*", type=int)
    ap.add_argument("--missing", action="store_true", help="only districts that have no data file yet")
    ap.add_argument("--city-only", action="store_true", help="only rebuild city.json (all terraces + water) from existing district files")
    ap.add_argument("--no-water", action="store_true", help="don't fetch water; keep what city.json has")
    ap.add_argument("--tiles", action="store_true", help="fetch the map tiles (every building) that aren't done yet")
    ap.add_argument("--tiles-all", action="store_true", help="fetch all map tiles again")
    ap.add_argument("--max-minutes", type=int, default=280, help="stop fetching tiles after this long (default 280)")
    ap.add_argument("--fixtures", help="read cached responses from this folder (offline tests)")
    ap.add_argument("--out", default=OUT, help="output data folder (default web/data)")
    a = ap.parse_args()
    FIXTURES = a.fixtures
    OUT = a.out
    wanted = a.districts or sorted(DISTRICTS)
    os.makedirs(os.path.join(OUT, "districts"), exist_ok=True)
    if a.tiles or a.tiles_all:
        done, failed = build_tiles(redo=a.tiles_all, max_minutes=a.max_minutes)
        log(f"tiles done: {len(done)}; not done: {len(failed)}")
        if failed and not done:
            sys.exit(1)
        return
    if a.city_only:
        write_city(fetch_water=not a.no_water)
        return
    if a.missing:
        wanted = [d for d in wanted if not os.path.exists(os.path.join(OUT, "districts", f"{d:02d}.json"))]
        log(f"Missing districts: {wanted or 'none'}")
        if not wanted:
            write_city(fetch_water=not a.no_water)
            return

    terraces = load_terraces()
    venues = load_osm_venues()
    overrides = load_overrides()

    idx_path = os.path.join(OUT, "index.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            index = {e["d"]: e for e in json.load(f)["districts"]}
    except (FileNotFoundError, KeyError, json.JSONDecodeError, TypeError):
        index = {}

    def save_index():
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump({"updated": time.strftime("%Y-%m-%d"),
                       "districts": [index[k] for k in sorted(index)]},
                      f, ensure_ascii=False, indent=1)

    done, failed, unreachable_in_a_row = [], [], 0
    for n, d in enumerate(wanted):
        if not terraces.get(d):
            log(f"District {d}: no terraces, skipped")
            continue
        try:
            index[d] = build_district(d, terraces[d], venues, overrides)
            save_index()  # progress survives a later failure or timeout
            done.append(d)
            unreachable_in_a_row = 0
        except Exception as e:  # keep going; one bad district shouldn't block the rest
            import traceback
            log(f"District {d} FAILED: {e}")
            annotate("error", f"District {d} failed", traceback.format_exc()[-1200:])
            failed.append(d)
            unreachable_in_a_row = unreachable_in_a_row + 1 if isinstance(e, ServerUnreachable) else 0
            if unreachable_in_a_row >= 2:
                rest = [x for x in wanted[n + 1:] if terraces.get(x)]
                failed += rest
                annotate("error", "City server stopped answering",
                         f"Stopped early after two districts in a row could not reach the server. "
                         f"Built: {done or 'none'}. Not built: {failed}. Run the workflow again later with 'missing'.")
                break

    save_index()
    write_city(fetch_water=not a.no_water)
    log(f"done: {done}; failed: {failed}")
    annotate("notice", "Summary", f"Built: {done or 'none'}. Not built: {failed or 'none'}.")
    if failed and not done:
        sys.exit(1)  # nothing worked: fail the run


def annotate(level, title, msg):
    if os.environ.get("GITHUB_ACTIONS"):
        msg = msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::{level} title={title}::{msg}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        annotate("error", "Pipeline crashed", traceback.format_exc()[-1500:])
        raise
