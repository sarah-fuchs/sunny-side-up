#!/usr/bin/env python3
"""Generate fake WFS / Overpass responses so the pipeline can run offline.

District 7: the 15 real Spittelberg terrace points (two split into Gehsteig + Parkspur rows
to test merging) with a synthetic block grid around them.
District 2: a large synthetic district (400 terraces, thousands of buildings) for a
performance check of the web app.
"""
import json, math, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))
import build  # noqa: E402

FX = os.path.join(HERE, "fixtures")
random.seed(7)

SPITTELBERG = [
    ("Stiftgasse", "13", "Gehsteig", 27, 16.354800, 48.201585),
    ("Siebensterngasse", "18", "Gehsteig", 28, 16.354726, 48.202519),
    ("Stiftgasse", "4", "Gehsteig", 22, 16.354620, 48.202605),
    ("Siebensterngasse", "5", "Gehsteig", 30, 16.356634, 48.202664),
    ("Siebensterngasse", "5", "Parkspur", 12, 16.356640, 48.202650),
    ("Schrankgasse", "2", "Gehsteig", 84, 16.354565, 48.202812),
    ("Siebensterngasse", "8", "Gehsteig", 25, 16.356354, 48.202815),
    ("Spittelberggasse", "3", "Fußgänger/Begegnungszone", 90, 16.355266, 48.202880),
    ("Schrankgasse", "4", "Fußgänger/Begegnungszone", 41, 16.354820, 48.203010),
    ("Schrankgasse", "1", "Fußgänger/Begegnungszone", 9, 16.354380, 48.203131),
    ("Schrankgasse", "8", "Gehsteig", 74, 16.354752, 48.203275),
    ("Spittelberggasse", "18", "Fußgänger/Begegnungszone", 10, 16.355266, 48.203439),
    ("Schrankgasse", "10", "Fußgänger/Begegnungszone", 26, 16.354706, 48.203451),
    ("Breite Gasse", "9", "Privatgrund", 26, 16.356648, 48.203602),
    ("Schrankgasse", "11", "Gehsteig", 24, 16.354270, 48.203960),
    ("Burggasse", "23", "Parkspur", 4, 16.354281, 48.203999),
]


def feat_point(i, street, onr, kind, area, lon, lat, plz):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"OBJECTID": i, "NAME": f"Schanigarten {kind}", "PLZ": plz, "ORT": "Wien",
                           "STRASSE": street, "ONR": onr, "FLAECHE": area, "GENEHMIGUNGSSTATUS": "Genehmigt"}}


def blocks(lon0, lat0, nx, ny, bw=38, bh=30, street=9):
    """Synthetic city blocks: each block = 3 houses of varying height, with a courtyard gap."""
    proj = build.Proj(lon0, lat0)
    out, fid = [], 0
    for i in range(-nx, nx + 1):
        for j in range(-ny, ny + 1):
            x0, y0 = i * (bw + street), j * (bh + street)
            for k in range(3):
                fid += 1
                ground = 35 + random.uniform(-1, 1)
                h = random.choice([12, 16, 19, 22, 25, 28])
                xa, xb = x0 + k * bw / 3, x0 + (k + 1) * bw / 3 - 0.2
                ring = [proj.ll(xa, y0), proj.ll(xb, y0), proj.ll(xb, y0 + bh * 0.45), proj.ll(xa, y0 + bh * 0.45), proj.ll(xa, y0)]
                out.append({"type": "Feature", "id": f"F{lon0:.3f}_{fid}",
                            "geometry": {"type": "MultiPolygon", "coordinates": [[[list(p) for p in ring]]]},
                            "properties": {"FMZK_ID": f"{lon0:.3f}_{fid}", "O_KOTE": round(ground + h, 2), "T_KOTE": round(ground, 2)}})
                fid += 1
                ring2 = [proj.ll(xa, y0 + bh * 0.6), proj.ll(xb, y0 + bh * 0.6), proj.ll(xb, y0 + bh), proj.ll(xa, y0 + bh), proj.ll(xa, y0 + bh * 0.6)]
                out.append({"type": "Feature", "id": f"F{lon0:.3f}_{fid}",
                            "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring2]]},
                            "properties": {"FMZK_ID": f"{lon0:.3f}_{fid}", "O_KOTE": round(ground + h - 3, 2), "T_KOTE": round(ground, 2)}})
            # a passage (null roof) and a tiny stair part — must be dropped
            fid += 1
            out.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[list(proj.ll(x0, y0)), list(proj.ll(x0 + 2, y0)), list(proj.ll(x0 + 2, y0 + 2)), list(proj.ll(x0, y0))]]},
                        "properties": {"FMZK_ID": f"{lon0:.3f}_{fid}", "O_KOTE": None, "T_KOTE": 35}})
            fid += 1
            out.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[list(proj.ll(x0 + 3, y0)), list(proj.ll(x0 + 4, y0)), list(proj.ll(x0 + 4, y0 + 1)), list(proj.ll(x0 + 3, y0))]]},
                        "properties": {"FMZK_ID": f"{lon0:.3f}_{fid}", "O_KOTE": 36, "T_KOTE": 35}})
    return out


def streets_for(lon0, lat0, nx, ny, bw=38, bh=30, street=9, names=("Testgasse", "Probestraße", "Mustergasse", "Beispielweg")):
    proj = build.Proj(lon0, lat0)
    els = []
    for i in range(-nx, nx + 1):
        x = i * (bw + street) - street / 2
        els.append({"type": "way", "tags": {"highway": "residential", "name": f"{names[i % 2]} {i + nx}"},
                    "geometry": [dict(zip(("lon", "lat"), proj.ll(x, y))) for y in (-ny * 40, 0, ny * 40)]})
    for j in range(-ny, ny + 1):
        y = j * (bh + street) - street / 2
        els.append({"type": "way", "tags": {"highway": "residential", "name": f"{names[2 + j % 2]} {j + ny}"},
                    "geometry": [dict(zip(("lon", "lat"), proj.ll(x, y))) for x in (-nx * 50, 0, nx * 50)]})
    return {"elements": els}


def dump(name, obj):
    with open(os.path.join(FX, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def main():
    os.makedirs(FX, exist_ok=True)
    feats = [feat_point(i, *row, 1070) for i, row in enumerate(SPITTELBERG)]
    # large synthetic district 2
    big_lon, big_lat = 16.395, 48.215
    proj2 = build.Proj(big_lon, big_lat)
    for i in range(400):
        x, y = random.uniform(-900, 900), random.uniform(-700, 700)
        lon, lat = proj2.ll(x, y)
        feats.append(feat_point(1000 + i, f"Riesengasse {i // 20}", str(i % 20 + 1), "Gehsteig", random.randint(5, 80), lon, lat, 1020))
    # a not-approved row and an out-of-city PLZ — must be ignored
    feats.append({**feat_point(9998, "Schrankgasse", "99", "Gehsteig", 10, 16.3547, 48.2033, 1070)})
    feats[-1]["properties"]["GENEHMIGUNGSSTATUS"] = "Abgelehnt"
    feats.append(feat_point(9999, "Flughafen", "1", "Gehsteig", 10, 16.57, 48.11, 1300))
    dump("wfs_schani_0.json", {"type": "FeatureCollection", "features": feats})

    # OSM venues: one address match, one base-number match, one nearby-only, one ambiguous address
    dump("osm_venues.json", {"elements": [
        {"type": "node", "lat": 48.2025, "lon": 16.3547, "tags": {"amenity": "cafe", "name": "Café Achtzehn", "addr:street": "Siebensterngasse", "addr:housenumber": "18A"}},
        {"type": "node", "lat": 48.2016, "lon": 16.3548, "tags": {"amenity": "bar", "name": "Stiftbar", "addr:street": "Stiftgasse", "addr:housenumber": "13"}},
        {"type": "node", "lat": 48.20396, "lon": 16.35428, "tags": {"amenity": "restaurant", "name": "Nahe Elf"}},
        {"type": "node", "lat": 48.2034, "lon": 16.3547, "tags": {"amenity": "cafe", "name": "Overridden", "addr:street": "Schrankgasse", "addr:housenumber": "10"}},
        {"type": "node", "lat": 48.2032, "lon": 16.3547, "tags": {"amenity": "cafe", "name": "Acht A", "addr:street": "Schrankgasse", "addr:housenumber": "8"}},
        {"type": "node", "lat": 48.2032, "lon": 16.3547, "tags": {"amenity": "bar", "name": "Acht B", "addr:street": "Schrankgasse", "addr:housenumber": "8", "outdoor_seating": "yes"}},
    ]})

    terr = build.load_terraces()
    for d, (lon0, lat0, nx, ny) in {7: (16.3555, 48.2030, 4, 5), 2: (big_lon, big_lat, 21, 20)}.items():
        ts = terr[d]
        plon = sum(t["lon"] for t in ts) / len(ts)
        plat = sum(t["lat"] for t in ts) / len(ts)
        proj = build.Proj(plon, plat)
        bl = blocks(lon0, lat0, nx, ny)
        for i, (w, s, e, n) in enumerate(build.cluster_boxes(ts, proj)):
            inbox = [f for f in bl if any(w <= c[0] <= e and s <= c[1] <= n
                     for poly in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"])
                     for c in poly[0])]
            for start in range(0, len(inbox) + 1, 1000):  # same paging as the pipeline
                dump(f"wfs_bkm_{d:02d}_{i}_{start}.json", {"type": "FeatureCollection", "features": inbox[start:start + 1000]})
        dump(f"osm_streets_{d:02d}.json", streets_for(lon0, lat0, nx, ny))
    print("fixtures written to", FX)


if __name__ == "__main__":
    build.FIXTURES = FX
    main()
