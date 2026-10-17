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
"""
import argparse, json, math, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request
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


def http_json(url, data=None, kind="get", key=None, tries=5):
    if FIXTURES:
        with open(_cache_name(kind, key or url), encoding="utf-8") as f:
            return json.load(f)
    body = urllib.parse.urlencode(data).encode() if data else None
    detail = ""
    for attempt in range(tries):
        text = ""
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                text = r.read().decode("utf-8", "replace")
                return json.loads(text)
        except urllib.error.HTTPError as e:
            snippet = e.read().decode("utf-8", "replace")[:300]
            detail = f"HTTP {e.code}: {snippet}"
        except json.JSONDecodeError:
            detail = f"not JSON: {text[:300]}"  # WFS errors come back as XML with status 200
        except Exception as e:  # noqa: BLE001 — retry anything transient
            detail = f"{type(e).__name__}: {e}"
        wait = 5 * 2 ** attempt
        log(f"  request failed ({detail}); retry in {wait}s")
        if attempt < tries - 1:
            time.sleep(wait)
    short = url if len(url) < 300 else url[:300] + "…"
    if os.environ.get("GITHUB_ACTIONS"):  # visible as an annotation on the run
        print(f"::error title=Request failed::{short} — {detail}".replace("\n", " "), flush=True)
    raise RuntimeError(f"giving up on {short}: {detail}")


def wfs(type_name, extra, page=5000, key=None):
    """Fetch all features of a WFS 2.0.0 layer, paging by startIndex."""
    feats, start = [], 0
    while True:
        params = {
            "service": "WFS", "request": "GetFeature", "version": "2.0.0",
            "typeNames": type_name, "srsName": "EPSG:4326", "outputFormat": "json",
            "count": page, "startIndex": start, **extra,
        }
        url = WFS + "?" + urllib.parse.urlencode(params, safe=":,")
        j = http_json(url, kind="wfs", key=f"{key or type_name}_{start}")
        got = j.get("features", [])
        feats += got
        if len(got) < page:
            return feats
        start += page


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


def simplify_ring(ring, proj, tol=SIMPLIFY_M):
    """Simplify a closed ring; returns open ring of [lon, lat] (≥3 points) or None."""
    pts = [proj.xy(lon, lat) for lon, lat in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    # split at the vertex furthest from the first, simplify both halves
    far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    a = dp(pts[: far + 1], tol)
    b = dp(pts[far:] + [pts[0]], tol)
    out = a[:-1] + b[:-1]
    if len(out) < 3:
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
    s = unicodedata.normalize("NFKC", s or "").lower().strip()
    s = s.replace("ß", "ss")
    s = re.sub(r"str\.?$", "strasse", s)
    s = re.sub(r"\bstr\.?\s", "strasse ", s)
    s = re.sub(r"[\s\-\.]", "", s)
    return s


def norm_number(n):
    n = (n or "").lower().replace(" ", "")
    return n


def base_number(n):
    m = re.match(r"\d+", norm_number(n))
    return m.group(0) if m else norm_number(n)


def fmt_number(n):
    return (n or "").strip()


# ---------------------------------------------------------------- steps

def load_terraces():
    log("Fetching Schanigärten …")
    feats = wfs("ogdwien:SCHANIGARTENOGD", {}, key="schani")
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
        street, onr = (p.get("STRASSE") or "").strip(), fmt_number(p.get("ONR"))
        key = (d, norm_street(street), norm_number(onr))
        lon, lat = g["coordinates"][:2]
        area = float(p.get("FLAECHE") or 0)
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
        feats = wfs("ogdwien:FMZKBKMOGD", {"bbox": bbox, "sortBy": "FMZK_ID"}, page=2000,
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


def main():
    global FIXTURES, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("districts", nargs="*", type=int)
    ap.add_argument("--fixtures", help="read cached responses from this folder (offline tests)")
    ap.add_argument("--out", default=OUT, help="output data folder (default web/data)")
    a = ap.parse_args()
    FIXTURES = a.fixtures
    OUT = a.out
    wanted = a.districts or sorted(DISTRICTS)
    os.makedirs(os.path.join(OUT, "districts"), exist_ok=True)

    terraces = load_terraces()
    venues = load_osm_venues()
    overrides = load_overrides()

    idx_path = os.path.join(OUT, "index.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            index = {e["d"]: e for e in json.load(f)["districts"]}
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        index = {}

    failed = []
    for d in wanted:
        if not terraces.get(d):
            log(f"District {d}: no terraces, skipped")
            continue
        try:
            index[d] = build_district(d, terraces[d], venues, overrides)
        except Exception as e:  # keep going; one bad district shouldn't block the rest
            log(f"District {d} FAILED: {e}")
            failed.append(d)

    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump({"updated": time.strftime("%Y-%m-%d"),
                   "districts": [index[k] for k in sorted(index)]},
                  f, ensure_ascii=False, indent=1)
    log(f"wrote {os.path.relpath(idx_path, ROOT)}")
    if failed:
        log(f"failed districts: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
