"""デマンド交通（GTFS-Flex の乗降地点グループ方式）と、運行実績からの乗車時間モデル。"""
import csv
import io
import statistics
import zipfile
from collections import defaultdict

from gtfs import active_services, has_file, hms_to_min, load_stops, read_table


class FlexService:
    def __init__(self, zip_path):
        self.zip = zip_path
        self.has_locations_geojson = has_file(zip_path, "locations.geojson")
        self.stops = load_stops(zip_path)
        # 名前に「廃止」を含む地点は使わない
        self.stops = {k: v for k, v in self.stops.items() if "廃止" not in v["name"]}
        self.groups = defaultdict(set)
        for r in read_table(zip_path, "location_group_stops.txt"):
            if r["stop_id"] in self.stops:
                self.groups[r["location_group_id"]].add(r["stop_id"])
        self.booking = read_table(zip_path, "booking_rules.txt")
        self.trips = {r["trip_id"]: r for r in read_table(zip_path, "trips.txt")}
        self.stop_times = defaultdict(list)
        for r in read_table(zip_path, "stop_times.txt"):
            self.stop_times[r["trip_id"]].append(r)

    def rules_on(self, date):
        """指定日の乗車ルール [(乗車地点集合, 降車地点集合, 開始分, 終了分), ...]。"""
        services = active_services(self.zip, date)
        rules = []
        for tid, sts in self.stop_times.items():
            if self.trips.get(tid, {}).get("service_id") not in services:
                continue
            sts = sorted(sts, key=lambda r: int(r["stop_sequence"]))
            for i, a in enumerate(sts):
                if a.get("pickup_type") == "1":
                    continue
                for b in sts[i + 1:]:
                    if b.get("drop_off_type") == "1":
                        continue
                    start = max(hms_to_min(a["start_pickup_drop_off_window"]),
                                hms_to_min(b["start_pickup_drop_off_window"]))
                    end = min(hms_to_min(a["end_pickup_drop_off_window"]),
                              hms_to_min(b["end_pickup_drop_off_window"]))
                    rules.append((self.groups[a["location_group_id"]],
                                  self.groups[b["location_group_id"]], start, end))
        return rules

    def booking_text(self):
        if not self.booking:
            return "予約条件の記載なし"
        b = self.booking[0]
        parts = []
        if b.get("prior_notice_start_day"):
            parts.append(f"{b['prior_notice_start_day']}日前から予約可")
        if b.get("prior_notice_duration_min") not in (None, "", "0"):
            parts.append(f"乗車{b['prior_notice_duration_min']}分前まで予約")
        elif b.get("prior_notice_duration_max"):
            parts.append(f"乗車{int(b['prior_notice_duration_max']) // 60}時間前から予約可")
        return "・".join(parts) or "当日予約"


def read_records(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith((".txt", ".csv")))
        with z.open(name) as f:
            return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))


class RideModel:
    """乗車時間 = 実績の中央値（十分な件数がある区間）／それ以外は 切片 + 傾き × 道のりkm。"""

    def __init__(self, records, road_m, min_samples):
        od = defaultdict(list)
        xs, ys = [], []
        dates = []
        for r in records:
            p, q = r["pickup_stop_id"], r["drop_off_stop_id"]
            t = hms_to_min(r["drop_off_time"]) - hms_to_min(r["pickup_time"])
            dates.append(r["pickup_date"])
            if p == q or t <= 0 or t > 120:
                continue
            od[(p, q)].append(t)
            d = road_m.get((p, q))
            if d is not None:
                xs.append(d / 1000)
                ys.append(t)
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        self.slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        self.intercept = my - self.slope * mx
        ss_res = sum((y - self.intercept - self.slope * x) ** 2 for x, y in zip(xs, ys))
        ss_tot = sum((y - my) ** 2 for y in ys)
        self.r2 = 1 - ss_res / ss_tot
        self.n_fit = n
        self.n_records = len(records)
        self.period = (min(dates), max(dates))
        self.median = {k: statistics.median(v) for k, v in od.items() if len(v) >= min_samples}
        self.road_m = road_m

    def minutes(self, p, q):
        if p == q:
            return None
        if (p, q) in self.median:
            return self.median[(p, q)]
        d = self.road_m.get((p, q))
        if d is None:
            return None
        return max(3.0, self.intercept + self.slope * d / 1000)
