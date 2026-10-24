"""ODPT（公共交通オープンデータセンター）から最新の入力データを data/raw/ にダウンロードする。

使い方:  python src/download_data.py

config.json の "downloads" に「保存先: URL」を書く。URL の代わりに "ckan:<データセット名>" と書くと、
https://ckan.odpt.org/dataset/<データセット名> に載っている最新のリソース（先頭のもの）を取得する。
"""
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "life-without-a-car/1.0"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def resolve(src):
    if not src.startswith("ckan:"):
        return src
    name = src[5:]
    page = get(f"https://ckan.odpt.org/dataset/{name}").decode("utf-8")
    res = re.findall(r'href="(/dataset/[^"]+/resource/[0-9a-f-]+)"', page)
    if not res:
        raise SystemExit(f"{name}: リソースが見つかりません")
    rpage = get("https://ckan.odpt.org" + res[0]).decode("utf-8")
    m = re.search(r'resource-url-analytics" href="([^"]+)"', rpage)
    if not m:
        raise SystemExit(f"{name}: ダウンロードURLが見つかりません")
    return m.group(1).replace("&amp;", "&")


def main():
    cfg = json.load(open(os.path.join(ROOT, "src/config.json"), encoding="utf-8"))
    for dest, src in cfg["downloads"].items():
        url = resolve(src)
        data = get(url)
        path = os.path.join(ROOT, dest)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print(f"{dest}  ←  {url}  ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
