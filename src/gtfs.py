"""GTFS / GTFS-Flex の読み込み（zipを直接読む）。"""
import csv
import datetime as dt
import io
import zipfile


def read_table(zip_path, name):
    """zip内のCSVを辞書のリストで返す。無ければ空リスト。"""
    with zipfile.ZipFile(zip_path) as z:
        names = {n.split("/")[-1]: n for n in z.namelist()}
        if name not in names:
            return []
        with z.open(names[name]) as f:
            text = io.TextIOWrapper(f, encoding="utf-8-sig")
            return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                    for row in csv.DictReader(text)]


def has_file(zip_path, name):
    with zipfile.ZipFile(zip_path) as z:
        return any(n.split("/")[-1] == name for n in z.namelist())


def hms_to_min(s):
    """'8:30:00' / '08:30' → 分。24時超えもそのまま。"""
    p = s.split(":")
    return int(p[0]) * 60 + int(p[1]) + (int(p[2]) / 60 if len(p) > 2 else 0)


def parse_date(s):
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def active_services(zip_path, date):
    """指定日に運行する service_id の集合（calendar + calendar_dates）。"""
    ymd = date.strftime("%Y%m%d")
    active = set()
    for r in read_table(zip_path, "calendar.txt"):
        if r["start_date"] <= ymd <= r["end_date"] and r[WEEKDAYS[date.weekday()]] == "1":
            active.add(r["service_id"])
    for r in read_table(zip_path, "calendar_dates.txt"):
        if r["date"] == ymd:
            if r["exception_type"] == "1":
                active.add(r["service_id"])
            elif r["exception_type"] == "2":
                active.discard(r["service_id"])
    return active


def feed_period(zip_path):
    """feed_info か calendar から有効期間 (start, end) を推定。"""
    info = read_table(zip_path, "feed_info.txt")
    if info and info[0].get("feed_start_date") and info[0].get("feed_end_date"):
        return parse_date(info[0]["feed_start_date"]), parse_date(info[0]["feed_end_date"])
    cal = read_table(zip_path, "calendar.txt")
    return (parse_date(min(r["start_date"] for r in cal)),
            parse_date(max(r["end_date"] for r in cal)))


def load_stops(zip_path, prefix=""):
    """{stop_id: {name, lat, lon}}。複数フィードの衝突を避けるため prefix を付ける。"""
    out = {}
    for r in read_table(zip_path, "stops.txt"):
        if not r.get("stop_lat") or r.get("location_type") not in ("", "0", None):
            continue
        out[prefix + r["stop_id"]] = {
            "name": r["stop_name"], "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"]),
        }
    return out


def load_connections(zip_path, date, prefix=""):
    """指定日の運行区間 (dep_stop, arr_stop, dep_min, arr_min, trip_key) のリスト。"""
    services = active_services(zip_path, date)
    trips = {r["trip_id"]: r for r in read_table(zip_path, "trips.txt")
             if r["service_id"] in services}
    by_trip = {}
    for r in read_table(zip_path, "stop_times.txt"):
        if r["trip_id"] not in trips:
            continue
        t_arr = r.get("arrival_time") or r.get("departure_time")
        t_dep = r.get("departure_time") or r.get("arrival_time")
        if not t_arr or not t_dep:
            continue  # 時刻なし（中間補間なし）の停留所は飛ばす
        by_trip.setdefault(r["trip_id"], []).append(
            (int(r["stop_sequence"]), prefix + r["stop_id"], hms_to_min(t_arr), hms_to_min(t_dep),
             r.get("pickup_type", "0"), r.get("drop_off_type", "0")))
    conns = []
    for tid, sts in by_trip.items():
        sts.sort()
        key = prefix + tid
        # (出発停留所, 到着停留所, 発, 着, 便, 出発側で乗車可, 到着側で降車可)
        for a, b in zip(sts, sts[1:]):
            conns.append((a[1], b[1], a[3], b[2], key, a[4] != "1", b[5] != "1"))
    return conns
