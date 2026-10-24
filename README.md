# たまGO 便利マップ（デマンド交通の効果マップ）

群馬県玉村町のデマンドタクシー「たまGO」を路線バス・徒歩と組み合わせると、
目的地までの所要時間がどれだけ短くなるかを、約250m四方のマス目ごとに色分けして示す静的ウェブアプリです。

- 市民向け：地図をタップすると「◯分短縮・歩く距離◯m減」と、使う乗降地点・バス路線が表示されます
- 事業者向け：公開データ（GTFS-Flex・運行実績・GTFS）からこの地図を作れることを示す広報材料になります

サーバーは不要です。所要時間は `src/` の Python で事前に計算して `docs/data/` に JSON を書き出し、
`docs/` のページ（MapLibre GL JS）がそれを読み込んで表示します。

## 構成

```
data/raw/flex/     デマンド交通の GTFS-Flex（たまGO）
data/raw/demand/   デマンド交通の運行実績（需要情報）
data/raw/gtfs/     路線バスの GTFS（永井バス・群馬中央バス・群馬バス）
data/cache/        OpenStreetMap の道路網・町の境界のキャッシュ（git 管理外）
src/config.json    目的地・時間帯・計算条件・出典の設定
src/build.py       計算本体（python src/build.py）
src/download_data.py  ODPT から最新データを取得
docs/              公開するウェブアプリ（index.html / app.js / style.css / data/）
```

## データを更新するとき

Python 3.10 以上と numpy が必要です。

```sh
pip install -r requirements.txt

# 1. 最新データを ODPT から取得（手で data/raw/ に置き直してもよい）
python src/download_data.py

# 2. 計算して docs/data/ を作り直す（数分）
python src/build.py

# 3. 確認（http://localhost:8000 を開く）
python -m http.server 8000 -d docs

# 4. docs/ をコミットして push すると GitHub Pages に反映されます
```

- 道路網は初回のみ OpenStreetMap から取得し、`data/cache/` に保存します。
  道路網を取り直したいときは `data/cache/` を削除してから実行してください。
- 時刻表の代表日は、実行日以降で全データの有効期間に入る最初の水曜日（平日）・日曜日を自動で選びます。
  固定したい場合は `config.json` の `service_dates` に `"2026-11-11"` のように書きます。
- 目的地・時間帯は `config.json` の `destinations`・`bands` で変更できます。
  目的地は停留所名（たまGOの乗降地点名、または `"feed": "gtfs"` を付けるとバス停名）か `lat`/`lon` で指定します。
- 別の地域に使うときは、`config.json` の入力ファイル・目的地・`sources`（出典表記）・`area_name`・`demand_name` を書き換えます。

## GitHub Pages での公開

リポジトリの Settings → Pages で、Source を「Deploy from a branch」、Branch を `main` の `/docs` にします。
`docs/.nojekyll` を置いているので、そのまま配信されます。

## 計算方法

| 項目 | 内容 |
|---|---|
| メッシュ | 1/4地域メッシュ（約250m）。町の境界（OpenStreetMap）内で、たまGOの乗降地点まで道のり1.2km以内のマス目 |
| 出発時刻 | 各時間帯の60分間に5分おきに出発した12通りの平均（待ち時間を含む） |
| 徒歩 | 分速80m。OpenStreetMap の道路網上の最短経路。取得できない場合は直線距離×1.3 で近似し、その旨を画面に表示 |
| いつもの方法 | 徒歩のみ、または 徒歩＋路線バス（乗り換え可）の最速。路線バスは GTFS の代表日ダイヤで経路探索（Connection Scan Algorithm） |
| たまGO込み | 上記に加え「徒歩→たまGO→徒歩」「徒歩→たまGO→路線バス→徒歩」を含めた最速 |
| たまGOの乗車 | 乗降地点まで最大1.5km徒歩、乗車5分前に到着。運行時間帯（GTFS-Flex の予約可能時間）内のみ |
| たまGOの乗車時間 | 運行実績で3件以上ある区間は実績の中央値。それ以外は実績から回帰した「切片＋傾き×道のりkm」（相乗りの寄り道込み） |
| 含まないもの | 予約にかかる時間、運賃、渋滞、車両の空き状況 |

実際の運行は乗合のため、所要時間は前後します。地図の値は目安です。

## 出典・ライセンス

- 「玉村町 デマンドタクシー『たまGO』」GTFS-Flex・需要情報：玉村町（公共交通オープンデータセンター、CC BY 4.0）
  ※「公共交通オープンデータチャレンジ2026」期間中の限定公開データです
- 永井バス・群馬中央バス・群馬バス GTFS：各社（公共交通オープンデータセンター、CC BY 4.0）
- 道路網・町の境界：© OpenStreetMap contributors（ODbL）
- 背景地図：地理院タイル（国土地理院）

本アプリは上記データを加工して作成したものです。
