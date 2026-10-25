"""市区町村界（OpenStreetMap / Nominatim）の取得と内外判定。"""
import json
import os
import urllib.request

from osm_walk import USER_AGENT

NOMINATIM = ("https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}"
             "&zoom=10&format=json&polygon_geojson=1&polygon_threshold=0.0002")


def fetch_boundary(lat, lon, cache_dir):
    """地点を含む市区町村の境界 GeoJSON geometry と名称。取得できなければ None。"""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"boundary_{lat:.4f}_{lon:.4f}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    try:
        req = urllib.request.Request(NOMINATIM.format(lat=lat, lon=lon), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            js = json.loads(r.read())
    except Exception as ex:  # noqa: BLE001
        print(f"  境界の取得に失敗: {ex}")
        return None
    geom = js.get("geojson")
    if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    out = {"name": js.get("name"), "osm": f"{js.get('osm_type')}/{js.get('osm_id')}", "geometry": geom}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def _in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def contains(geometry, lat, lon):
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for poly in polys:
        if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False
