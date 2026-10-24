"""固定路線の経路探索。

目的地ごとに Connection Scan Algorithm（プロファイル版）を時刻の逆順に1回走らせ、
「停留所 s を時刻 τ 以降に出発したときの、目的地への最早到着時刻」を全停留所について求める。
"""
import math
from bisect import bisect_left

import numpy as np

INF = float("inf")


class Profile:
    """停留所ごとのパレート集合 (出発, 到着, 徒歩m, 最初に乗る路線)。出発の昇順で保持。"""
    __slots__ = ("dep", "arr", "walk", "route")

    def __init__(self):
        self.dep, self.arr, self.walk, self.route = [], [], [], []

    def eval(self, t):
        i = bisect_left(self.dep, t)
        if i == len(self.dep):
            return INF, 0.0, None
        return self.arr[i], self.walk[i], self.route[i]

    def add(self, d, a, w, r):
        i = bisect_left(self.dep, d)
        if i < len(self.dep) and self.arr[i] <= a:
            return False
        self.dep.insert(i, d)
        self.arr.insert(i, a)
        self.walk.insert(i, w)
        self.route.insert(i, r)
        # 出発が早いのに到着が遅い（＝劣る）要素を除く
        j = i - 1
        while j >= 0 and self.arr[j] >= a:
            for lst in (self.dep, self.arr, self.walk, self.route):
                del lst[j]
            j -= 1
        return True


def profile_search(conns_desc, trip_route, egress, footpaths, speed):
    """
    conns_desc: 出発時刻の降順に並んだ区間リスト
    egress: {stop: 目的地までの徒歩m}
    footpaths: {stop: [(隣の停留所, 徒歩m), ...]}
    戻り値: {stop: Profile}
    """
    prof = {}
    trip_best = {}
    for u, v, td, ta, trip, board, alight in conns_desc:
        best, bw = INF, 0.0
        if alight:
            if v in egress:
                best, bw = ta + egress[v] / speed, egress[v]
            pv = prof.get(v)
            if pv is not None:
                a, w, _ = pv.eval(ta)
                if a < best:
                    best, bw = a, w
        tb = trip_best.get(trip)
        if tb is not None and tb[0] < best:
            best, bw = tb
        if best == INF:
            continue
        if tb is None or best < tb[0]:
            trip_best[trip] = (best, bw)
        if not board:
            continue
        route = trip_route[trip]
        p = prof.setdefault(u, Profile())
        p.add(td, best, bw, route)
        for w_stop, dist in footpaths.get(u, ()):
            prof.setdefault(w_stop, Profile()).add(td - dist / speed, best, bw + dist, route)
    return prof


def to_grid(prof, stops, t0, t1):
    """停留所リストについて、分単位の表 arr[i, τ-t0], walk[i, τ-t0], route[i, τ-t0] を作る。"""
    n, m = len(stops), t1 - t0
    arr = np.full((n, m), np.inf, dtype=np.float64)
    walk = np.zeros((n, m), dtype=np.float32)
    route = np.full((n, m), -1, dtype=np.int32)
    taus = np.arange(t0, t1, dtype=np.float64)
    for i, s in enumerate(stops):
        p = prof.get(s)
        if p is None or not p.dep:
            continue
        idx = np.searchsorted(np.asarray(p.dep), taus, side="left")
        ok = idx < len(p.dep)
        arr[i, ok] = np.asarray(p.arr)[idx[ok]]
        walk[i, ok] = np.asarray(p.walk, dtype=np.float32)[idx[ok]]
        route[i, ok] = np.asarray(p.route, dtype=np.int32)[idx[ok]]
    return arr, walk, route


def build_footpaths(stops, max_m, detour, haversine):
    """停留所間の乗換徒歩（直線距離×補正、max_m 以内）。"""
    cell = 0.004
    grid = {}
    for s, v in stops.items():
        grid.setdefault((int(v["lat"] / cell), int(v["lon"] / cell)), []).append(s)
    fp = {}
    for s, v in stops.items():
        gy, gx = int(v["lat"] / cell), int(v["lon"] / cell)
        for y in (gy - 1, gy, gy + 1):
            for x in (gx - 1, gx, gx + 1):
                for t in grid.get((y, x), ()):
                    if t == s:
                        continue
                    d = haversine(v["lat"], v["lon"], stops[t]["lat"], stops[t]["lon"]) * detour
                    if d <= max_m:
                        fp.setdefault(s, []).append((t, d))
    return fp


def ceil_min(x):
    return int(math.ceil(x - 1e-9))
