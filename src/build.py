"""デマンド交通の効果を示す比較地図用データを作る。

使い方（リポジトリのルートで）:
    python src/build.py

data/raw/ の GTFS-Flex・運行実績・路線バスGTFS を読み、メッシュごとに
「固定路線＋徒歩」と「デマンド込み」の所要時間を計算して docs/data/ に JSON を書き出す。
"""
import datetime as dt
import json
import math
import os
import sys
import time
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gtfs  # noqa: E402
from boundary import contains, fetch_boundary  # noqa: E402
from demand import FlexService, RideModel, read_records  # noqa: E402
from osm_walk import Walker, WalkGraph, fetch_walk_network, haversine_m  # noqa: E402
from transit import build_footpaths, profile_search, to_grid  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INF = float("inf")

# 短縮時間の凡例（分）。フロントエンドの色分けと一致させる
SAVING_CLASSES = [0, 1, 5, 10, 20, 30]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def p(path):
    return os.path.join(ROOT, path)


# ---------------------------------------------------------------- メッシュ

def quarter_mesh(lat, lon):
    """1/4地域メッシュ（約250m）の (行, 列) と メッシュコード。"""
    i = int(math.floor(lat * 480))
    j = int(math.floor((lon - 100) * 320))
    i3, j3 = i // 4, j // 4
    code = (f"{i3 // 80:02d}{j3 // 80:02d}{(i3 % 80) // 10}{(j3 % 80) // 10}{i3 % 10}{j3 % 10}"
            f"{1 + (j % 4) // 2 + 2 * ((i % 4) // 2)}{1 + j % 2 + 2 * (i % 2)}")
    return i, j, code


def make_cells(points, max_dist):
    """デマンド乗降地点から max_dist 以内に中心があるメッシュ。"""
    cand = {}
    for la, lo in points:
        i0, j0, _ = quarter_mesh(la, lo)
        r = int(max_dist / 230) + 1
        for i in range(i0 - r, i0 + r + 1):
            for j in range(j0 - r, j0 + r + 1):
                cand[(i, j)] = None
    cells = []
    for (i, j) in sorted(cand):
        clat, clon = (i + 0.5) / 480, 100 + (j + 0.5) / 320
        if min(haversine_m(clat, clon, la, lo) for la, lo in points) <= max_dist:
            cells.append({"i": i, "j": j, "lat": clat, "lon": clon,
                          "code": quarter_mesh(clat, clon)[2]})
    return cells


def short_agency(name):
    """「永井バス（永井運輸株式会社）」→「永井バス」、「株式会社群馬バス」→「群馬バス」"""
    name = name.split("（")[0].split("(")[0]
    return name.replace("株式会社", "").replace("（株）", "").strip()


# ---------------------------------------------------------------- 日付

def pick_date(day, feeds, flex_zip, override):
    if override:
        return dt.date.fromisoformat(override)
    want = 2 if day == "weekday" else 6  # 水曜 / 日曜
    periods = [gtfs.feed_period(z) for z in feeds + [flex_zip]]
    start = max([dt.date.today()] + [s for s, _ in periods])
    for k in range(0, 400):
        d = start + dt.timedelta(days=k)
        if d.weekday() != want or any(not (s <= d <= e) for s, e in periods):
            continue
        if gtfs.active_services(flex_zip, d) and all(gtfs.active_services(z, d) for z in feeds):
            return d
    raise SystemExit(f"{day} の代表日が見つかりません。config.json の service_dates で指定してください")


# ---------------------------------------------------------------- メイン

def main():
    cfg = json.load(open(p("src/config.json"), encoding="utf-8"))
    speed = cfg["walk_speed_m_per_min"]
    out_dir = p(cfg["output_dir"])
    os.makedirs(os.path.join(out_dir, "scenarios"), exist_ok=True)

    # --- デマンド交通 ---
    log("GTFS-Flex を読み込み")
    flex = FlexService(p(cfg["flex_zip"]))
    dstops = flex.stops
    did = sorted(dstops)
    dindex = {s: k for k, s in enumerate(did)}
    flex_agency = gtfs.read_table(p(cfg["flex_zip"]), "agency.txt")

    # --- 固定路線 ---
    log("路線バス GTFS を読み込み")
    feeds = [p(z) for z in cfg["gtfs_zips"]]
    bus_stops, trip_route, route_labels, agencies = {}, {}, [], []
    label_index = {}
    for fi, z in enumerate(feeds):
        pref = f"{fi}:"
        bus_stops.update(gtfs.load_stops(z, pref))
        ag = {r.get("agency_id", ""): short_agency(r["agency_name"]) for r in gtfs.read_table(z, "agency.txt")}
        agencies.extend(ag.values())
        routes = {}
        for r in gtfs.read_table(z, "routes.txt"):
            name = r.get("route_long_name") or r.get("route_short_name") or r["route_id"]
            short = r.get("route_short_name") or ""
            if short and short not in name:
                name = f"{short} {name}"
            routes[r["route_id"]] = f"{ag.get(r.get('agency_id', ''), next(iter(ag.values())))} {name}"
        for r in gtfs.read_table(z, "trips.txt"):
            lab = routes.get(r["route_id"], "路線バス")
            if lab not in label_index:
                label_index[lab] = len(route_labels)
                route_labels.append(lab)
            trip_route[pref + r["trip_id"]] = label_index[lab]

    # --- 目的地 ---
    dests = []
    for d in cfg["destinations"]:
        pool = bus_stops if d.get("feed") == "gtfs" else dstops
        hits = [v for v in pool.values() if v["name"] == d["stop_name"]]
        if not hits and "lat" not in d:
            raise SystemExit(f"目的地 {d['stop_name']} が見つかりません")
        lat = d.get("lat", sum(h["lat"] for h in hits) / max(1, len(hits)))
        lon = d.get("lon", sum(h["lon"] for h in hits) / max(1, len(hits)))
        dests.append({**d, "lat": lat, "lon": lon})

    # --- 範囲 ---
    lats = [v["lat"] for v in dstops.values()]
    lons = [v["lon"] for v in dstops.values()]
    m = 0.012
    area = (min(lats) - m, min(lons) - m, max(lats) + m, max(lons) + m)
    in_area = lambda la, lo: area[0] <= la <= area[2] and area[1] <= lo <= area[3]  # noqa: E731
    bboxes = [area] + [(d["lat"] - 0.008, d["lon"] - 0.008, d["lat"] + 0.008, d["lon"] + 0.008)
                       for d in dests if not in_area(d["lat"], d["lon"])]

    # --- 徒歩ネットワーク ---
    log("OpenStreetMap の道路網を取得")
    net = None
    try:
        net = fetch_walk_network(bboxes, p(cfg["cache_dir"]))
    except Exception as ex:  # noqa: BLE001
        print(f"  道路網の取得に失敗: {ex}")
    if net:
        graph = WalkGraph(net[0], net[1])
        walk_method = {"type": "osm", "source": net[2],
                       "text": f"OpenStreetMap の道路網（{net[2]} で取得）上の最短経路"}
        log(f"  徒歩グラフ: ノード {len(graph.ids):,}")
    else:
        graph = None
        walk_method = {"type": "approx",
                       "text": f"道路網を取得できなかったため、直線距離 × {cfg['walk_detour_factor']} で近似"}
        log("  道路網なし → 直線距離で近似")
    walker = Walker(graph, cfg["walk_detour_factor"])

    def walk_fallback(a, b):
        return haversine_m(a[0], a[1], b[0], b[1]) * cfg["walk_detour_factor"]

    # --- メッシュ ---
    cells = make_cells([(v["lat"], v["lon"]) for v in dstops.values()],
                       cfg["mesh_max_dist_from_demand_stop_m"])
    log(f"メッシュ数: {len(cells)}")
    # 対象自治体の境界（OSM）。町外の病院などの乗降地点まわりを地図に出さないため
    bd = cfg.get("boundary_from_destination")
    boundary = None
    if bd:
        d0 = next(d for d in dests if d["id"] == bd)
        boundary = fetch_boundary(d0["lat"], d0["lon"], p(cfg["cache_dir"]))
    if boundary:
        cells = [c for c in cells if contains(boundary["geometry"], c["lat"], c["lon"])]
        log(f"  {boundary['name']} の境界内: {len(cells)}")
    else:
        log("  境界なし（乗降地点からの距離だけで対象を決定）")

    area_bus = sorted(s for s, v in bus_stops.items() if in_area(v["lat"], v["lon"]))
    bindex = {s: k for k, s in enumerate(area_bus)}
    access = cfg["max_access_walk_m"]

    # --- 徒歩距離 ---
    log("徒歩距離: メッシュ → 停留所・乗降地点")
    tgt_bus = {s: (bus_stops[s]["lat"], bus_stops[s]["lon"]) for s in area_bus}
    tgt_dem = {s: (dstops[s]["lat"], dstops[s]["lon"]) for s in did}
    cell_dem = [walker.from_point(c["lat"], c["lon"], tgt_dem, access) for c in cells]
    # 川向こうなど、道のりで乗降地点に近づけないメッシュは対象外
    reach = cfg["mesh_max_walk_to_demand_stop_m"]
    keep = [k for k, cd in enumerate(cell_dem) if cd and min(cd.values()) <= reach]
    cells = [cells[k] for k in keep]
    cell_dem = [cell_dem[k] for k in keep]
    log(f"  道のり {reach}m 以内に乗降地点があるメッシュ: {len(cells)}")
    cell_bus = [walker.from_point(c["lat"], c["lon"], tgt_bus, access) for c in cells]

    log("徒歩距離: 乗降地点間（乗車時間モデル用の道のり）と 乗降地点 → 停留所")
    road = {}
    dem_bus = {}
    d2b = cfg.get("demand_to_bus_walk_m", 500)
    for s in did:
        v = dstops[s]
        dist = walker.from_point(v["lat"], v["lon"], tgt_dem, INF)
        for t, d in dist.items():
            # 徒歩用の道路網は車の道のりの近似。直線距離×補正より短くはしない
            road[(s, t)] = d if graph else walk_fallback((v["lat"], v["lon"]), tgt_dem[t])
        dem_bus[s] = walker.from_point(v["lat"], v["lon"], tgt_bus, d2b)

    log("運行実績から乗車時間モデルを作成")
    records = read_records(p(cfg["demand_zip"]))
    ride = RideModel(records, road, cfg["demand_od_min_samples"])
    log(f"  乗車時間 = {ride.intercept:.1f} + {ride.slope:.2f} × 道のりkm（R²={ride.r2:.2f}, n={ride.n_fit}）"
        f" / 実績中央値を使う区間 {len(ride.median)}")
    nd = len(did)
    R = np.full((nd, nd), np.inf)
    for a in did:
        for b in did:
            t = ride.minutes(a, b)
            if t is not None:
                R[dindex[a], dindex[b]] = t

    log("徒歩距離: 目的地 → 各地点")
    dest_walk = []
    for d in dests:
        near_bus = {s: (v["lat"], v["lon"]) for s, v in bus_stops.items()
                    if haversine_m(d["lat"], d["lon"], v["lat"], v["lon"]) <= access}
        tg = {("c", k): (c["lat"], c["lon"]) for k, c in enumerate(cells)}
        tg.update({("d", s): xy for s, xy in tgt_dem.items()})
        tg.update({("b", s): xy for s, xy in near_bus.items()})
        dist = walker.from_point(d["lat"], d["lon"], tg, INF)
        dw = {"cell": [], "dem": {}, "egress": {}}
        for k, c in enumerate(cells):
            dw["cell"].append(dist.get(("c", k), walk_fallback((d["lat"], d["lon"]), (c["lat"], c["lon"]))))
        for s in did:
            if ("d", s) in dist:
                dw["dem"][s] = dist[("d", s)]
        for s in near_bus:
            if dist.get(("b", s), INF) <= access:
                dw["egress"][s] = dist[("b", s)]
        dest_walk.append(dw)

    footpaths = build_footpaths(bus_stops, cfg["max_transfer_walk_m"], cfg["walk_detour_factor"], haversine_m)

    # --- 代表日 ---
    dates = {}
    for day in sorted({b["day"] for b in cfg["bands"]}):
        dates[day] = pick_date(day, feeds, p(cfg["flex_zip"]), cfg["service_dates"].get(day))
        log(f"代表日（{day}）: {dates[day]}")

    # --- シナリオ計算 ---
    margin = cfg["demand_pickup_margin_min"]
    samples_n = cfg["band_minutes"] // cfg["sample_every_min"]
    scenarios = []
    demand_name = cfg["demand_name"]
    dname = [dstops[s]["name"] for s in did]
    bname = [bus_stops[s]["name"] for s in area_bus]
    cell_P = [(np.array([dindex[s] for s in cd], dtype=np.int64),
               np.array(list(cd.values()), dtype=np.float64)) for cd in cell_dem]
    cell_B = [(np.array([bindex[s] for s in cb], dtype=np.int64),
               np.array(list(cb.values()), dtype=np.float64)) for cb in cell_bus]

    for day, date in dates.items():
        conns = []
        for fi, z in enumerate(feeds):
            conns.extend(gtfs.load_connections(z, date, f"{fi}:"))
        conns.sort(key=lambda c: -c[2])
        rules = flex.rules_on(date)
        rule_arr = [(np.array([s in r[0] for s in did]), np.array([s in r[1] for s in did]), r[2], r[3])
                    for r in rules]
        log(f"{day}: 運行区間 {len(conns):,} / デマンド運行 {[(r[2], r[3]) for r in rules]}")

        for di, d in enumerate(dests):
            dw = dest_walk[di]
            prof = profile_search(conns, trip_route, dw["egress"], footpaths, speed)
            for band in [b for b in cfg["bands"] if b["day"] == day]:
                t_start = int(gtfs.hms_to_min(band["start"]))
                g0, g1 = t_start, t_start + cfg["band_minutes"] + 300
                G, GW, GR = to_grid(prof, area_bus, g0, g1)
                T = g1 - g0
                taus = np.arange(g0, g1, dtype=np.float64)

                # 降車地点 q に時刻 τ に着いた後、目的地へ着く時刻（徒歩 or 路線バス）
                E = np.full((nd, T), np.inf)
                EW = np.zeros((nd, T))
                EB = np.full((nd, T), -1, dtype=np.int64)   # 乗り継ぐバス停（-1 は徒歩）
                ER = np.full((nd, T), -1, dtype=np.int64)
                for qi, s in enumerate(did):
                    if s in dw["dem"]:
                        E[qi] = taus + dw["dem"][s] / speed
                        EW[qi] = dw["dem"][s]
                    for b, wqb in dem_bus[s].items():
                        bi = bindex[b]
                        sh = int(math.ceil(wqb / speed))
                        if sh >= T:
                            continue
                        cand = np.full(T, np.inf)
                        cand[:T - sh] = G[bi, sh:]
                        better = cand < E[qi]
                        E[qi, better] = cand[better]
                        EW[qi, better] = wqb + GW[bi, sh:][better[:T - sh]]
                        EB[qi, better] = bi
                        ER[qi, better] = GR[bi, sh:][better[:T - sh]]

                res = {k: [] for k in ("b", "i", "bw", "iw", "bm", "im", "bl", "il")}
                for ci in range(len(cells)):
                    Bi, Bw = cell_B[ci]
                    Pi, Pw = cell_P[ci]
                    direct = dw["cell"][ci]
                    rows = []
                    for k in range(samples_n):
                        t = t_start + k * cfg["sample_every_min"]
                        # 固定路線＋徒歩
                        bt, bwk, bm, blab = direct / speed, direct, 0, "徒歩のみ"
                        if len(Bi):
                            idx = t + np.ceil(Bw / speed).astype(np.int64) - g0
                            ok = idx < T
                            if ok.any():
                                arr = np.where(ok, G[Bi, np.minimum(idx, T - 1)], np.inf)
                                j = int(np.argmin(arr))
                                if arr[j] - t < bt:
                                    ii = idx[j]
                                    bt, bwk, bm = arr[j] - t, Bw[j] + GW[Bi[j], ii], 1
                                    blab = f"{bname[Bi[j]]}から{route_labels[GR[Bi[j], ii]]}"
                        it, iwk, im, ilab = bt, bwk, bm, blab
                        # デマンド込み
                        if len(Pi):
                            pick = t + Pw / speed + margin
                            for pmask, qmask, ws, we in rule_arr:
                                sel = pmask[Pi]
                                if not sel.any():
                                    continue
                                pk = np.maximum(pick[sel], ws)
                                D = pk[:, None] + R[Pi[sel]]
                                valid = qmask[None, :] & np.isfinite(D) & (D <= we)
                                if not valid.any():
                                    continue
                                di_ = np.clip(np.ceil(np.where(valid, D, g0)).astype(np.int64) - g0, 0, T - 1)
                                arr = np.where(valid, E[np.arange(nd)[None, :], di_], np.inf)
                                flat = int(np.argmin(arr))
                                if arr.flat[flat] - t < it - 1e-9:
                                    pr, q = divmod(flat, nd)
                                    pidx = Pi[sel][pr]
                                    ii = di_[pr, q]
                                    it = arr.flat[flat] - t
                                    iwk = Pw[sel][pr] + EW[q, ii]
                                    if EB[q, ii] >= 0:
                                        im = 3
                                        ilab = (f"{dname[pidx]}から{demand_name}で{dname[q]}へ"
                                                f"→{bname[EB[q, ii]]}から{route_labels[ER[q, ii]]}")
                                    else:
                                        im = 2
                                        ilab = f"{dname[pidx]}から{demand_name}で{dname[q]}へ"
                        rows.append((bt, it, bwk, iwk, bm, im, blab, ilab))
                    bmode = Counter(r[4] for r in rows).most_common(1)[0][0]
                    imode = Counter(r[5] for r in rows).most_common(1)[0][0]
                    res["b"].append(round(sum(r[0] for r in rows) / len(rows), 1))
                    res["i"].append(round(sum(r[1] for r in rows) / len(rows), 1))
                    res["bw"].append(int(round(sum(r[2] for r in rows) / len(rows), -1)))
                    res["iw"].append(int(round(sum(r[3] for r in rows) / len(rows), -1)))
                    res["bm"].append(bmode)
                    res["im"].append(imode)
                    res["bl"].append(next(r[6] for r in rows if r[4] == bmode))
                    res["il"].append(next(r[7] for r in rows if r[5] == imode))

                saved = [b - i for b, i in zip(res["b"], res["i"])]
                summary = {
                    "cells": len(cells),
                    "share_saved_10": round(sum(s >= 10 for s in saved) / len(saved), 3),
                    "share_saved_5": round(sum(s >= 5 for s in saved) / len(saved), 3),
                    "median_saved": round(float(np.median(saved)), 1),
                    "max_saved": round(max(saved), 1),
                    "mean_base": round(float(np.mean(res["b"])), 1),
                    "mean_incl": round(float(np.mean(res["i"])), 1),
                }
                sid = f"{d['id']}_{band['id']}"
                with open(os.path.join(out_dir, "scenarios", f"{sid}.json"), "w", encoding="utf-8") as f:
                    json.dump({"id": sid, "dest": d["id"], "band": band["id"], "summary": summary, **res},
                              f, ensure_ascii=False, separators=(",", ":"))
                scenarios.append({"id": sid, "dest": d["id"], "band": band["id"], "summary": summary})
                log(f"  {d['label']} / {band['label']}: 10分以上短縮 {summary['share_saved_10']:.0%}"
                    f"、中央値 {summary['median_saved']}分")

    # --- 出力 ---
    with open(os.path.join(out_dir, "cells.json"), "w", encoding="utf-8") as f:
        json.dump({"dlat": 1 / 480, "dlon": 1 / 320,
                   "cells": [[c["code"], round(c["i"] / 480, 7), round(100 + c["j"] / 320, 7)] for c in cells]},
                  f, separators=(",", ":"))
    with open(os.path.join(out_dir, "stops.json"), "w", encoding="utf-8") as f:
        json.dump({
            "demand": [[dstops[s]["name"], round(dstops[s]["lat"], 6), round(dstops[s]["lon"], 6)] for s in did],
            "bus": sorted({(bus_stops[s]["name"], round(bus_stops[s]["lat"], 5), round(bus_stops[s]["lon"], 5))
                           for s in area_bus}),
        }, f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(out_dir, "boundary.json"), "w", encoding="utf-8") as f:
        geom = boundary["geometry"] if boundary else None
        if geom:
            rnd = lambda ring: [[round(x, 5), round(y, 5)] for x, y in ring]  # noqa: E731
            polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
            geom = {"type": "MultiPolygon", "coordinates": [[rnd(r) for r in poly] for poly in polys]}
        json.dump({"type": "Feature", "properties": {}, "geometry": geom}, f, separators=(",", ":"))

    flex_info = gtfs.read_table(p(cfg["flex_zip"]), "feed_info.txt")
    rules_text = {}
    for day, date in dates.items():
        rules_text[day] = [f"{int(r[2]) // 60}:{int(r[2]) % 60:02d}〜{int(r[3]) // 60}:{int(r[3]) % 60:02d}"
                           for r in flex.rules_on(date)]
    meta = {
        "generated": dt.date.today().isoformat(),
        "area_name": cfg["area_name"],
        "demand_name": demand_name,
        "demand_operator": flex_agency[0]["agency_name"] if flex_agency else "",
        "dates": {k: v.isoformat() for k, v in dates.items()},
        "demand_hours": {k: sorted(set(v)) for k, v in rules_text.items()},
        "booking": flex.booking_text(),
        "has_locations_geojson": flex.has_locations_geojson,
        "demand_points": len(did),
        "walk": walk_method,
        "boundary": ({"name": boundary["name"], "source": f"OpenStreetMap {boundary['osm']}"}
                     if boundary else None),
        "params": {
            "walk_speed_m_per_min": speed,
            "max_access_walk_m": access,
            "max_transfer_walk_m": cfg["max_transfer_walk_m"],
            "demand_pickup_margin_min": margin,
            "band_minutes": cfg["band_minutes"],
            "sample_every_min": cfg["sample_every_min"],
            "mesh": "1/4地域メッシュ（約250m）",
        },
        "ride_model": {
            "intercept": round(ride.intercept, 2), "slope_per_km": round(ride.slope, 2),
            "r2": round(ride.r2, 2), "records": ride.n_records,
            "period": [ride.period[0], ride.period[1]], "od_with_median": len(ride.median),
        },
        "flex_feed_version": flex_info[0].get("feed_version", "") if flex_info else "",
        "bus_agencies": sorted(set(agencies)),
        "sources": cfg["sources"],
        "saving_classes": SAVING_CLASSES,
        "destinations": [{"id": d["id"], "label": d["label"], "lat": d["lat"], "lon": d["lon"]} for d in dests],
        "bands": cfg["bands"],
        "scenarios": scenarios,
        # 初期表示範囲は対象メッシュ全体
        "bounds": [round(min(c["lon"] for c in cells) - 0.003, 4), round(min(c["lat"] for c in cells) - 0.003, 4),
                   round(max(c["lon"] for c in cells) + 0.003, 4), round(max(c["lat"] for c in cells) + 0.003, 4)],
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    log(f"完了: {out_dir}")


if __name__ == "__main__":
    main()
