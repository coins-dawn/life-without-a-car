/* デマンド交通 効果マップ — src/build.py が書き出した docs/data/*.json を表示する */
(() => {
  "use strict";

  const COLORS = ["--s0", "--s1", "--s2", "--s3", "--s4", "--s5"].map((v) =>
    getComputedStyle(document.documentElement).getPropertyValue(v).trim());
  const MODE_BASE = { 0: "徒歩のみ", 1: "路線バス＋徒歩" };

  const state = { meta: null, cells: null, stops: null, scen: {}, dest: null, band: null, sel: null };
  const $ = (id) => document.getElementById(id);
  const fmtM = (m) => `${Math.round(m).toLocaleString("ja-JP")}m`;
  const fmtMin = (x) => (x >= 120 ? `${Math.floor(x / 60)}時間${Math.round(x % 60)}分` : `${Math.round(x)}分`);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function getJSON(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }

  function classOf(saved) {
    const cls = state.meta.saving_classes; // [0,1,5,10,20,30]
    let k = 0;
    for (let i = 1; i < cls.length; i++) if (saved >= cls[i]) k = i;
    return k;
  }

  // ---------- URL に状態を残す（広報資料からのリンク用） ----------
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    return { dest: h.get("dest"), band: h.get("band") };
  }
  function writeHash() {
    history.replaceState(null, "", `#dest=${state.dest}&band=${state.band}`);
  }

  // ---------- UI ----------
  function renderChips() {
    const mk = (box, items, cur, onPick) => {
      box.innerHTML = "";
      for (const it of items) {
        const b = document.createElement("button");
        b.className = "chip";
        b.type = "button";
        b.textContent = it.label;
        b.setAttribute("aria-pressed", String(it.id === cur));
        b.addEventListener("click", () => onPick(it.id));
        box.appendChild(b);
      }
    };
    mk($("dest-chips"), state.meta.destinations, state.dest, (id) => { state.dest = id; update(); });
    mk($("band-chips"), state.meta.bands, state.band, (id) => { state.band = id; update(); });
  }

  function renderLegend() {
    const cls = state.meta.saving_classes;
    const labels = cls.map((c, i) => (i === 0 ? "変化なし" : i === cls.length - 1 ? `${c}分以上` : `${c}〜${cls[i + 1]}分`));
    $("legend-scale").innerHTML = labels.map((l, i) =>
      `<div class="legend-step"><i style="background:${COLORS[i]}"></i><span>${l}</span></div>`).join("");
  }

  function renderSummary(sc) {
    const d = state.meta.destinations.find((x) => x.id === state.dest);
    const b = state.meta.bands.find((x) => x.id === state.band);
    const s = sc.summary;
    $("summary").innerHTML =
      `${esc(b.label)}に<b>${esc(d.label)}</b>へ向かうと、対象エリアの <strong>${Math.round(s.share_saved_10 * 100)}%</strong> で10分以上短縮` +
      `（平均 ${fmtMin(s.mean_base)} → ${fmtMin(s.mean_incl)}）`;
  }

  function renderSheet() {
    const k = state.sel;
    if (k == null) { $("sheet").hidden = true; return; }
    const sc = state.scen[`${state.dest}_${state.band}`];
    const d = state.meta.destinations.find((x) => x.id === state.dest);
    const band = state.meta.bands.find((x) => x.id === state.band);
    const saved = sc.b[k] - sc.i[k];
    const walkDiff = sc.bw[k] - sc.iw[k];
    const name = state.meta.demand_name;
    let head;
    if (saved >= 1) {
      const walk = walkDiff >= 10 ? `歩く距離 ${fmtM(walkDiff)}減` : walkDiff <= -10 ? `歩く距離 ${fmtM(-walkDiff)}増` : "歩く距離はほぼ同じ";
      head = `<div class="big"><span class="saved">${fmtMin(saved)}短縮</span><span class="walk">${walk}</span></div>`;
    } else {
      head = `<div class="big"><span class="flat">この場所は、いつもの方法とほぼ同じです</span></div>`;
    }
    const usesDemand = sc.im[k] >= 2;
    $("sheet-body").innerHTML = `
      <h3>この場所から <b>${esc(d.label)}</b> まで（${esc(band.label)}に出発）</h3>
      ${head}
      <div class="cmp">
        <div class="cmp-row">
          <div class="cmp-head"><span>いつもの方法（${MODE_BASE[sc.bm[k]]}）</span></div>
          <div class="cmp-val">${fmtMin(sc.b[k])}<small>徒歩 ${fmtM(sc.bw[k])}</small></div>
          <div class="cmp-route">${sc.bm[k] === 0 ? "バス停が遠い、またはバスより歩く方が早い" : esc(sc.bl[k])}</div>
        </div>
        <div class="cmp-row ${usesDemand ? "with" : ""}">
          <div class="cmp-head"><span>${esc(name)}も使うと</span></div>
          <div class="cmp-val">${fmtMin(sc.i[k])}<small>徒歩 ${fmtM(sc.iw[k])}</small></div>
          <div class="cmp-route">${usesDemand ? esc(sc.il[k]) : `${esc(name)}を使わない方法が最も早い`}</div>
        </div>
      </div>
      <p class="foot">所要時間は${band.label.replace(/^.*?(\d+時台)/, "$1")}の間に5分おきに出発した場合の平均（待ち時間を含む）。
      ${esc(name)}は予約が必要です（${esc(state.meta.booking)}）。目安の値です。</p>`;
    $("sheet").hidden = false;
  }

  // ---------- 地図 ----------
  let map;

  function cellsGeoJSON(sc) {
    const { dlat, dlon, cells } = state.cells;
    return {
      type: "FeatureCollection",
      features: cells.map(([code, la, lo], k) => {
        const saved = sc ? sc.b[k] - sc.i[k] : 0;
        return {
          type: "Feature", id: k,
          properties: { k, code, c: classOf(saved) },
          geometry: { type: "Polygon", coordinates: [[[lo, la], [lo + dlon, la], [lo + dlon, la + dlat], [lo, la + dlat], [lo, la]]] },
        };
      }),
    };
  }

  function pointsGeoJSON(rows) {
    return { type: "FeatureCollection", features: rows.map(([n, la, lo]) => ({ type: "Feature", properties: { n }, geometry: { type: "Point", coordinates: [lo, la] } })) };
  }

  let destMarker;
  function setDestMarker() {
    const d = state.meta.destinations.find((x) => x.id === state.dest);
    if (destMarker) destMarker.remove();
    const el = document.createElement("div");
    el.className = "dest-marker";
    el.innerHTML = `<div class="pin"></div><div class="lbl">${esc(d.label)}</div>`;
    destMarker = new maplibregl.Marker({ element: el, anchor: "top" }).setLngLat([d.lon, d.lat]).addTo(map);
  }

  function initMap() {
    const b = state.meta.bounds;
    map = new maplibregl.Map({
      container: "map",
      style: {
        version: 8,
        sources: {
          gsi: {
            type: "raster",
            tiles: ["https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"],
            tileSize: 256, minzoom: 5, maxzoom: 18,
            attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a> | 道路網 © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> | 交通データ <a href="https://www.odpt.org/" target="_blank" rel="noopener">ODPT</a>',
          },
        },
        layers: [{ id: "gsi", type: "raster", source: "gsi" }],
      },
      bounds: [[b[0], b[1]], [b[2], b[3]]],
      fitBoundsOptions: { padding: 20 },
      maxZoom: 17, minZoom: 9,
      attributionControl: { compact: true },
      dragRotate: false, pitchWithRotate: false,
    });
    if (new URLSearchParams(location.search).has("debug")) window.__map = map;
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true } }), "top-right");

    map.on("load", () => {
      // 帰属表示は折りたたんだ状態で始める（凡例と重ならないように）
      const attrib = document.querySelector(".maplibregl-ctrl-attrib");
      if (attrib) attrib.classList.remove("maplibregl-compact-show");
      map.addSource("cells", { type: "geojson", data: cellsGeoJSON(null) });
      map.addLayer({
        id: "cells-fill", type: "fill", source: "cells",
        paint: {
          "fill-color": ["match", ["get", "c"], 0, COLORS[0], 1, COLORS[1], 2, COLORS[2], 3, COLORS[3], 4, COLORS[4], COLORS[5]],
          "fill-opacity": ["match", ["get", "c"], 0, 0.35, 0.72],
        },
      });
      map.addLayer({
        id: "cells-line", type: "line", source: "cells",
        paint: { "line-color": "#ffffff", "line-width": 0.6, "line-opacity": 0.6 },
      });
      map.addLayer({
        id: "cells-sel", type: "line", source: "cells",
        filter: ["==", ["get", "k"], -1],
        paint: { "line-color": "#e8590c", "line-width": 3 },
      });
      if (state.boundary && state.boundary.geometry) {
        map.addSource("boundary", { type: "geojson", data: state.boundary });
        map.addLayer({
          id: "boundary", type: "line", source: "boundary",
          paint: { "line-color": "#55534e", "line-width": 2, "line-dasharray": [3, 2] },
        });
      }
      map.addSource("bus", { type: "geojson", data: pointsGeoJSON(state.stops.bus) });
      map.addLayer({
        id: "bus", type: "circle", source: "bus", layout: { visibility: "none" },
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2, 15, 4], "circle-color": "#55534e", "circle-stroke-color": "#fff", "circle-stroke-width": 1 },
      });
      map.addSource("demand", { type: "geojson", data: pointsGeoJSON(state.stops.demand) });
      map.addLayer({
        id: "demand", type: "circle", source: "demand",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2.5, 15, 5],
          "circle-color": "#ffffff", "circle-stroke-color": "#e8590c",
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 11, 1.5, 15, 2.5],
        },
      });
      map.on("click", "cells-fill", (e) => {
        const f = e.features && e.features[0];
        if (!f) return;
        state.sel = f.properties.k;
        $("hint").style.opacity = "0";
        map.setFilter("cells-sel", ["==", ["get", "k"], state.sel]);
        renderSheet();
      });
      map.on("mouseenter", "cells-fill", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "cells-fill", () => { map.getCanvas().style.cursor = ""; });
      update();
    });

    $("tg-demand").addEventListener("change", (e) => map.setLayoutProperty("demand", "visibility", e.target.checked ? "visible" : "none"));
    $("tg-bus").addEventListener("change", (e) => map.setLayoutProperty("bus", "visibility", e.target.checked ? "visible" : "none"));
  }

  async function update() {
    renderChips();
    writeHash();
    const id = `${state.dest}_${state.band}`;
    if (!state.scen[id]) state.scen[id] = await getJSON(`data/scenarios/${id}.json`);
    const sc = state.scen[id];
    if (map && map.getSource("cells")) {
      map.getSource("cells").setData(cellsGeoJSON(sc));
      setDestMarker();
    }
    renderSummary(sc);
    renderSheet();
  }

  // ---------- 説明・出典 ----------
  function renderInfo() {
    const m = state.meta;
    const p = m.params;
    const rm = m.ride_model;
    const per = (s) => `${s.slice(0, 4)}年${+s.slice(4, 6)}月`;
    const dayName = { weekday: "平日", sunday: "日曜" };
    const dates = Object.entries(m.dates).map(([k, v]) => `${dayName[k] || k}は ${v}`).join("、");
    const hours = Object.entries(m.demand_hours).map(([k, v]) => `${dayName[k] || k} ${v.join("・")}`).join("／");
    $("info-body").innerHTML = `
      <p>${esc(m.area_name)}で、デマンド交通「${esc(m.demand_name)}」を路線バスや徒歩と組み合わせると、
      目的地までの所要時間がどれだけ短くなるかを約250m四方のマス目ごとに示した地図です。
      色が濃いほど、${esc(m.demand_name)}を使うことで早く着けます。</p>

      <h4>比べているもの</h4>
      <ul>
        <li><b>いつもの方法</b>：徒歩、または 徒歩＋路線バス（${esc(m.bus_agencies.join("・"))}）のうち最も早い方法</li>
        <li><b>${esc(m.demand_name)}も使うと</b>：上の方法に加え、${esc(m.demand_name)}だけ、または ${esc(m.demand_name)}で近くのバス停まで行き路線バスに乗り継ぐ方法も含めて、最も早い方法</li>
      </ul>

      <h4>計算方法</h4>
      <ul>
        <li>選んだ時間帯（${p.band_minutes}分間）に${p.sample_every_min}分おきに出発した場合の所要時間を平均しています（バスや乗車の待ち時間を含む）。</li>
        <li>路線バスは各社GTFSの時刻表を使用。代表日は ${esc(dates)}。乗り換えは${p.max_transfer_walk_m}m以内の停留所間で可能としています。</li>
        <li>徒歩は分速${p.walk_speed_m_per_min}m。バス停・乗降地点までは${fmtM(p.max_access_walk_m)}まで歩くものとしています。</li>
        <li><b>徒歩の距離：${esc(m.walk.text)}</b>。</li>
        <li>${esc(m.demand_name)}は町内の乗降地点${m.demand_points}か所の間で乗車可能（運行 ${esc(hours)}）。乗車場所には${p.demand_pickup_margin_min}分前に着く想定です。</li>
        <li>乗車時間は運行実績（${per(rm.period[0])}〜${per(rm.period[1])}、${rm.records.toLocaleString()}件）から推定。実績が3件以上ある区間は実績の中央値、それ以外は「${rm.intercept}分 ＋ 道のり1kmあたり${rm.slope_per_km}分」（相乗りの寄り道を含む）で計算しています。</li>
        <li>予約（${esc(m.booking)}）にかかる時間、運賃、渋滞は含みません。乗合のため実際の時間は前後します。</li>
        <li>対象は、${m.boundary ? `${esc(m.boundary.name)}内（境界は${esc(m.boundary.source)}）で、` : ""}乗降地点まで道のり1.2km以内のマス目です（地図の点線が境界）。</li>
      </ul>

      <h4>出典・ライセンス</h4>
      <ul class="src">
        ${m.sources.map((s) => `<li>「${esc(s.name)}」${esc(s.holder)}（${esc(s.via)}、<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.license)}</a>）${s.note ? `<br><small>${esc(s.note)}</small>` : ""}</li>`).join("")}
        <li>道路網：© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>（ODbL）</li>
        <li>背景地図：<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル（淡色地図）</a>（国土地理院）</li>
      </ul>
      <p class="src">本マップは上記のデータを加工して作成したもので、各データ提供者が内容を保証するものではありません。
      所要時間は計算による目安です。データ作成日：${esc(m.generated)}</p>`;
  }

  function bindDialogs() {
    const info = $("info");
    const open = () => { info.hidden = false; $("info-close").focus(); };
    const close = () => { info.hidden = true; $("info-btn").focus(); };
    $("info-btn").addEventListener("click", open);
    $("info-close").addEventListener("click", close);
    info.addEventListener("click", (e) => { if (e.target === info) close(); });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!info.hidden) close();
      else if (!$("sheet").hidden) closeSheet();
    });
    const closeSheet = () => {
      state.sel = null;
      $("sheet").hidden = true;
      if (map && map.getLayer("cells-sel")) map.setFilter("cells-sel", ["==", ["get", "k"], -1]);
    };
    $("sheet-close").addEventListener("click", closeSheet);
  }

  async function main() {
    try {
      const [meta, cells, stops, boundary] = await Promise.all([
        getJSON("data/meta.json"), getJSON("data/cells.json"), getJSON("data/stops.json"),
        getJSON("data/boundary.json").catch(() => null),
      ]);
      Object.assign(state, { meta, cells, stops, boundary });
    } catch (e) {
      $("summary").textContent = "データを読み込めませんでした。src/build.py を実行して docs/data/ を作成してください。";
      throw e;
    }
    const m = state.meta;
    document.title = `${m.demand_name} 便利マップ`;
    $("subtitle").textContent = `${m.area_name} デマンド交通「${m.demand_name}」の効果マップ`;
    document.querySelector("h1").textContent = `${m.demand_name}で、もっと近くなる`;
    const h = readHash();
    state.dest = m.destinations.some((d) => d.id === h.dest) ? h.dest : m.destinations[0].id;
    state.band = m.bands.some((b) => b.id === h.band) ? h.band : m.bands[0].id;
    if (m.walk.type !== "osm") {
      $("approx-notice").hidden = false;
      $("approx-notice").textContent = `注意：${m.walk.text}しています`;
    }
    renderLegend();
    renderInfo();
    bindDialogs();
    renderChips();
    initMap();
  }

  main();
})();
