/* Better Zivi — front end. Plain JS + Leaflet; talks to the local FastAPI server.
   Texts go through t() (i18n.js): French is the source, German and Italian come from there. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) { let m = r.statusText; try { m = fmtMsg((await r.json()).detail) || m; } catch { /* text */ } throw new Error(m); }
  return r;
}
/* the interface language rides along with every request that depends on it */
const withLang = (path) => `${path}${path.includes("?") ? "&" : "?"}lang=${LANG}`;

const JNM = (v) => ({ J: t("Oui"), N: t("Non"), M: t("Possible") }[v]);
const YN = (v) => ({ 1: t("Oui"), 0: t("Non") }[v]);
const LANG_NAMES = { de: "Allemand", fr: "Français", it: "Italien" }; // facet "Langue"
const PAGE = 50;
const nf = (n) => Number(n || 0).toLocaleString(LOCALE[LANG]);
const COLON = () => (LANG === "fr" ? " : " : ": "); // French puts a space before the colon

/* Sectors: the export labels them "2 Social", "5 Agriculture"…; the leading code picks the colour. */
const SECTOR_COLORS = {
  1: "#D6475B", // Santé
  2: "#3E7CC0", // Social
  3: "#9B6BC4", // Biens culturels
  4: "#3E9B4F", // Nature, environnement, forêt
  5: "#E0A419", // Agriculture
  6: "#1FA3A3", // Coopération au développement
  7: "#E5732B", // Catastrophes
  8: "#8C6A50", // Instruction publique
};
const OTHER_COLOR = "#7A8580";
const sectorCode = (label) => (String(label ?? "").match(/^\s*(\d+)/) || [])[1] || null;
const sectorColor = (code) => SECTOR_COLORS[code] || OTHER_COLOR;
const sectorName = (label) => dataLabel(String(label ?? "").replace(/^\s*\d+\s*/, "")) || t("Autre");

/* Rare traits come from the server in French ("Travail de nuit" / "Oui"): shown here in
   the interface language. The French value stays the filter key. */
const FIELD_LABELS = {
  sector: "Domaine", work_model: "Horaire", language: "Langue", canton: "Canton",
  weekend: "Travail le week-end", night: "Travail de nuit", housing: "Logement", meals: "Repas",
  reputation_check: "Contrôle de réputation", priority_program: "Programme prioritaire",
  abroad: "À l’étranger", min_weeks: "Durée minimale", hours_per_week: "Heures par semaine",
  task_areas: "Tâche",
};
const traitLabel = (field, fallback) => (FIELD_LABELS[field] ? t(FIELD_LABELS[field]) : fallback);
function traitValue(field, value) {
  if (["sector", "work_model", "task_areas"].includes(field)) return dataLabel(value);
  if (field === "min_weeks") { const n = parseInt(value, 10); return Number.isNaN(n) ? value : tn(n, "{n} semaine", "{n} semaines"); }
  if (field === "canton") return value;
  return t(value); // Oui / Non / Possible, language names, hour ranges
}

/* ------------------------------------------------------------------ state */
const blank = () => ({
  text: "", text_mode: "keyword", sort: "", ai_top_k: 200,
  period: { from: "", to: "", duration_weeks: "", mode: "any" },
  centers: [], radius: 25, bboxOnly: false,
  housing: null, meals: null, reputation_check: null, priority_program: null, abroad: null,
  weekend: [], night: [], no_course_required: false,
  min_weeks_min: "", min_weeks_max: "", hours_min: "", hours_max: "",
  task_areas: [], task_areas_mode: "any", canton: [], language: [], sector: [], work_model: [],
  favorites_only: false,
  view_tr: false, search_original: true, // translation options (Base de données → Traduction)
  traits: [], // rare traits clicked in the list: [{ field, value, label }]
});
let S = Object.assign(blank(), store.get("zivi.state", {}));
if (S.view_lang === "fr") S.view_tr = true; // older setting: "show in French"
delete S.view_lang;
let lastResult = null;
let offset = 0;
let bbox = null;

function query(extra = {}) {
  const q = {
    text: S.text, text_mode: S.text_mode, sort: S.sort || undefined, ai_top_k: S.ai_top_k,
    housing: S.housing, meals: S.meals, reputation_check: S.reputation_check,
    priority_program: S.priority_program, abroad: S.abroad,
    weekend: S.weekend, night: S.night, no_course_required: S.no_course_required,
    min_weeks_min: S.min_weeks_min, min_weeks_max: S.min_weeks_max,
    hours_min: S.hours_min, hours_max: S.hours_max,
    task_areas: S.task_areas, task_areas_mode: S.task_areas_mode,
    canton: S.canton, language: S.language, sector: S.sector, work_model: S.work_model,
    favorites_only: S.favorites_only,
    lang: LANG, view_lang: S.view_tr ? LANG : "orig", search_original: S.search_original,
    traits: S.traits.map(({ field, value }) => ({ field, value })),
    limit: PAGE, offset: 0,
  };
  const p = S.period;
  if (p.from || p.to) q.period = { from: p.from || null, to: p.to || null, mode: p.mode, duration_weeks: p.duration_weeks || null };
  if (S.centers.length) q.geo = { centers: S.centers };
  if (S.bboxOnly && bbox) q.bbox = bbox;
  return Object.assign(q, extra);
}

/* ------------------------------------------------------------------ map */
const map = L.map("map", { zoomControl: true, preferCanvas: false }).setView([46.8, 8.23], 8);
const swiss = L.tileLayer("https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg", {
  maxZoom: 18, attribution: '&copy; <a href="https://www.swisstopo.admin.ch/">swisstopo</a>',
});
const swissGrey = L.tileLayer("https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/{z}/{x}/{y}.jpeg", {
  maxZoom: 18, attribution: '&copy; <a href="https://www.swisstopo.admin.ch/">swisstopo</a>',
});
const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
});
swissGrey.addTo(map);
let layerControl = null;
function renderLayerControl() { // names in the interface language
  if (layerControl) layerControl.remove();
  layerControl = L.control.layers({ [t("Carte nationale (gris)")]: swissGrey, [t("Carte nationale (couleur)")]: swiss, OpenStreetMap: osm },
    null, { position: "topright" }).addTo(map);
}
const cluster = L.markerClusterGroup({
  showCoverageOnHover: false, maxClusterRadius: 40, spiderfyOnMaxZoom: true,
  iconCreateFunction: (c) => {
    const n = c.getChildCount(); const s = n < 10 ? 32 : n < 100 ? 40 : 48;
    const bySector = {};
    for (const m of c.getAllChildMarkers()) bySector[m.options.sector] = (bySector[m.options.sector] || 0) + 1;
    return L.divIcon({ html: `<div class="cluster" style="width:${s}px;height:${s}px;background:${pie(bySector, n)}"><span>${n}</span></div>`,
      className: "", iconSize: [s, s] });
  },
}).addTo(map);
/* conic-gradient with one slice per sector, in sector order so neighbouring clusters read alike */
function pie(bySector, total) {
  const codes = Object.keys(bySector).sort((a, b) => (+a || 99) - (+b || 99));
  if (codes.length === 1) return sectorColor(codes[0]);
  let at = 0;
  const stops = codes.map((k) => {
    const from = at; at += (360 * bySector[k]) / total;
    return `${sectorColor(k)} ${from.toFixed(2)}deg ${at.toFixed(2)}deg`;
  });
  return `conic-gradient(${stops.join(", ")})`;
}
/* Full-screen map: filters and list hidden, the map alone; the detail panel still opens
   over it when a point is clicked. The button sits under the zoom controls. */
const ICON_FULL = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ICON_EXIT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2.5V6H2.5M13.5 6H10V2.5M10 13.5V10h3.5M2.5 10H6v3.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const FullControl = L.Control.extend({
  options: { position: "topleft" },
  onAdd() {
    const b = L.DomUtil.create("button", "map-full-btn leaflet-bar");
    b.type = "button";
    L.DomEvent.disableClickPropagation(b);
    b.addEventListener("click", () => setMapFull(!document.body.classList.contains("map-full")));
    this.btn = b;
    return b;
  },
});
const fullControl = new FullControl().addTo(map);
/* Refocus: zoom the map to the entries currently shown (after panning, or new filters). */
const ICON_FIT = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="2.2" fill="currentColor"/><circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 .8v2.4M8 12.8v2.4M.8 8h2.4M12.8 8h2.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
const FitControl = L.Control.extend({
  options: { position: "topleft" },
  onAdd() {
    const b = L.DomUtil.create("button", "map-full-btn leaflet-bar");
    b.type = "button";
    b.innerHTML = ICON_FIT;
    L.DomEvent.disableClickPropagation(b);
    b.addEventListener("click", fitToResults);
    this.btn = b;
    return b;
  },
});
const fitControl = new FitControl().addTo(map);
function fitToResults() {
  if (!cluster.getLayers().length) return;
  map.fitBounds(cluster.getBounds(), { padding: [30, 30], maxZoom: 13 });
}
function renderFitBtn() {
  const label = t("Recentrer sur les affectations affichées");
  fitControl.btn.title = label;
  fitControl.btn.setAttribute("aria-label", label);
  fitControl.btn.disabled = !cluster.getLayers().length;
}
function renderFullBtn() {
  const on = document.body.classList.contains("map-full");
  const label = on ? t("Quitter la carte en plein écran") : t("Carte en plein écran");
  fullControl.btn.innerHTML = on ? ICON_EXIT : ICON_FULL;
  fullControl.btn.title = label;
  fullControl.btn.setAttribute("aria-label", label);
  fullControl.btn.setAttribute("aria-pressed", String(on));
}
function setMapFull(on) {
  document.body.classList.toggle("map-full", on);
  store.set("zivi.mapFull", on);
  renderFullBtn();
  setTimeout(() => map.invalidateSize(), 60);
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("map-full") && $("#drawer").hidden
      && !document.querySelector("dialog[open]") && $("#adv").hidden && $("#langList").hidden) setMapFull(false);
});
const circles = L.layerGroup().addTo(map);
const dotIcons = {};
const dotIcon = (code, fav) => (dotIcons[`${code}|${fav}`] ||= L.divIcon({
  className: "", iconSize: [14, 14],
  html: `<div class="dot${fav ? " fav" : ""}" style="--c:${sectorColor(code)}"></div>`,
}));
const markersById = new Map();
const favs = new Set();
let fitted = false;

function drawPoints(points) {
  cluster.clearLayers(); markersById.clear();
  const markers = points.map(([id, lat, lon, title, employer, place, sector, fav]) => {
    if (fav) favs.add(id); else favs.delete(id);
    const m = L.marker([lat, lon], { icon: dotIcon(sector, fav), title, sector: sector || "x" });
    m.bindPopup(() => `<h4>${esc(title)}</h4><div class="who">${esc(employer)}<br>${esc(place || "")}</div>
      <p class="pop-actions"><button class="btn primary" data-open="${id}">${t("Détails")}</button> ${favBtn(id)}</p>`);
    if (!markersById.has(id)) markersById.set(id, []);
    markersById.get(id).push(m);
    return m;
  });
  cluster.addLayers(markers);
  renderFitBtn();
  if (!fitted && markers.length) { map.fitBounds(cluster.getBounds(), { padding: [20, 20], maxZoom: 11 }); fitted = true; }
}
map.on("popupopen", (e) => {
  const b = e.popup.getElement().querySelector("[data-open]");
  if (b) b.addEventListener("click", () => openDetail(+b.dataset.open));
});
function highlight(id, on) {
  for (const m of markersById.get(id) || []) {
    const el = m.getElement(); if (el) el.firstChild.classList.toggle("hl", on);
  }
}
function drawCircles() {
  circles.clearLayers();
  for (const c of S.centers) {
    L.circle([c.lat, c.lon], { radius: c.radius_km * 1000, color: "#D52B1E", weight: 2, fillOpacity: 0.05, dashArray: "6 6" }).addTo(circles);
    L.circleMarker([c.lat, c.lon], { radius: 5, color: "#D52B1E", fillColor: "#fff", fillOpacity: 1, weight: 3 }).bindTooltip(c.label).addTo(circles);
  }
}
function fitCenters() {
  if (!S.centers.length) return;
  const bounds = S.centers.reduce((b, c) => b.extend(L.latLng(c.lat, c.lon).toBounds(c.radius_km * 2000)),
    L.latLng(S.centers[0].lat, S.centers[0].lon).toBounds(1));
  map.fitBounds(bounds, { padding: [20, 20] });
  fitted = true;
}
map.on("moveend", () => {
  const b = map.getBounds();
  bbox = [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()];
  if (S.bboxOnly) runSearch();
});
map.on("click", (e) => {
  if (!$("#clickAdd").checked) return;
  addCenter({ label: `${t("Point")} ${e.latlng.lat.toFixed(3)} ; ${e.latlng.lng.toFixed(3)}`, lat: e.latlng.lat, lon: e.latlng.lng });
});

/* ------------------------------------------------------------------ search + render */
let seq = 0;
async function runSearchNow() {
  store.set("zivi.state", S);
  const my = ++seq;
  offset = 0;
  let res;
  try { res = await (await api("/api/search", query())).json(); } catch (e) { showWarnings([String(e.message)]); return; }
  if (my !== seq) return; // a newer search started
  lastResult = res;
  renderCount(res);
  renderTraitBar();
  showWarnings(res.warnings || []);
  $("#list").innerHTML = res.items.map(itemHtml).join("");
  $("#more").hidden = res.items.length >= res.total;
  renderEmpty(res);
  renderFacets(res.facets || {});
  drawPoints(res.points || []);
  setFavCount(res.favorites);
  renderMapKey(res.facets?.sector || []);
  $("#mapInfo").textContent = tn(res.points.length, "{n} lieu sur la carte", "{n} lieux sur la carte") +
    (res.unlocated ? ` · ${tn(res.unlocated, "{n} résultat sans localisation", "{n} résultats sans localisation")}` : "");
}
const runSearch = debounce(runSearchNow, 250);
/* Colour key of the map, shown when the "i" next to the place count is hovered or focused:
   the sectors of the current results, the pie bubbles, the favourite ring. */
function renderMapKey(sectors) {
  const rows = [...sectors].sort(([a], [b]) => (+sectorCode(a) || 99) - (+sectorCode(b) || 99));
  $("#mapKey").innerHTML = `<b>${t("Couleurs : domaine d’activité")}</b>
    <ul>${rows.map(([k, n]) => `<li><i class="swatch" style="--c:${sectorColor(sectorCode(k))}"></i><span>${esc(sectorName(k))}</span><small>${nf(n)}</small></li>`).join("")}</ul>
    <p><span class="key-pie" aria-hidden="true"></span>${t("Bulle : plusieurs affectations proches, en parts par domaine ; le nombre au centre.")}</p>
    <p><span class="dot fav key-fav" aria-hidden="true"></span>${t("Anneau doré : favori.")}</p>`;
}

async function loadMore() {
  offset += PAGE;
  const res = await (await api("/api/search", query({ offset }))).json();
  $("#list").insertAdjacentHTML("beforeend", res.items.map(itemHtml).join(""));
  $("#more").hidden = offset + res.items.length >= res.total;
}

function renderCount(res) {
  const mode = res.mode_used === "semantic" ? ` · ${t("classées par l’IA")}` : res.mode_used === "hybrid" ? ` · ${t("mots-clés + IA")}` : "";
  const favNote = S.favorites_only ? ` · ${t("favoris uniquement")}` : "";
  $("#count").innerHTML = `<b>${nf(res.total)}</b> ${res.total === 1 ? t("affectation") : t("affectations")}${favNote}${mode}`;
  $("#sort").value = S.sort || "";
  $("#rarityBtn").setAttribute("aria-pressed", String(S.sort === "rarity"));
  document.body.classList.toggle("rarity-mode", S.sort === "rarity");
}
function showWarnings(ws) { $("#warnings").innerHTML = ws.map((w) => `<p>${esc(fmtMsg(w))}</p>`).join(""); }
function renderEmpty(res) {
  const el = $("#empty");
  if (res.total) { el.hidden = true; return; }
  el.hidden = false;
  if ((res.warnings || []).some((w) => w?.key === "no_data")) {
    el.innerHTML = `<p>${t("Aucune affectation chargée pour l’instant.")}</p><button type="button" class="btn primary" data-import>${t("Charger un export JSON")}</button>`;
  } else if (S.favorites_only && !res.favorites) {
    el.innerHTML = `<p>${t("Vous n’avez encore aucun favori. Cliquez sur l’étoile ☆ d’une affectation pour l’ajouter.")}</p>
      <button class="btn" type="button" id="emptyFav">${t("Afficher toutes les affectations")}</button>`;
    $("#emptyFav").onclick = () => setFavoritesOnly(false);
  } else {
    el.innerHTML = `<p>${t("Aucune affectation ne correspond à tous ces filtres.")}</p><button class="btn" type="button" id="emptyReset">${t("Effacer tous les filtres")}</button>`;
    $("#emptyReset").onclick = resetAll;
  }
}

function itemHtml(it) {
  const facts = [];
  if (it.favorite) favs.add(it.id); else favs.delete(it.id);
  if (it.min_weeks) facts.push(t("min. {n} sem.", { n: it.min_weeks }));
  if (it.hours_per_week) facts.push(t("{n} h/sem.", { n: it.hours_per_week }));
  if (it.housing) facts.push(`<span class="yes">${t("Logement")}</span>`);
  if (it.meals) facts.push(`<span class="yes">${t("Repas")}</span>`);
  if (it.weekend === "J") facts.push(t("Week-ends"));
  if (it.night === "J") facts.push(t("Nuits"));
  if (it.first_week) facts.push(t("dès le {date}", { date: fmtDate(it.first_week) }));
  if (it.canton) facts.push(it.canton.replace(/,/g, ", "));
  const dist = it.distance_km != null ? `<span class="dist">${it.distance_km} km</span>` : "";
  const snip = it.snippet ? `<p class="snip">${sanitizeSnippet(it.snippet)}</p>` : "";
  const sec = it.sector ? `<span class="sec" style="--c:${sectorColor(sectorCode(it.sector))}">${esc(sectorName(it.sector))}</span>` : "";
  const showRare = (S.sort === "rarity" || S.traits.length) && it.rare?.length;
  const rare = showRare ? `<div class="rare">${rareChips(it.rare, 4)}</div>` : "";
  return `<li class="item" data-id="${it.id}" tabindex="0">${favBtn(it.id)}${dist}
    <h4${it.title_orig ? ` title="${esc(t("Original : {title}", { title: it.title_orig }))}"` : ""}>${esc(it.title)}</h4>${sec}
    <div class="who">${esc(it.employer)} · ${esc(it.place || "")}</div>
    <div class="facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
    <div class="facts">${(it.task_areas || []).map((x) => esc(dataLabel(x))).join(", ")}</div>${rare}${snip}</li>`;
}
/* "Durée minimale : 1 semaine · 0,3 %" — the rarest traits first, as given by the server */
/* Each chip is a button: a click shows every assignment sharing that rare value. */
const pct = (s) => (s < 0.001 ? `< ${(0.1).toLocaleString(LOCALE[LANG])} %`
  : `${(s * 100).toLocaleString(LOCALE[LANG], { maximumFractionDigits: s < 0.01 ? 1 : 0 })} %`);
const traitIndex = (field, value) => S.traits.findIndex((x) => x.field === field && x.value === String(value));
function rareChips(rare, max = Infinity) {
  // the traits already selected come first, so they are never hidden behind "+N"
  const sorted = [...rare].sort((a, b) => (traitIndex(b.field, b.value) >= 0) - (traitIndex(a.field, a.value) >= 0));
  const chips = sorted.slice(0, max).map((r) => {
    const on = traitIndex(r.field, r.value) >= 0;
    const tip = t("{count} sur l’ensemble ({pct}) ont cette caractéristique", { count: tn(r.count, "{n} affectation", "{n} affectations"), pct: pct(r.share) }) +
      " — " + (on ? t("cliquer pour retirer ce filtre") : t("cliquer pour les afficher"));
    return `<button type="button" class="rare-chip${on ? " on" : ""}" aria-pressed="${on}" title="${esc(tip)}"
      data-trait-field="${esc(r.field)}" data-trait-value="${esc(r.value)}" data-trait-label="${esc(r.label)}" data-trait-share="${r.share}">
      ✨ ${esc(traitLabel(r.field, r.label))}${COLON()}<b>${esc(traitValue(r.field, r.value))}</b> <small>${pct(r.share)}</small></button>`;
  });
  const more = rare.length > max ? `<span class="rare-more">+${rare.length - max}</span>` : "";
  return chips.join("") + more;
}
function toggleTrait(x) {
  const i = traitIndex(x.field, x.value);
  if (i >= 0) S.traits.splice(i, 1); else S.traits.push(x);
  runSearchNow();
}
/* The selected rare traits, on top of the list; × removes one. */
function renderTraitBar() {
  const bar = $("#traitBar");
  bar.hidden = !S.traits.length;
  bar.innerHTML = S.traits.map((x, i) => {
    const label = traitLabel(x.field, x.label), value = traitValue(x.field, x.value);
    return `<span class="rare-chip on">✨ ${esc(label)}${COLON()}<b>${esc(value)}</b>${
      x.share != null ? ` <small>${pct(x.share)}</small>` : ""}<button type="button" class="x" data-untrait="${i}"
      aria-label="${esc(t("Retirer {what}", { what: `${label}${COLON()}${value}` }))}" title="${esc(t("Retirer ce filtre"))}">×</button></span>`;
  }).join("") + (S.traits.length > 1 ? `<button type="button" class="link" data-untrait="all">${t("Tout retirer")}</button>` : "");
}
$("#traitBar").addEventListener("click", (e) => {
  const b = e.target.closest("[data-untrait]"); if (!b) return;
  if (b.dataset.untrait === "all") S.traits = []; else S.traits.splice(+b.dataset.untrait, 1);
  runSearchNow();
});
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-trait-field]");
  if (!b) return;
  e.stopPropagation(); // capture phase: the chip must not also open the entry
  const d = b.dataset;
  if (b.closest("#drawer")) { closeDrawer(); showPane("results"); }
  toggleTrait({ field: d.traitField, value: d.traitValue, label: d.traitLabel, share: +d.traitShare });
  $("#results").scrollTop = 0;
}, true);
function sanitizeSnippet(s) {
  return esc(s).replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>")
    .replace(/\s*\n\s*/g, " · "); // contact lines, addresses… on one line
}
function fmtDate(iso) { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; }

/* ------------------------------------------------------------------ facets */
function renderFacets(f) {
  for (const el of $$(".tri")) renderTri(el, f[el.dataset.field] || []);
  for (const el of $$(".multi")) renderMulti(el, f[el.dataset.field] || []);
  renderChecks("task_areas", f.task_areas || [], dataLabel);
  renderChecks("canton", f.canton || [], (v) => v);
  renderChecks("language", f.language || [], (v) => (LANG_NAMES[v] ? t(LANG_NAMES[v]) : v), t("Langue"));
  renderChecks("sector", f.sector || [], (v) => dataLabel(v), t("Domaine d’activité"));
  renderChecks("work_model", f.work_model || [], dataLabel, t("Modèle d’horaire"));
}
function renderTri(el, counts) {
  const c = Object.fromEntries(counts.map(([k, n]) => [String(k), n]));
  const field = el.dataset.field, cur = S[field];
  const opts = [["any", t("Tous"), (c[1] || 0) + (c[0] || 0)], [1, t("Oui"), c[1] || 0], [0, t("Non"), c[0] || 0]];
  let box = el.querySelector(".opts");
  if (!box) { box = document.createElement("div"); box.className = "opts"; el.append(box); }
  box.innerHTML = opts.map(([v, l, n]) => `<button type="button" data-v="${v}" aria-pressed="${String(cur === null ? v === "any" : cur === v)}">${l} <small>${n}</small></button>`).join("");
}
function renderMulti(el, counts) {
  const c = Object.fromEntries(counts);
  const field = el.dataset.field;
  el.querySelector(".opts").innerHTML = ["J", "M", "N"].map((v) =>
    `<button type="button" data-v="${v}" aria-pressed="${S[field].includes(v)}">${JNM(v)} <small>${c[v] || 0}</small></button>`).join("");
}
function renderChecks(field, counts, label, title) {
  const el = $(`.facet[data-field="${field}"]`);
  const sel = new Set(S[field]);
  const seen = new Set(counts.map(([k]) => k));
  const all = [...counts, ...[...sel].filter((k) => !seen.has(k)).map((k) => [k, 0])];
  const head = title ? `<p class="hint" style="margin-top:.6rem">${title}</p>` : "";
  const swatch = (k) => (field === "sector" ? `<i class="swatch" style="--c:${sectorColor(sectorCode(k))}"></i>` : "");
  el.innerHTML = head + all.map(([k, n]) => `<label class="${n ? "" : "zero"}"><input type="checkbox" value="${esc(k)}" ${sel.has(k) ? "checked" : ""}>
    ${swatch(k)}<span>${esc(label(k))}</span><span class="n">${n}</span></label>`).join("");
}
document.addEventListener("click", (e) => {
  const b = e.target.closest(".tri button, .multi button");
  if (!b) return;
  const host = b.closest("[data-field]"), field = host.dataset.field, v = b.dataset.v;
  if (host.classList.contains("tri")) S[field] = v === "any" ? null : +v;
  else S[field] = S[field].includes(v) ? S[field].filter((x) => x !== v) : [...S[field], v];
  runSearch();
});
document.addEventListener("change", (e) => {
  const box = e.target.closest(".facet");
  if (!box) return;
  S[box.dataset.field] = $$("input:checked", box).map((i) => i.value);
  runSearch();
});

/* ------------------------------------------------------------------ places */
function addCenter(c) {
  S.centers.push({ label: c.label, lat: +c.lat, lon: +c.lon, radius_km: +S.radius });
  renderCenters(); drawCircles(); fitCenters(); runSearch();
}
function renderCenters() {
  $("#centers").innerHTML = S.centers.map((c, i) => `<li><span>${esc(c.label)}</span>
    <input type="number" min="1" max="300" value="${c.radius_km}" data-i="${i}" aria-label="${esc(t("Rayon en km pour {place}", { place: c.label }))}"> km
    <button type="button" data-del="${i}" aria-label="${esc(t("Retirer {what}", { what: c.label }))}">×</button></li>`).join("");
}
$("#centers").addEventListener("click", (e) => {
  const i = e.target.dataset.del; if (i === undefined) return;
  S.centers.splice(+i, 1); renderCenters(); drawCircles(); runSearch();
});
$("#centers").addEventListener("input", debounce((e) => {
  const i = e.target.dataset.i; if (i === undefined) return;
  S.centers[+i].radius_km = Math.max(1, +e.target.value || 1); drawCircles(); runSearch();
}, 300));
$("#radius").addEventListener("input", (e) => {
  S.radius = +e.target.value; $("#radiusOut").textContent = `${S.radius} km`;
  if (S.centers.length === 1) { S.centers[0].radius_km = S.radius; renderCenters(); drawCircles(); runSearch(); }
});
let sugItems = [], sugIdx = -1;
const suggest = debounce(async (text) => {
  if (text.trim().length < 2) { $("#placeSug").hidden = true; return; }
  try { sugItems = (await (await api(`/api/geocode?q=${encodeURIComponent(text)}`)).json()).results; } catch { sugItems = []; }
  sugIdx = -1;
  const ul = $("#placeSug");
  ul.innerHTML = sugItems.length ? sugItems.map((s, i) => `<li role="option" data-i="${i}">${esc(s.label)}</li>`).join("") : `<li>${t("Aucun lieu trouvé")}</li>`;
  ul.hidden = false;
}, 250);
$("#placeQ").addEventListener("input", (e) => suggest(e.target.value));
$("#placeQ").addEventListener("keydown", (e) => {
  const lis = $$("#placeSug li[data-i]");
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); if (!lis.length) return;
    sugIdx = (sugIdx + (e.key === "ArrowDown" ? 1 : -1) + lis.length) % lis.length;
    lis.forEach((li, i) => li.setAttribute("aria-selected", i === sugIdx));
  } else if (e.key === "Enter") {
    e.preventDefault(); pickPlace(sugIdx >= 0 ? sugIdx : 0);
  } else if (e.key === "Escape") { $("#placeSug").hidden = true; }
});
$("#placeSug").addEventListener("mousedown", (e) => { const li = e.target.closest("li[data-i]"); if (li) pickPlace(+li.dataset.i); });
$("#placeQ").addEventListener("blur", () => setTimeout(() => { $("#placeSug").hidden = true; }, 150));
function pickPlace(i) {
  const s = sugItems[i]; if (!s) return;
  addCenter(s); $("#placeQ").value = ""; $("#placeSug").hidden = true;
}
$("#clickAdd").addEventListener("change", (e) => document.body.classList.toggle("pick-mode", e.target.checked));
$("#bboxOnly").addEventListener("change", (e) => { S.bboxOnly = e.target.checked; runSearch(); });

/* ------------------------------------------------------------------ other inputs */
function bindInputs() {
  const v = (id, val) => { $(id).value = val ?? ""; };
  v("#q", S.text); v("#pFrom", S.period.from); v("#pTo", S.period.to); v("#pDur", S.period.duration_weeks);
  $("#pAll").checked = S.period.mode === "all";
  v("#mwMin", S.min_weeks_min); v("#mwMax", S.min_weeks_max); v("#hMin", S.hours_min); v("#hMax", S.hours_max);
  $("#noCourse").checked = !!S.no_course_required;
  $(`input[name=mode][value=${S.text_mode}]`).checked = true;
  $(`input[name=taMode][value=${S.task_areas_mode}]`).checked = true;
  $("#radius").value = S.radius; $("#radiusOut").textContent = `${S.radius} km`;
  $("#bboxOnly").checked = S.bboxOnly;
  renderCenters(); drawCircles();
}
$("#q").addEventListener("input", (e) => { S.text = e.target.value; runSearch(); if (!$("#adv").hidden) fillAdvanced(); });

/* ------------------------------------------------------------------ advanced search
   The four fields are just another way to write the same query text as the main box:
   all → plain words, phrase → "…", any → a OR b OR c, none → -word. */
const advField = (k) => $(`[data-adv="${k}"]`);
const words = (v) => (v.match(/"[^"]+"|[^\s"]+/g) || []);
function buildQuery() {
  const all = words(advField("all").value).join(" ");
  const phrase = advField("phrase").value.replace(/"/g, "").trim();
  const any = words(advField("any").value).filter((w) => w !== "OR");
  const none = words(advField("none").value).map((w) => `-${w.replace(/^-+/, "")}`);
  return [all, phrase && `"${phrase}"`, any.join(" OR "), none.join(" ")].filter(Boolean).join(" ");
}
/* Main box → fields (best effort: a second "phrase" stays in "all words", quoted). */
function fillAdvanced() {
  const toks = S.text.match(/-?"[^"]*"?|\S+/g) || [];
  const f = { all: [], phrase: "", any: [], none: [] };
  for (let i = 0; i < toks.length; i++) {
    const tok = toks[i];
    if (tok === "OR") continue;
    const orNext = toks[i + 1] === "OR", orPrev = toks[i - 1] === "OR";
    if (tok.startsWith("-") && tok.length > 1) f.none.push(tok.slice(1).replace(/"/g, ""));
    else if (orNext || orPrev) f.any.push(tok);
    else if (tok.startsWith('"') && !f.phrase) f.phrase = tok.replace(/"/g, "");
    else f.all.push(tok);
  }
  advField("all").value = f.all.join(" ");
  advField("phrase").value = f.phrase;
  advField("any").value = f.any.join(" ");
  advField("none").value = f.none.join(" ");
  $("#advPreview").textContent = S.text.trim() || "—";
}
function setAdvanced(open) {
  $("#adv").hidden = !open;
  $("#advBtn").setAttribute("aria-expanded", String(open));
  $("#qWrap").classList.toggle("adv-open", open);
  if (open) { fillAdvanced(); advField("all").focus(); }
}
$("#advBtn").addEventListener("click", () => setAdvanced($("#adv").hidden));
$("#advDone").addEventListener("click", () => { setAdvanced(false); $("#q").focus(); });
$("#advClear").addEventListener("click", () => {
  for (const k of ["all", "phrase", "any", "none"]) advField(k).value = "";
  advField("all").dispatchEvent(new Event("input", { bubbles: true }));
  advField("all").focus();
});
$("#adv").addEventListener("input", (e) => {
  if (!e.target.dataset.adv) return;
  S.text = buildQuery();
  $("#q").value = S.text;
  $("#advPreview").textContent = S.text || "—";
  runSearch();
});
document.addEventListener("mousedown", (e) => {
  if (!$("#adv").hidden && !e.target.closest("#qWrap")) setAdvanced(false);
  if (!$("#langList").hidden && !e.target.closest("#langMenu")) setLangMenu(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#adv").hidden) { setAdvanced(false); $("#q").focus(); }
  if (e.key === "Escape" && !$("#langList").hidden) { setLangMenu(false); $("#langBtn").focus(); }
});
$("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); runSearchNow(); });
$$("input[name=mode]").forEach((r) => r.addEventListener("change", () => { S.text_mode = r.value; runSearch(); }));
$$("input[name=taMode]").forEach((r) => r.addEventListener("change", () => { S.task_areas_mode = r.value; runSearch(); }));
const numBind = { "#mwMin": "min_weeks_min", "#mwMax": "min_weeks_max", "#hMin": "hours_min", "#hMax": "hours_max" };
for (const [id, key] of Object.entries(numBind)) $(id).addEventListener("input", (e) => { S[key] = e.target.value; runSearch(); });
$("#pFrom").addEventListener("change", (e) => { S.period.from = e.target.value; runSearch(); });
$("#pTo").addEventListener("change", (e) => { S.period.to = e.target.value; runSearch(); });
$("#pDur").addEventListener("input", (e) => { S.period.duration_weeks = e.target.value; runSearch(); });
$("#pAll").addEventListener("change", (e) => { S.period.mode = e.target.checked ? "all" : "any"; runSearch(); });
$("#noCourse").addEventListener("change", (e) => { S.no_course_required = e.target.checked; runSearch(); });
$("#sort").addEventListener("change", (e) => { S.sort = e.target.value; runSearch(); });
let sortBeforeRarity = "";
$("#rarityBtn").addEventListener("click", () => {
  if (S.sort === "rarity") S.sort = sortBeforeRarity === "rarity" ? "" : sortBeforeRarity;
  else { sortBeforeRarity = S.sort || ""; S.sort = "rarity"; }
  runSearchNow();
});
$("#more").addEventListener("click", loadMore);
function resetAll() {
  const keep = { text_mode: S.text_mode, ai_top_k: S.ai_top_k, favorites_only: S.favorites_only,
    view_tr: S.view_tr, search_original: S.search_original };
  S = Object.assign(blank(), keep); bindInputs(); runSearchNow();
}
$("#resetBtn").addEventListener("click", resetAll);

/* ------------------------------------------------------------------ favourites */
function favBtn(id) {
  const on = favs.has(id);
  const label = on ? t("Retirer des favoris") : t("Ajouter aux favoris");
  return `<button type="button" class="fav${on ? " on" : ""}" data-fav="${id}" aria-pressed="${on}"
    title="${label}" aria-label="${label}">${on ? "★" : "☆"}</button>`;
}
function setFavCount(n) { if (n != null) $("#favCount").textContent = nf(n); }
function setFavoritesOnly(on) {
  S.favorites_only = on;
  $("#favOnly").setAttribute("aria-pressed", String(on));
  document.body.classList.toggle("fav-mode", on);
  fitted = false;
  runSearchNow();
}
$("#favOnly").addEventListener("click", () => setFavoritesOnly(!S.favorites_only));
async function toggleFavorite(id) {
  const want = !favs.has(id);
  let r;
  try { r = await (await api(`/api/favorites/${id}`, { favorite: want })).json(); } catch (e) { alert(e.message); return; }
  if (r.favorite) favs.add(id); else favs.delete(id);
  setFavCount(r.count);
  for (const b of $$(`[data-fav="${id}"]`)) b.outerHTML = favBtn(id);
  for (const m of markersById.get(id) || []) m.setIcon(dotIcon(m.options.sector, r.favorite));
  if (S.favorites_only) runSearchNow(); // the entry leaves (or joins) the list, map and filters
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-fav]");
  if (!b) return;
  e.stopPropagation(); // capture phase: the star must not also open the entry
  toggleFavorite(+b.dataset.fav);
}, true);

const list = $("#list");
list.addEventListener("click", (e) => { const li = e.target.closest(".item"); if (li) openDetail(+li.dataset.id); });
list.addEventListener("keydown", (e) => { const li = e.target.closest(".item"); if (li && e.key === "Enter") openDetail(+li.dataset.id); });
list.addEventListener("mouseover", (e) => { const li = e.target.closest(".item"); if (li) highlight(+li.dataset.id, true); });
list.addEventListener("mouseout", (e) => { const li = e.target.closest(".item"); if (li) highlight(+li.dataset.id, false); });

/* ------------------------------------------------------------------ detail drawer */
let openId = null;
async function openDetail(id) {
  let d;
  try { d = await (await api(withLang(`/api/assignment/${id}`))).json(); } catch (e) { alert(e.message); return; }
  openId = id;
  if (d.favorite) favs.add(id); else favs.delete(id);
  // each attribute can be clicked (a "+" shows on hover) to filter on that value
  const applyAttr = (filter) => (filter ? ` data-apply="${esc(JSON.stringify(filter))}" tabindex="0" role="button" title="${esc(t("Filtrer : afficher les affectations avec cette valeur"))}"` : "");
  const fact = (k, v, filter) => (v === null || v === undefined || v === "" ? "" : `<div${applyAttr(filter)}><dt>${k}</dt><dd>${esc(v)}</dd></div>`);
  const yn = (field) => (d[field] == null ? null : { [field]: d[field] ? 1 : 0 });
  const src = d.language || "";
  const lang = src ? ` lang="${esc(src)}"` : "";
  // data-tr marks the original-language texts the "Traduire" button translates
  // data-field names the field so a stored translation (d.tr) can replace the text
  const sec = (title, text, field) => (text
    ? `<section class="d-sec"><h3>${title}</h3><p${field ? ` data-tr data-field="${field}"${lang}` : ""}>${esc(text)}</p></section>` : "");
  const addr = (d.locations || []).map((l) => l.label || [l.plz, l.city].filter(Boolean).join(" ")).filter(Boolean);
  $("#drawerBody").innerHTML = `
    <p class="d-num">${t("N°")} ${esc(d.number)}${(d.canton || "").split(",").filter(Boolean).map((c) => ` · <span class="d-attr"${applyAttr({ canton: [c] })}>${esc(c)}</span>`).join("")}${d.sector ? ` · <span class="sec d-attr" style="--c:${sectorColor(sectorCode(d.sector))}"${applyAttr({ sector: [d.sector] })}>${esc(sectorName(d.sector))}</span>` : ""}</p>
    <h2 class="d-title" id="dTitle" data-tr data-field="title"${lang}>${esc(d.title)}</h2>
    <p class="d-who">${esc(d.employer)} · ${esc(d.place || "")}</p>
    <p class="d-actions"><a class="btn primary" href="${esc(d.url)}" target="_blank" rel="noopener">${t("Ouvrir sur ZiviConnect")}</a>
       ${d.locations.some((l) => l.lat != null) ? `<button class="btn" type="button" id="zoomTo">${t("Voir sur la carte")}</button>` : ""}
       ${src !== LANG ? `<button class="btn" type="button" id="trBtn" title="${esc(t("Traduire le texte de l’affectation dans la langue de l’interface avec le traducteur intégré du navigateur"))}"><span aria-hidden="true">文A</span><span class="tr-label"> ${t("Traduire")}</span></button>` : ""}
       ${favBtn(id)}</p>
    <p class="tr-status" id="trStatus" role="status" hidden></p>
    <dl class="d-grid">
      ${fact(t("Durée minimale"), d.min_weeks ? tn(d.min_weeks, "{n} semaine", "{n} semaines") : "", d.min_weeks && { min_weeks_min: d.min_weeks, min_weeks_max: d.min_weeks })}
      ${fact(t("Heures par semaine"), d.hours_per_week, d.hours_per_week && { hours_min: d.hours_per_week, hours_max: d.hours_per_week })}
      ${fact(t("Modèle d’horaire"), d.work_model ? dataLabel(d.work_model) : "", d.work_model && { work_model: [d.work_model] })}
      ${fact(t("Logement"), YN(d.housing), yn("housing"))}${fact(t("Repas"), YN(d.meals), yn("meals"))}
      ${fact(t("Travail le week-end"), JNM(d.weekend), d.weekend && { weekend: [d.weekend] })}${fact(t("Travail de nuit"), JNM(d.night), d.night && { night: [d.night] })}
      ${fact(t("Contrôle de réputation"), YN(d.reputation_check), yn("reputation_check"))}
      ${fact(t("Programme prioritaire"), YN(d.priority_program), yn("priority_program"))}
      ${fact(t("À l’étranger"), d.abroad ? t("Oui") : "", d.abroad ? { abroad: 1 } : null)}
    </dl>
    <p class="d-tasks">${(d.task_areas || []).map((x) => `<span class="d-attr"${applyAttr({ task_areas: [x] })}>${esc(dataLabel(x))}</span>`).join("")}</p>
    ${d.rare?.length ? `<section class="d-sec"><h3>${t("Ce qui la rend rare")}</h3><div class="rare">${rareChips(d.rare)}</div></section>` : ""}
    ${sec(t("Tâches"), d.tasks, "tasks")}
    ${sec(t("Compétences requises"), d.required_skills, "required_skills")}
    ${sec(t("Compétences souhaitées"), d.desired_skills, "desired_skills")}
    ${sec(t("Cours du service civil"), d.courses, "courses")}
    <section class="d-sec"><h3>${t("Semaines de début disponibles")}</h3>${calendar(d.weeks || [])}</section>
    ${sec(t("À propos de l’établissement"), d.employer_description, "employer_description")}
    ${sec(t("Adresse de l’affectation"), addr.join("\n"))}
    <section class="d-sec"><h3>${t("Contact")}</h3><p>${esc([d.contact_name, d.contact_role].filter(Boolean).join(", "))}
${d.contact_phone ? `<a href="tel:${esc(d.contact_phone.replace(/\s/g, ""))}">${esc(d.contact_phone)}</a>` : ""}
${d.contact_email ? `<a href="mailto:${esc(d.contact_email)}">${esc(d.contact_email)}</a>` : ""}</p></section>
    ${sec(t("Comment postuler"), d.how_to_apply, "how_to_apply")}`;
  const tb = $("#trBtn");
  detailTr = d.tr || {};
  if (tb) {
    tb.onclick = () => toggleTranslation(src);
    // "show translated" is on and the database has this entry translated: open it so
    if (S.view_tr && Object.keys(detailTr).length) applyStored(src, t("Traduction enregistrée dans la base."));
  }
  const z = $("#zoomTo");
  if (z) z.onclick = () => {
    closeDrawer(); showPane("mapPane");
    const l = d.locations.find((x) => x.lat != null); map.setView([l.lat, l.lon], 13);
    const m = (markersById.get(id) || [])[0];
    if (m) cluster.zoomToShowLayer(m, () => m.openPopup());
  };
  $("#drawer").hidden = false; $("#drawerClose").focus();
}
/* ------------------------------------------------------------------ translation
   Uses the browser's built-in, on-device Translator API (Chrome / Edge 138+): nothing is
   sent to a server. The target is the interface language. Without the API, the user is
   pointed to the browser's own page translation. */
const translators = new Map(); // "de>fr" -> Translator
async function getTranslator(source, status = () => {}) {
  const key = `${source}>${LANG}`;
  if (translators.has(key)) return translators.get(key);
  const opts = { sourceLanguage: source, targetLanguage: LANG };
  status(t("Préparation du traducteur du navigateur…"));
  // some browsers expose the API but never answer (embedded Chromium): don't hang forever
  const avail = await Promise.race([Translator.availability(opts),
    new Promise((resolve) => setTimeout(() => resolve("timeout"), 8000))]);
  if (avail === "timeout") throw new Error(t("le traducteur intégré du navigateur ne répond pas"));
  if (avail === "unavailable") return null;
  if (avail !== "available") status(t("Téléchargement du modèle de traduction…"));
  const tr = await Translator.create({
    ...opts,
    monitor(m) {
      m.addEventListener("downloadprogress", (e) =>
        status(t("Téléchargement du modèle de traduction : {pct} %", { pct: Math.round(e.loaded * 100) })));
    },
  });
  translators.set(key, tr);
  return tr;
}
/* The browser's translator fails now and then ("Other generic failures occurred"): on long
   inputs, and sometimes after a while for no visible reason. So texts go in sentence slices,
   a failed slice is retried with a fresh translator, then cut in half; only a slice that
   still fails makes the text fail (the caller decides what to do with it). */
const SLICE = 600; // characters per call
function slices(line, max = SLICE) {
  const out = [];
  let rest = line;
  while (rest.length > max) {
    const head = rest.slice(0, max);
    // cut after the last sentence end, else after a comma or a space, never mid-word
    let cut = Math.max(...[". ", "! ", "? ", "; ", ": "].map((m) => head.lastIndexOf(m)));
    if (cut < max * 0.4) cut = head.lastIndexOf(", ");
    if (cut < max * 0.4) cut = head.lastIndexOf(" ");
    cut = cut > 0 ? cut + 1 : max;
    out.push(rest.slice(0, cut));
    rest = rest.slice(cut);
  }
  if (rest) out.push(rest);
  return out;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function translateSlice(lang, text, status, depth = 0) {
  let lastErr;
  for (let attempt = 0; attempt < 3; attempt++) {
    // only the first creation may show "Préparation…"/download progress; quiet recreations
    const tr = await getTranslator(lang, attempt === 0 && depth === 0 ? status : undefined);
    if (!tr) throw new Error(t("le navigateur ne sait pas traduire depuis {lang}", { lang: langName(lang) }));
    try {
      return await tr.translate(text);
    } catch (e) {
      lastErr = e;
      if (attempt >= 1) { // failed twice: drop this translator, the next attempt gets a fresh one
        try { tr.destroy?.(); } catch { /* already gone */ }
        translators.delete(`${lang}>${LANG}`);
      }
      if (attempt < 2) await sleep(150 * (attempt + 1));
    }
  }
  if (text.length > 300 && depth < 1) { // a long slice may go through in two halves
    const parts = slices(text, Math.ceil(text.length / 2));
    if (parts.length > 1) {
      const out = [];
      for (const part of parts) out.push((await translateSlice(lang, part, status, depth + 1)).trim());
      return out.join(" ");
    }
  }
  throw lastErr;
}
/* A whole text: line breaks kept, each line sent in slices. */
async function translateText(lang, text, status) {
  const out = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) { out.push(line); continue; }
    const parts = [];
    for (const piece of slices(line)) parts.push((await translateSlice(lang, piece, status)).trim());
    out.push(parts.join(" "));
  }
  return out.join("\n");
}
async function detectLanguage(text) {
  if (!("LanguageDetector" in self)) return "de"; // most assignments are in German
  try {
    const det = await LanguageDetector.create();
    return (await det.detect(text))[0]?.detectedLanguage || "de";
  } catch { return "de"; }
}
/* "allemand" / "Deutsch" / "tedesco": a language name in the interface language */
const langName = (code) => t({ de: "allemand", fr: "français", it: "italien", en: "anglais" }[code] || code);
let trRun = 0;
let detailTr = {}; // stored translations of the open entry, by field
/* Put the stored translations in place; returns the elements still in the original language. */
function applyStored(source, message) {
  const rest = [];
  for (const el of $$("#drawerBody [data-tr]")) {
    const tx = detailTr[el.dataset.field];
    if (!tx) { rest.push(el); continue; }
    el.dataset.orig ??= el.textContent;
    el.textContent = tx; el.lang = LANG;
  }
  if (rest.length < $$("#drawerBody [data-tr]").length) {
    const btn = $("#trBtn");
    btn.dataset.state = "done"; btn.querySelector(".tr-label").textContent = ` ${t("Voir l’original")}`;
    const out = $("#trStatus"); out.hidden = !message; out.textContent = message || ""; out.classList.remove("err");
  }
  return rest;
}
async function toggleTranslation(source) {
  const btn = $("#trBtn"), out = $("#trStatus"), label = btn.querySelector(".tr-label");
  const els = $$("#drawerBody [data-tr]");
  const status = (message, err = false) => { out.hidden = !message; out.textContent = message || ""; out.classList.toggle("err", err); };
  if (btn.dataset.state === "done") { // back to the original text
    for (const el of els) if (el.dataset.orig != null) { el.textContent = el.dataset.orig; if (source) el.lang = source; }
    btn.dataset.state = ""; label.textContent = ` ${t("Traduire")}`; status("");
    return;
  }
  // translations already in the database first; the browser only does the rest
  const stored = Object.keys(detailTr).length;
  const rest = applyStored(source, t("Traduction enregistrée dans la base."));
  if (!rest.length) return;
  if (!("Translator" in self)) {
    if (stored) return; // partly translated from the database: good enough
    status(t("Ce navigateur n’a pas de traducteur intégré utilisable par la page. Utilisez un Chrome ou Edge récent, ou faites un clic droit sur le texte → « Traduire ».") , true);
    return;
  }
  const run = ++trRun;
  btn.disabled = true;
  try {
    const src = source || await detectLanguage(els.map((e) => e.textContent).join("\n").slice(0, 2000));
    if (src === LANG) { status(t("Ce texte est déjà dans la langue de l’interface.")); return; }
    const tr = await getTranslator(src, status);
    if (!tr) { status(t("Le navigateur ne sait pas traduire depuis {lang}.", { lang: langName(src) }), true); return; }
    status(t("Traduction en cours…"));
    for (const el of rest) {
      if (run !== trRun || !$("#drawerBody").contains(el)) return; // panel closed or another entry opened
      const orig = el.dataset.orig ?? el.textContent;
      const done = await translateText(src, orig, status);
      el.dataset.orig = orig;
      el.textContent = done;
      el.lang = LANG;
    }
    btn.dataset.state = "done"; label.textContent = ` ${t("Voir l’original")}`;
    status(t("Traduit depuis {lang} par le traducteur intégré du navigateur.", { lang: langName(src) }));
  } catch (e) {
    status(t("La traduction a échoué : {error}. Faites un clic droit sur le texte → « Traduire », ou utilisez un Chrome ou Edge récent.", { error: e.message }), true);
  } finally {
    if (run === trRun) btn.disabled = false;
  }
}

function calendar(weeks) {
  if (!weeks.length) return `<p>${t("Aucune date publiée.")}</p>`;
  const { from, to } = S.period;
  const months = {};
  for (const w of weeks) (months[w.slice(0, 7)] ||= []).push(w);
  const name = (ym) => new Date(ym + "-15").toLocaleDateString(LOCALE[LANG], { month: "long", year: "numeric" });
  return `<div class="cal">${Object.entries(months).map(([ym, ws]) => `<div class="m"><b>${name(ym)}</b>${ws.map((w) =>
    `<span class="${(from && w >= from && (!to || w <= to)) || (!from && to && w <= to) ? "in" : ""}">${w.slice(8)}.${w.slice(5, 7)}</span>`).join("")}</div>`).join("")}</div>`;
}
function closeDrawer() { $("#drawer").hidden = true; openId = null; }
/* An attribute of the open entry was clicked: add that filter, show the matching entries.
   Lists (sector, canton, task types…) gain the value; yes/no and ranges take it. */
function applyAttribute(el) {
  const filter = JSON.parse(el.dataset.apply);
  for (const [k, v] of Object.entries(filter)) {
    if (Array.isArray(v)) S[k] = [...new Set([...(S[k] || []), ...v])];
    else S[k] = v;
  }
  if (filter.task_areas) S.task_areas_mode = S.task_areas.length > 1 ? S.task_areas_mode : "any";
  closeDrawer();
  bindInputs();
  showPane("results");
  $("#results").scrollTop = 0;
  runSearchNow();
}
$("#drawerBody").addEventListener("click", (e) => { const el = e.target.closest("[data-apply]"); if (el) applyAttribute(el); });
$("#drawerBody").addEventListener("keydown", (e) => {
  const el = e.target.closest("[data-apply]");
  if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); applyAttribute(el); }
});
$("#drawerClose").addEventListener("click", closeDrawer);
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer(); });

/* ------------------------------------------------------------------ import */
const mb = (bytes) => (bytes / 1048576).toLocaleString(LOCALE[LANG], { maximumFractionDigits: 1 });
$("#fileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  const dlg = $("#importDlg");
  $("#impMsg").textContent = t("Envoi de {file} ({size} Mo)…", { file: file.name, size: mb(file.size) });
  $("#impBar").style.width = "2%"; $("#impReport").hidden = true; $("#impClose").disabled = true;
  dlg.showModal();
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch("/api/import", { method: "POST", body: fd });
    if (!r.ok) throw new Error(fmtMsg((await r.json()).detail) || r.statusText);
  } catch (err) { $("#impMsg").textContent = t("Échec de l’envoi : {error}", { error: err.message }); $("#impClose").disabled = false; return; }
  const poll = async () => {
    const st = await (await api("/api/import/status")).json();
    $("#impMsg").textContent = st.error ? t("Échec de l’import : {error}", { error: fmtMsg(st.error) }) : fmtMsg(st.message);
    $("#impBar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
    if (st.running) { setTimeout(poll, 700); return; }
    $("#impClose").disabled = false;
    if (!st.error) $("#impBar").style.width = "100%";
    if (st.report) {
      const r = st.report;
      $("#impMsg").textContent = t("{n} affectations chargées — {located} placées sur la carte, {weeks} avec des dates de début.",
        { n: nf(r.assignments), located: nf(r.with_location), weeks: nf(r.with_weeks) });
      const extra = { unknown_fields: r.unknown_detail_fields, missing_fields: r.fields_missing_in_some_records };
      if (Object.keys(r.unknown_detail_fields || {}).length || Object.keys(r.fields_missing_in_some_records || {}).length) {
        $("#impReport").textContent = JSON.stringify(extra, null, 2); $("#impReport").hidden = false;
      }
      fitted = false;
      await loadLabels();
      runSearchNow();
      if ($("#dbDlg").open) loadDbInfo();
    }
  };
  poll();
});

/* ------------------------------------------------------------------ "Base de données" window
   One floating window, three sections chosen in the left column. */
const SEC_TITLES = { db: "Base de données", tr: "Traduction", ai: "Recherche IA" };
let panelSec = store.get("zivi.panelSec", "db");
function showSection(sec) {
  panelSec = sec;
  $$("#dbDlg .panel-nav [data-sec]").forEach((b) => b.toggleAttribute("aria-current", b.dataset.sec === sec));
  $$("#dbDlg .panel-sec").forEach((el) => { el.hidden = el.dataset.sec !== sec; });
  $("#pTitle").textContent = t(SEC_TITLES[sec]);
  store.set("zivi.panelSec", sec);
  ({ db: loadDbInfo, tr: () => { refreshTrStats(); showTrEngine(); }, ai: loadAI })[sec]();
}
function openPanel(sec) {
  const dlg = $("#dbDlg");
  const opening = !dlg.open;
  if (opening) dlg.showModal();
  showSection(sec || panelSec);
  // the dialog focuses its first button by default (Base de données): use the open section
  if (opening) $("#dbDlg .panel-nav [aria-current]")?.focus();
}
$("#dbBtn").addEventListener("click", () => openPanel());
$("#dbClose").addEventListener("click", () => $("#dbDlg").close());
$("#dbDlg").addEventListener("click", (e) => { if (e.target.id === "dbDlg") $("#dbDlg").close(); }); // backdrop
$$("#dbDlg .panel-nav [data-sec]").forEach((b) => b.addEventListener("click", () => showSection(b.dataset.sec)));
document.addEventListener("click", (e) => { if (e.target.closest("[data-import]")) $("#fileInput").click(); });

/* ---- database */
const fmtSize = (b) => (b > 1048576 ? t("{n} Mo", { n: mb(b) }) : t("{n} Ko", { n: Math.round(b / 1024) }));
const fmtWhen = (s) => new Date(s * 1000).toLocaleString(LOCALE[LANG], { dateStyle: "long", timeStyle: "short" });
async function loadDbInfo() {
  let st;
  try { st = await (await api("/api/stats")).json(); } catch (e) { $("#dbInfo").textContent = e.message; return; }
  const card = (n, label, sub = "") => `<div class="stat"><b>${n}</b><span>${label}</span>${sub ? `<small>${sub}</small>` : ""}</div>`;
  const ai = st.ai_index || {};
  const byLang = st.translations_by_lang || {};
  const trSub = Object.entries(byLang).map(([l, n]) => `${l.toUpperCase()} ${nf(n)}`).join(" · ");
  $("#dbCards").innerHTML = [
    card(nf(st.assignments), t("affectations")),
    card(nf(st.located), t("sur la carte"), st.assignments ? `${Math.round((100 * st.located) / st.assignments)} %` : ""),
    card(nf(st.favorites), t("favoris")),
    card(nf(st.translations), t("textes traduits"), trSub),
    card(ai.ready ? nf(ai.vectors) : "—", t("index IA"), ai.ready ? esc(ai.model || "") : t("pas construit")),
  ].join("");
  const r = st.report || {};
  const row = (k, v) => (v ? `<div><dt>${k}</dt><dd>${v}</dd></div>` : "");
  $("#dbInfo").innerHTML = [
    row(t("Fichier importé"), esc(r.source_file || "")),
    row(t("Importé le"), st.imported_at ? fmtWhen(st.imported_at) : ""),
    row(t("Semaines de début"), st.first_week ? t("du {from} au {to}", { from: fmtDate(st.first_week), to: fmtDate(st.last_week) }) : ""),
    row(t("Fichier de la base"), `<code>${esc(st.db_path)}</code> <button type="button" class="link" id="copyPath">${t("Copier")}</button>`),
    row(t("Taille"), st.db_size ? fmtSize(st.db_size) : ""),
  ].join("");
  const cp = $("#copyPath");
  if (cp) cp.onclick = async () => { try { await navigator.clipboard.writeText(st.db_path); cp.textContent = t("Copié"); } catch { cp.textContent = t("Copie impossible"); } };
  renderBackup(byLang);
  const trHere = byLang[LANG] || 0;
  $("#dzFav").textContent = tn(st.favorites, "{n} favori", "{n} favoris");
  $("#dzTr").textContent = `${tn(trHere, "{n} texte traduit", "{n} textes traduits")} (${langName(LANG)})`;
  $("#dzAi").textContent = ai.ready ? t("{n} vecteurs ({model})", { n: nf(ai.vectors), model: ai.model }) : t("aucun");
  $("#dzFavBtn").disabled = !st.favorites;
  $("#dzTrBtn").disabled = !trHere || TR.running;
  $("#dzAiBtn").disabled = !ai.ready || ai.running;
}
/* ---- backup and sharing: download / upload the translations and the AI index */
async function renderBackup(byLang) {
  const nTr = Object.values(byLang).reduce((a, b) => a + b, 0);
  $("#bkTr").textContent = nTr
    ? `${tn(nTr, "{n} texte traduit", "{n} textes traduits")} (${Object.entries(byLang).map(([l, n]) => `${l.toUpperCase()} ${nf(n)}`).join(" · ")})`
    : t("aucune pour l’instant");
  $("#bkTrDown").classList.toggle("disabled", !nTr);
  let info = null;
  try { info = await (await api("/api/ai")).json(); } catch { /* shown as none */ }
  const built = builtNames(info?.built, info?.settings?.model);
  const vectors = Object.values(info?.built || {}).reduce((a, b) => a + b, 0);
  $("#bkAi").textContent = vectors
    ? `${info.settings.model} · ${t("{n} vecteurs ({model})", { n: nf(vectors), model: built.join(", ") })}`
    : t("aucun pour l’instant");
  $("#bkAiDown").classList.toggle("disabled", !vectors);
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-upload]"); if (!b) return;
  $("#bkFile").dataset.kind = b.dataset.upload;
  $("#bkFile").click();
});
$("#bkFile").addEventListener("change", async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  const kind = e.target.dataset.kind, out = $("#bkStatus");
  out.classList.remove("err"); out.textContent = t("Import de {file}…", { file: file.name });
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch(`/api/${kind}/import`, { method: "POST", body: fd });
    const body = await r.json();
    if (!r.ok) throw new Error(fmtMsg(body.detail) || r.statusText);
    // "FR 1'234" for translations; "nomic-embed-text DE 420" for AI indexes
    const name = (k) => (kind === "translate" ? k.toUpperCase()
      : k.includes("@") ? `${k.split("@")[0]} ${k.split("@")[1].toUpperCase()}` : k);
    const parts = Object.entries(body.imported).map(([k, n]) => `${name(k)} ${nf(n)}`);
    const total = Object.values(body.imported).reduce((a, b) => a + b, 0);
    out.textContent = kind === "translate"
      ? t("{n} traductions importées ({detail}).", { n: nf(total), detail: parts.join(" · ") })
      : t("{n} vecteurs importés ({detail}).", { n: nf(total), detail: parts.join(" · ") });
    loadDbInfo(); refreshTrStats();
    if (S.view_tr || S.text) runSearchNow();
  } catch (err) {
    out.classList.add("err"); out.textContent = t("Import impossible : {error}", { error: err.message });
  }
});
async function danger(question, call) {
  if (!confirm(question)) return;
  try { await call(); } catch (e) { alert(e.message); }
  loadDbInfo(); runSearchNow();
}
const del = (path) => fetch(path, { method: "DELETE" }).then(async (r) => { if (!r.ok) throw new Error(fmtMsg((await r.json()).detail) || r.statusText); return r; });
$("#dzFavBtn").addEventListener("click", () => danger(t("Effacer tous les favoris ? C’est définitif."), async () => {
  await del("/api/favorites"); favs.clear();
}));
$("#dzTrBtn").addEventListener("click", () => danger(t("Supprimer les traductions en {lang} ? Les autres langues sont conservées.", { lang: langName(LANG) }), async () => {
  await del(withLang("/api/translate")); refreshTrStats();
}));
$("#dzAiBtn").addEventListener("click", () => danger(t("Supprimer l’index IA de ce modèle (dans cette langue) ? Il faudra le reconstruire."), () => del("/api/ai/index")));

/* ---- translation of the whole database into the interface language.
   Browser engine: in this page, the server hands out texts not translated yet (titles first)
   and stores the answers. Ollama engine: the server runs the job, the page follows it. */
const TR = { running: false, stop: false, startedAt: 0, charsAtStart: 0, stats: null };
function renderTr() {
  const st = TR.stats;
  if (!st) return;
  const pctDone = st.chars ? (100 * st.chars_done) / st.chars : 0;
  const pctTxt = st.texts && st.done === st.texts ? "100 %" : `${pctDone.toLocaleString(LOCALE[LANG], { maximumFractionDigits: pctDone < 10 ? 1 : 0 })} %`;
  $("#trPct").textContent = st.texts ? pctTxt : t("Rien à traduire");
  $("#trCount").textContent = st.texts
    ? t("{done} / {texts} textes · {adone} / {assignments} affectations entièrement traduites",
      { done: nf(st.done), texts: nf(st.texts), adone: nf(st.assignments_done), assignments: nf(st.assignments) })
    : t("Toutes les affectations sont déjà en {lang}.", { lang: langName(LANG) });
  $("#trBar").style.width = `${pctDone}%`;
  $("#trStart").hidden = TR.running;
  $("#trPause").hidden = !TR.running;
  $("#trStart").textContent = st.done && st.done < st.texts ? t("Reprendre la traduction") : t("Traduire la base en {lang}", { lang: langName(LANG) });
  if (st.failed && !TR.running) $("#trCount").textContent += ` · ${tn(st.failed, "{n} texte mis de côté", "{n} textes mis de côté")}`;
  $("#trStart").disabled = !st.texts || st.done === st.texts;
  const badge = TR.running ? pctTxt : "";
  for (const id of ["#dbBadge", "#trNavBadge"]) { $(id).hidden = !badge; $(id).textContent = badge; }
}
async function refreshTrStats() {
  try { TR.stats = await (await api(withLang("/api/translate"))).json(); } catch { return; }
  renderTr();
  if (!TR.running && trEngine() === "browser" && !("Translator" in self)) {
    trJobMsg(t("Ce navigateur n’a pas de traducteur intégré utilisable par la page : utilisez un Chrome ou Edge récent, ou Ollama."), true);
  }
}
function trJobMsg(message, err = false) { const el = $("#trJobStatus"); el.textContent = message || ""; el.classList.toggle("err", err); }
const eta = (secs) => (secs > 5400 ? t("{n} h", { n: Math.round(secs / 3600) }) : t("{n} min", { n: Math.max(1, Math.round(secs / 60)) }));
async function translateDatabase() {
  if (TR.running) return;
  if (trEngine() === "ollama") return translateWithOllama();
  return translateInBrowser();
}
async function translateInBrowser() {
  if (TR.running) return;
  if (!("Translator" in self)) { trJobMsg(t("Ce navigateur n’a pas de traducteur intégré : utilisez un Chrome ou Edge récent."), true); return; }
  const target = LANG; // stays the same for the whole run, even if the interface changes
  Object.assign(TR, { running: true, stop: false, engine: "browser", startedAt: performance.now(),
    charsAtStart: TR.stats?.chars_done || 0, failed: 0 });
  renderTr();
  try {
    // texts set aside by an earlier run get another chance
    try { TR.stats = await (await api(`/api/translate/retry?lang=${target}`, {})).json(); } catch { /* keep counts */ }
    // ask for every model right away, while the click still counts as a user action
    // (the browser only allows downloading a model then)
    const langs = Object.keys(TR.stats?.by_language || { de: 1 }).filter((l) => l !== "?" && l !== target);
    for (const l of langs.length ? langs : ["de"]) {
      const tr = await getTranslator(l, trJobMsg);
      if (!tr) trJobMsg(t("Le navigateur ne sait pas traduire depuis {lang} : ces textes seront gardés tels quels.", { lang: langName(l) }), true);
    }
    let sinceStats = 0, failedInARow = 0, lastErr = null;
    while (!TR.stop) {
      const { items } = await (await api(`/api/translate/todo?limit=12&lang=${target}`)).json();
      if (!items.length) break;
      const done = [];
      for (const it of items) {
        if (TR.stop) break;
        const lang = it.lang || await detectLanguage(it.text);
        const tr = lang === target ? null : await getTranslator(lang, trJobMsg);
        if (!tr) { // already in the target language, or one the browser can't translate: keep as is
          done.push({ hash: it.hash, lang, text: it.text });
        } else {
          try {
            done.push({ hash: it.hash, lang, text: await translateText(lang, it.text, trJobMsg) });
            failedInARow = 0;
          } catch (e) {
            // set aside (retried on the next run) instead of stopping everything
            done.push({ hash: it.hash, failed: true });
            TR.failed += 1; failedInARow += 1; lastErr = e;
            if (failedInARow >= 25) throw new Error(t("le traducteur échoue sans arrêt ({error})", { error: e.message }));
            continue;
          }
        }
        TR.stats.done += 1; TR.stats.chars_done += it.text.length;
      }
      if (done.length) await api("/api/translate/save", { items: done, lang: target });
      const secs = (performance.now() - TR.startedAt) / 1000;
      const rate = (TR.stats.chars_done - TR.charsAtStart) / Math.max(secs, 1);
      const left = rate > 0 ? (TR.stats.chars - TR.stats.chars_done) / rate : 0;
      trJobMsg(t("Traduction en cours… {rate} caractères/s · encore {eta} environ", { rate: nf(Math.round(rate)), eta: eta(left) }) +
        (TR.failed ? ` · ${tn(TR.failed, "{n} texte mis de côté", "{n} textes mis de côté")} (${t("dernière erreur : {error}", { error: lastErr?.message })})` : "") + ".");
      renderTr();
      if (++sinceStats >= 15) { sinceStats = 0; await refreshTrStats(); } // exact counts now and then
    }
    const aside = TR.failed ? ` ${tn(TR.failed, "{n} texte n’a pas pu être traduit", "{n} textes n’ont pas pu être traduits")} : ${t("« Reprendre la traduction » les réessaie.")}` : "";
    trJobMsg((TR.stop ? t("En pause. « Reprendre la traduction » continue là où elle s’est arrêtée.") : t("Traduction terminée.")) + aside);
  } catch (e) {
    trJobMsg(t("La traduction s’est arrêtée : {error}. Ce qui est fait est enregistré ; vous pouvez reprendre.", { error: e.message }), true);
  } finally {
    TR.running = false;
    try { TR.stats = await (await api(`/api/translate/finish?lang=${target}`, {})).json(); } catch { /* keep the local counts */ }
    renderTr();
    if (S.view_tr || S.text) runSearchNow();
  }
}
$("#trStart").addEventListener("click", translateDatabase);
$("#trPause").addEventListener("click", async () => {
  TR.stop = true;
  if (TR.engine === "ollama") { try { await api("/api/translate/job/stop", {}); } catch { /* polling shows it */ } }
  trJobMsg(t("Mise en pause après le texte en cours…"));
});
// only the browser engine needs the page open; the Ollama job runs on the server
window.addEventListener("beforeunload", (e) => { if (TR.running && TR.engine === "browser") { e.preventDefault(); e.returnValue = ""; } });

/* ---- translation through Ollama: the server runs the job, the page only follows it */
const trEngine = () => $("input[name=trEngine]:checked")?.value || "ollama";
function showTrEngine() {
  const ol = trEngine() === "ollama";
  $("#trOllama").hidden = !ol;
  $("#trHint").textContent = ol
    ? t("La traduction tourne sur cet ordinateur, dans Ollama : vous pouvez fermer cette fenêtre, et même la page. Ce qui est traduit est enregistré au fur et à mesure ; « Traduire la base » reprend là où elle s’est arrêtée.")
    : t("La traduction se fait dans cette page : vous pouvez fermer cette fenêtre et continuer à chercher, mais gardez la page ouverte. Ce qui est traduit est enregistré au fur et à mesure ; « Traduire la base » reprend là où elle s’est arrêtée.");
  if (ol) renderOllamaBox($("#trOllamaBox"), $("#trModel").value, "");
  else trJobMsg("");
}
$$("input[name=trEngine]").forEach((r) => r.addEventListener("change", () => {
  store.set("zivi.trEngine", trEngine()); showTrEngine(); refreshTrStats();
}));
$("#trModel").addEventListener("change", async () => {
  const model = $("#trModel").value.trim(); if (!model) return;
  try { await api("/api/translate/settings", { model }); } catch { /* shown by the box */ }
  renderOllamaBox($("#trOllamaBox"), model, "");
});
async function loadTrSettings() {
  try {
    const job = await (await api("/api/translate/job")).json();
    if (!$("#trModel").value) $("#trModel").value = job.model;
    if (job.running) followOllamaJob();
  } catch { /* server not ready */ }
}
async function translateWithOllama() {
  const model = $("#trModel").value.trim();
  const st = await ollamaStatus("");
  if (!st?.running) { trJobMsg(t("Ollama ne répond pas : démarrez-le ou installez-le ci-dessus."), true); return; }
  if (!st.models.some((m) => sameModel(m.name, model))) {
    trJobMsg(t("Téléchargez d’abord le modèle {model} (bouton ci-dessus).", { model }), true); return;
  }
  try { await api("/api/translate/job", { model, lang: LANG }); } catch (e) { trJobMsg(e.message, true); return; }
  followOllamaJob();
}
let trFollowing = false;
async function followOllamaJob() {
  if (trFollowing) return;
  trFollowing = true;
  Object.assign(TR, { running: true, stop: false, engine: "ollama" });
  renderTr();
  let target = LANG;
  try {
    for (;;) {
      const job = await (await api("/api/translate/job")).json();
      target = job.target || target;
      TR.stats = await (await api(`/api/translate?lang=${target}`)).json();
      TR.running = !!job.running;
      renderTr();
      const left = job.rate > 0 ? (TR.stats.chars - TR.stats.chars_done) / job.rate : 0;
      const aside = job.skipped ? ` · ${tn(job.skipped, "{n} texte mis de côté", "{n} textes mis de côté")}` : "";
      if (job.running) {
        trJobMsg(t("Traduction en {lang} avec {model}…", { lang: langName(target), model: job.model }) + " " + (job.rate
          ? t("{rate} caractères/s · encore {eta} environ", { rate: nf(Math.round(job.rate)), eta: eta(left) }) + aside
          : t("premier texte (le modèle se charge en mémoire)")) + (job.note ? ` · ${fmtMsg(job.note)}` : "") + ".");
        await sleep(2000);
        continue;
      }
      if (job.error) trJobMsg(t("La traduction s’est arrêtée : {error}. Ce qui est fait est enregistré ; vous pouvez reprendre.", { error: fmtMsg(job.error) }), true);
      else if (job.cancelled) trJobMsg(t("En pause{aside}. « Reprendre la traduction » continue là où elle s’est arrêtée.", { aside }));
      else trJobMsg(t("Traduction terminée{aside}.", { aside }) + (job.skipped ? " " + t("« Reprendre la traduction » réessaie les textes mis de côté.") : ""));
      break;
    }
  } catch (e) {
    trJobMsg(t("Suivi interrompu : {error}", { error: e.message }), true);
  } finally {
    trFollowing = false; TR.running = false;
    try { TR.stats = await (await api(`/api/translate/finish?lang=${target}`, {})).json(); } catch { /* keep counts */ }
    if (target !== LANG) refreshTrStats(); else renderTr();
    if (S.view_tr || S.text) runSearchNow();
  }
}

/* ---- Ollama box, shared by Traduction and Recherche IA: detects Ollama, offers to start it,
   downloads the model with a progress bar, or points to ollama.com when it is not installed. */
const sameModel = (a, b) => {
  const n = (m) => (m.includes(":") ? m : `${m}:latest`);
  return n(a.trim()) === n(b.trim());
};
const fmtGo = (b) => t("{n} Go", { n: (b / 1e9).toLocaleString(LOCALE[LANG], { maximumFractionDigits: 1 }) });
async function ollamaStatus(host) {
  try { return await (await api(`/api/ollama${host ? `?host=${encodeURIComponent(host)}` : ""}`)).json(); } catch { return null; }
}
async function renderOllamaBox(box, model, host) {
  box.dataset.model = model; box.dataset.host = host || "";
  const st = await ollamaStatus(host);
  if (box.dataset.model !== model) return; // the model changed while we waited
  const pull = st?.pulls?.[model];
  const have = st?.models?.find((m) => sameModel(m.name, model));
  const row = (cls, message, actions = "") => `<span class="dot ${cls}"></span><span class="ol-msg">${message}</span>${actions ? `<span class="ol-actions">${actions}</span>` : ""}`;
  const m = `<b>${esc(model)}</b>`;
  let html;
  if (!st) html = row("off", t("Impossible de joindre le serveur de l’application."));
  else if (!st.running && !st.installed) {
    html = row("off", t("Ollama n’est pas installé sur cet ordinateur. Installez-le (gratuit), puis revenez ici."),
      `<a class="btn primary" href="${esc(st.download_url)}" target="_blank" rel="noopener">${t("Télécharger Ollama ↗")}</a>
       <button type="button" class="btn" data-ol="recheck">${t("Vérifier à nouveau")}</button>`);
  } else if (!st.running) {
    html = row("warn", t("Ollama est installé mais ne tourne pas."),
      `<button type="button" class="btn primary" data-ol="start">${t("Démarrer Ollama")}</button>
       <button type="button" class="btn" data-ol="recheck">${t("Vérifier à nouveau")}</button>`);
  } else if (pull?.running) {
    const pctPull = pull.total ? Math.round((100 * pull.completed) / pull.total) : 0;
    const prog = pull.total ? `${pctPull} % (${fmtGo(pull.completed)} / ${fmtGo(pull.total)})` : esc(pull.status || "…");
    html = row("busy", t("Téléchargement de {model} : {progress}…", { model: m, progress: prog })) +
      `<div class="progress"><div style="width:${pctPull}%"></div></div>`;
    setTimeout(() => { if (box.dataset.model === model) renderOllamaBox(box, model, host); }, 1000);
  } else if (have) {
    html = row("on", t("Ollama {version} · modèle {model} installé{size}.",
      { version: esc(st.version || ""), model: m, size: have.size ? ` (${fmtGo(have.size)})` : "" }));
  } else {
    const err = pull?.error ? `<br><span class="err">${t("Le téléchargement a échoué : {error}", { error: esc(fmtMsg(pull.error)) })}</span>` : "";
    html = row("warn", t("Ollama {version} est prêt, mais le modèle {model} n’est pas installé.", { version: esc(st.version || ""), model: m }) + err,
      `<button type="button" class="btn primary" data-ol="pull">${t("Télécharger le modèle")}</button>`);
  }
  if (st && (st.running || st.installed)) { // free the memory once the page is gone
    html += `<label class="ol-auto" title="${esc(t("Environ une minute et demie après la fermeture de la dernière page, les modèles sont retirés de la mémoire et Ollama est arrêté, sauf pendant une traduction ou la construction d’un index. Il redémarre à la prochaine visite."))}">
      <input type="checkbox" data-ol-auto ${st.auto_stop ? "checked" : ""}> ${t("Libérer la mémoire et arrêter Ollama quand la page est fermée")}</label>`;
  }
  box.innerHTML = html;
  if (pull && !pull.running && pull.done && box.dataset.pulled !== model) { // just finished
    box.dataset.pulled = model;
    if (box.id === "aiOllamaBox") loadAI();
  }
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ol]"); if (!b) return;
  const box = b.closest(".ollama-box"), { model, host } = box.dataset;
  b.disabled = true;
  try {
    if (b.dataset.ol === "pull") await api("/api/ollama/pull", { model, host: host || undefined });
    if (b.dataset.ol === "start") {
      await api("/api/ollama/start", {});
      for (let i = 0; i < 15; i++) { await sleep(1000); if ((await ollamaStatus(host))?.running) break; }
    }
  } catch (err) { alert(err.message); }
  renderOllamaBox(box, model, host);
});

document.addEventListener("change", async (e) => {
  const cb = e.target.closest("[data-ol-auto]"); if (!cb) return;
  try { await api("/api/ollama/auto-stop", { on: cb.checked }); } catch (err) { alert(err.message); }
  for (const other of $$("[data-ol-auto]")) other.checked = cb.checked; // both boxes agree
});
/* The server frees Ollama (models out of memory, then stopped) ~90 s after the last page
   is gone: tell it this page is open, and when it closes. */
const alive = () => fetch("/api/alive", { method: "POST" }).catch(() => {});
setInterval(alive, 20000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) alive(); });
window.addEventListener("pagehide", () => navigator.sendBeacon?.("/api/leave"));

/* in Recherche IA, only for a model served by this computer */
const isLocal = (url) => /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(url || "");
function updateAiOllamaBox() {
  const box = $("#aiOllamaBox"), base = $("#aiBase").value, model = $("#aiModel").value.trim();
  box.hidden = !isLocal(base) || !model;
  if (!box.hidden) renderOllamaBox(box, model, base);
}
$("#aiBase").addEventListener("input", debounce(updateAiOllamaBox, 500));
$("#aiModel").addEventListener("input", debounce(updateAiOllamaBox, 500));
$("#optViewTr").addEventListener("change", (e) => { S.view_tr = e.target.checked; runSearchNow(); });
$("#optSearchOrig").addEventListener("change", (e) => { S.search_original = e.target.checked; runSearchNow(); });

/* ---- AI search */
const PRESETS = {
  openai: { base: "https://api.openai.com/v1", model: "text-embedding-3-small" },
  mistral: { base: "https://api.mistral.ai/v1", model: "mistral-embed" },
  ollama: { base: "http://localhost:11434/v1", model: "nomic-embed-text" },
};
$("#aiTopK").addEventListener("change", (e) => { S.ai_top_k = +e.target.value; runSearch(); });
$("#aiPreset").addEventListener("change", (e) => {
  const p = PRESETS[e.target.value]; if (!p) return;
  $("#aiBase").value = p.base; $("#aiModel").value = p.model;
  updateAiOllamaBox();
});
async function loadAI() {
  let info;
  try { info = await (await api("/api/ai")).json(); } catch (e) { aiStatus(null, e.message); return; }
  const s = info.settings;
  $("#aiBase").value = s.base_url; $("#aiModel").value = s.model;
  $("#aiPreset").value = Object.entries(PRESETS).find(([, p]) => p.base === s.base_url)?.[0] || "custom";
  updateAiOllamaBox();
  $("#aiKey").value = ""; $("#aiKey").placeholder = s.key_hint ? t("enregistré ({hint}) — laisser vide pour le conserver", { hint: s.key_hint }) : "sk-…";
  $("#aiRemember").checked = !!store.get("zivi.aiKey", "");
  $("#aiTopK").value = String(S.ai_top_k);
  aiStatus(info);
  if (info.running) pollAI();
}
/* "original", "FR", "DE"…: the AI indexes already built for this model */
const builtNames = (built, model) => Object.keys(built || {}).map((k) => (k === model ? t("texte original") : k.split("@")[1]?.toUpperCase() || k));
function aiStatus(info, err) {
  const el = $("#aiStatus"); el.classList.toggle("err", !!err);
  if (err) { el.textContent = err; return; }
  const skipped = info.skipped ? ` · ${tn(info.skipped, "{n} texte ignoré", "{n} textes ignorés")} (${t("trop long ou trop lent")})` : "";
  if (info.running) {
    el.textContent = t("Construction de l’index : {done} / {total} · lots de {batch}", { done: nf(info.done), total: nf(info.total), batch: info.batch }) + skipped +
      (info.note ? ` · ${fmtMsg(info.note)}` : "");
  } else if (info.error) { el.textContent = t("La dernière construction s’est arrêtée : {error}. Ce qui est fait est gardé ; « Construire l’index » reprend.", { error: fmtMsg(info.error) }); el.classList.add("err"); }
  else if (info.cancelled) el.textContent = t("Arrêtée à {done} / {total}. « Construire l’index » reprend là où elle s’est arrêtée.", { done: nf(info.done), total: nf(info.total) });
  else if (info.ready) el.textContent = t("Index IA prêt : {n} affectations ({model}){skipped}. Choisissez « IA » ou « Les deux » à côté du champ de recherche.", { n: nf(info.vectors), model: info.model, skipped });
  else el.textContent = t("Pas encore d’index IA pour ce modèle dans cette langue.");
  // what the next build would do: new assignments, earlier failures, newly translated texts
  if (!info.running && !info.error && info.pending) {
    el.textContent += " " + t("{count} : « Construire l’index » s’en charge.",
      { count: tn(info.pending, "{n} affectation est à calculer", "{n} affectations sont à calculer") });
  }
  if (info.coverage) {
    const c = info.coverage;
    $("#aiTranslated").checked = !!info.translated;
    const built = builtNames(info.built, info.settings?.model);
    $("#aiTranslatedHint").textContent = t("Utilise la traduction dans la langue de l’interface quand elle existe (Traduction), sinon le texte original : {have} affectations sur {total} sont en {lang} ({native} d’origine, {translated} traduites). Chaque langue garde son propre index.",
      { have: nf(c.native + c.translated), total: nf(c.total), lang: langName(c.lang), native: nf(c.native), translated: nf(c.translated) }) +
      (built.length ? " " + t("Index déjà construits : {list}.", { list: built.join(", ") }) : "");
  }
  $("#aiStop").hidden = !info.running;
  $("#aiBuild").disabled = $("#aiRebuild").disabled = !!info.running;
  const badge = info.running && info.total ? `${Math.round((100 * info.done) / info.total)} %` : "";
  $("#aiNavBadge").hidden = !badge; $("#aiNavBadge").textContent = badge;
}
async function saveAI() {
  const body = { base_url: $("#aiBase").value, model: $("#aiModel").value, lang: LANG };
  const key = $("#aiKey").value.trim();
  if (key) body.api_key = key;
  if ($("#aiRemember").checked && key) store.set("zivi.aiKey", key);
  if (!$("#aiRemember").checked) store.del("zivi.aiKey");
  return (await api("/api/ai/config", body)).json();
}
$("#aiTest").addEventListener("click", async () => {
  try {
    await saveAI(); const r = await (await api("/api/ai/test", {})).json();
    $("#aiStatus").classList.remove("err");
    $("#aiStatus").textContent = t("Connecté. Le modèle renvoie des vecteurs de {n} dimensions.", { n: r.dimensions });
  } catch (e) { aiStatus(null, e.message); }
});
let aiPolling = false;
function pollAI() {
  if (aiPolling) return;
  aiPolling = true;
  $("#aiProg").hidden = false;
  const poll = async () => {
    let info;
    try { info = await (await api("/api/ai")).json(); } catch { aiPolling = false; return; }
    aiStatus(info);
    $("#aiBar").style.width = !info.running && !info.error ? "100%" : info.total ? `${Math.round(100 * info.done / info.total)}%` : "5%";
    if (info.running) { setTimeout(poll, 1000); return; }
    aiPolling = false;
    if (S.text_mode !== "keyword") runSearchNow();
  };
  poll();
}
async function startAI(rebuild) {
  if (rebuild && !confirm(t("Tout reconstruire ? Les vecteurs de ce modèle (dans cette langue) seront recalculés pour toutes les affectations."))) return;
  try { await saveAI(); await api("/api/ai/index", { rebuild }); } catch (e) { aiStatus(null, e.message); return; }
  pollAI();
}
$("#aiBuild").addEventListener("click", () => startAI(false));
$("#aiTranslated").addEventListener("change", async (e) => {
  try { await api("/api/ai/config", { translated: e.target.checked, lang: LANG }); } catch (err) { aiStatus(null, err.message); return; }
  loadAI(); // the count of assignments to (re)compute changes
});
$("#aiRebuild").addEventListener("click", () => startAI(true));
$("#aiStop").addEventListener("click", async () => { await api("/api/ai/stop", {}); $("#aiStatus").textContent = t("Arrêt après le lot en cours…"); });
(async () => { // restore a remembered token into the local server after a restart
  const key = store.get("zivi.aiKey", "");
  if (key) { try { await api("/api/ai/config", { api_key: key }); } catch { /* ignore */ } }
})();

/* ------------------------------------------------------------------ language menu */
function renderLangMenu() {
  $("#langBtn").innerHTML = `${FLAGS[LANG]}<span class="lang-code">${LANG.toUpperCase()}</span>
    <svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  $("#langBtn").setAttribute("aria-label", `${t("Langue")}${COLON()}${LANG_NATIVE[LANG]}`);
  $("#langList").innerHTML = Object.keys(LANG_NATIVE).map((l) => `<li role="option" tabindex="-1" data-lang="${l}" aria-selected="${l === LANG}" lang="${l}">
    ${FLAGS[l]}<span>${LANG_NATIVE[l]}</span>${l === LANG ? '<span class="tick" aria-hidden="true">✓</span>' : ""}</li>`).join("");
}
function setLangMenu(open) {
  $("#langList").hidden = !open;
  $("#langBtn").setAttribute("aria-expanded", String(open));
  if (open) ($(`#langList [aria-selected=true]`) || $("#langList li")).focus();
}
$("#langBtn").addEventListener("click", () => setLangMenu($("#langList").hidden));
$("#langList").addEventListener("click", (e) => { const li = e.target.closest("[data-lang]"); if (li) chooseLang(li.dataset.lang); });
$("#langList").addEventListener("keydown", (e) => {
  const items = $$("#langList li"), i = items.indexOf(document.activeElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus(); }
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (i >= 0) chooseLang(items[i].dataset.lang); }
});
async function chooseLang(lang) {
  setLangMenu(false);
  if (lang === LANG) return;
  setLang(lang);
  try { await api("/api/prefs", { lang }); } catch { /* the server keeps the previous language */ }
  refreshUi();
}
/* everything drawn by the script, in the new language */
function refreshUi() {
  renderLangMenu(); renderLayerControl(); renderCenters(); renderFullBtn(); renderFitBtn();
  runSearchNow();
  if (openId != null && !$("#drawer").hidden) openDetail(openId);
  if ($("#dbDlg").open) showSection(panelSec); else refreshTrStats();
  if (!$("#adv").hidden) fillAdvanced();
}

/* ------------------------------------------------------------------ export, tabs, start */
$("#exportBtn").addEventListener("click", async () => {
  const r = await api("/api/export", query());
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: S.favorites_only ? "zivi_favoris.csv" : "zivi_resultats.csv" });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
});
function showPane(id) {
  $$(".pane").forEach((p) => p.classList.toggle("on", p.id === id));
  $$(".tabs button").forEach((b) => b.classList.toggle("on", (b.dataset.pane === "map" ? "mapPane" : b.dataset.pane) === id));
  if (id === "mapPane") setTimeout(() => map.invalidateSize(), 50);
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showPane(b.dataset.pane === "map" ? "mapPane" : b.dataset.pane)));
showPane("results");

setLang(LANG); // translates the static page
renderLangMenu();
renderLayerControl();
setMapFull(!!store.get("zivi.mapFull", false));
bindInputs();
$("#optViewTr").checked = !!S.view_tr;
$("#optSearchOrig").checked = S.search_original !== false;
$(`input[name=trEngine][value=${store.get("zivi.trEngine", "ollama") === "browser" ? "browser" : "ollama"}]`).checked = true;
$("#favOnly").setAttribute("aria-pressed", String(!!S.favorites_only));
document.body.classList.toggle("fav-mode", !!S.favorites_only);
api("/api/prefs", { lang: LANG }).catch(() => {}); // translations and AI index follow the interface
alive(); // restarts Ollama if it was stopped when the last page left
loadTrSettings().then(refreshTrStats);
loadLabels().finally(runSearchNow);
