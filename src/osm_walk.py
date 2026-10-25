"""OpenStreetMap の道路網による徒歩距離計算。

取得順: Overpass API → OSM 本体 API（範囲を分割） → 失敗時は None（直線距離×補正で近似）。
取得結果は data/cache/ に保存し、次回以降はキャッシュを使う。
"""
import hashlib
import heapq
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSM_API = "https://api.openstreetmap.org/api/0.6/map?bbox={w},{s},{e},{n}"
USER_AGENT = "life-without-a-car/1.0 (demand transport accessibility map)"

# 歩けない道路種別
EXCLUDE_HIGHWAY = {"motorway", "motorway_link", "construction", "proposed", "raceway",
                   "bus_guideway", "abandoned", "platform"}


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def _walkable(tags):
    hw = tags.get("highway")
    if not hw or hw in EXCLUDE_HIGHWAY:
        return False
    if tags.get("foot") == "no":
        return False
    if tags.get("access") in ("no", "private") and tags.get("foot") not in ("yes", "designated"):
        return False
    if tags.get("area") == "yes":
        return False
    return True


def _http(url, data=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_overpass(bbox):
    s, w, n, e = bbox
    q = f'[out:json][timeout:170];way["highway"]({s},{w},{n},{e});(._;>;);out body;'
    for url in OVERPASS_URLS:
        try:
            raw = _http(url, urllib.parse.urlencode({"data": q}).encode())
            js = json.loads(raw)
            nodes = {el["id"]: (el["lat"], el["lon"]) for el in js["elements"] if el["type"] == "node"}
            ways = [el["nodes"] for el in js["elements"]
                    if el["type"] == "way" and _walkable(el.get("tags", {}))]
            return nodes, ways
        except Exception as ex:  # noqa: BLE001 — 次の取得方法へ
            print(f"  Overpass 取得失敗 ({url}): {ex}")
    return None


def _fetch_osm_api_tile(s, w, n, e, depth=0):
    try:
        raw = _http(OSM_API.format(s=s, w=w, n=n, e=e))
    except urllib.error.HTTPError as ex:
        if ex.code == 400 and depth < 4:  # ノード数上限超過 → 4分割
            ms, mw = (s + n) / 2, (w + e) / 2
            out_n, out_w = {}, []
            for bb in [(s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)]:
                nn, ww = _fetch_osm_api_tile(*bb, depth=depth + 1)
                out_n.update(nn)
                out_w.extend(ww)
            return out_n, out_w
        raise
    root = ET.fromstring(raw)
    nodes = {int(nd.get("id")): (float(nd.get("lat")), float(nd.get("lon")))
             for nd in root.iter("node")}
    ways = []
    for way in root.iter("way"):
        tags = {t.get("k"): t.get("v") for t in way.iter("tag")}
        if _walkable(tags):
            ways.append([int(nd.get("ref")) for nd in way.iter("nd")])
    return nodes, ways


def _fetch_osm_api(bbox, tile_deg=0.02):
    s, w, n, e = bbox
    nodes, ways, seen = {}, [], set()
    lat = s
    tiles = []
    while lat < n:
        lon = w
        while lon < e:
            tiles.append((lat, lon, min(lat + tile_deg, n), min(lon + tile_deg, e)))
            lon += tile_deg
        lat += tile_deg
    for i, t in enumerate(tiles):
        print(f"  OSM API タイル {i + 1}/{len(tiles)}")
        nn, ww = _fetch_osm_api_tile(*t)
        nodes.update(nn)
        for wy in ww:
            k = (wy[0], wy[-1], len(wy))
            if k not in seen:
                seen.add(k)
                ways.append(wy)
        time.sleep(1)  # API 利用ポリシーへの配慮
    return nodes, ways


def fetch_walk_network(bboxes, cache_dir):
    """bboxes: [(south, west, north, east), ...]。戻り値 (nodes, ways, source) または None。"""
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.md5(json.dumps([[round(x, 4) for x in b] for b in bboxes]).encode()).hexdigest()[:10]
    path = os.path.join(cache_dir, f"osm_walk_{key}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            js = json.load(f)
        print(f"  OSM キャッシュを使用: {path}")
        return {int(k): tuple(v) for k, v in js["nodes"].items()}, js["ways"], js["source"]
    all_nodes, all_ways, source = {}, [], None
    for bb in bboxes:
        got = _fetch_overpass(bb)
        src = "Overpass API"
        if got is None:
            try:
                got = _fetch_osm_api(bb)
                src = "OpenStreetMap API"
            except Exception as ex:  # noqa: BLE001
                print(f"  OSM API 取得失敗: {ex}")
                return None
        all_nodes.update(got[0])
        all_ways.extend(got[1])
        source = src if source in (None, src) else "Overpass API / OpenStreetMap API"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "fetched": time.strftime("%Y-%m-%d"),
                   "nodes": all_nodes, "ways": all_ways}, f)
    return all_nodes, all_ways, source


class WalkGraph:
    """徒歩グラフ。距離はメートル。"""

    def __init__(self, nodes, ways):
        used = {nid for w in ways for nid in w if nid in nodes}
        self.ids = sorted(used)
        index = {nid: i for i, nid in enumerate(self.ids)}
        self.lat = [nodes[n][0] for n in self.ids]
        self.lon = [nodes[n][1] for n in self.ids]
        self.adj = [[] for _ in self.ids]
        for w in ways:
            pts = [index[n] for n in w if n in index]
            for a, b in zip(pts, pts[1:]):
                if a == b:
                    continue
                d = haversine_m(self.lat[a], self.lon[a], self.lat[b], self.lon[b])
                self.adj[a].append((b, d))
                self.adj[b].append((a, d))
        self._keep_largest_component()
        self._build_grid()

    def _keep_largest_component(self):
        comp = [-1] * len(self.ids)
        sizes = []
        for s in range(len(self.ids)):
            if comp[s] >= 0:
                continue
            c = len(sizes)
            stack, n = [s], 0
            comp[s] = c
            while stack:
                u = stack.pop()
                n += 1
                for v, _ in self.adj[u]:
                    if comp[v] < 0:
                        comp[v] = c
                        stack.append(v)
            sizes.append(n)
        # 小さな孤立部分（私道の切れ端など）へのスナップを防ぐ
        big = max(range(len(sizes)), key=sizes.__getitem__)
        self.active = [comp[i] == big or sizes[comp[i]] >= 200 for i in range(len(self.ids))]

    def _build_grid(self, cell=0.003):
        self.cell = cell
        self.grid = {}
        for i, (la, lo) in enumerate(zip(self.lat, self.lon)):
            if self.active[i]:
                self.grid.setdefault((int(la / cell), int(lo / cell)), []).append(i)

    def nearest(self, lat, lon):
        """最寄りノード (index, 直線距離m)。"""
        gy, gx = int(lat / self.cell), int(lon / self.cell)
        best, bd = None, float("inf")
        for r in range(0, 12):
            for y in range(gy - r, gy + r + 1):
                for x in range(gx - r, gx + r + 1):
                    if max(abs(y - gy), abs(x - gx)) != r:
                        continue
                    for i in self.grid.get((y, x), ()):
                        d = haversine_m(lat, lon, self.lat[i], self.lon[i])
                        if d < bd:
                            best, bd = i, d
            if best is not None and bd < r * self.cell * 90000:
                break
        return best, bd

    def dijkstra(self, source, cutoff=float("inf")):
        """source ノードからの距離 dict {node: m}。"""
        dist = {source: 0.0}
        pq = [(0.0, source)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")) or d > cutoff:
                continue
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, float("inf")) and nd <= cutoff:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist


class Walker:
    """地点間の徒歩距離を返す。OSM グラフが無ければ直線距離×補正係数。"""

    def __init__(self, graph, detour_factor):
        self.g = graph
        self.k = detour_factor
        self._snap = {}

    @property
    def uses_osm(self):
        return self.g is not None

    def snap(self, lat, lon):
        key = (round(lat, 6), round(lon, 6))
        if key not in self._snap:
            self._snap[key] = self.g.nearest(lat, lon)
        return self._snap[key]

    def from_point(self, lat, lon, targets, cutoff):
        """targets: {key: (lat, lon)} → {key: 距離m}（cutoff 以内のみ）。"""
        out = {}
        if self.g is None:
            for k, (la, lo) in targets.items():
                d = haversine_m(lat, lon, la, lo) * self.k
                if d <= cutoff:
                    out[k] = d
            return out
        src, sd = self.snap(lat, lon)
        # 遠すぎる対象を先に除外（直線距離は道のり以下）
        cand = {k: v for k, v in targets.items() if haversine_m(lat, lon, *v) <= cutoff}
        if not cand:
            return out
        dist = self.g.dijkstra(src, cutoff)
        for k, (la, lo) in cand.items():
            node, td = self.snap(la, lo)
            if node in dist:
                d = sd + dist[node] + td
                if d <= cutoff:
                    out[k] = d
        return out
