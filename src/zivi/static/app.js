/* Zivi search — front end. Plain JS + Leaflet; talks to the local FastAPI server. */
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
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch { /* text */ } throw new Error(m); }
  return r;
}

const JNM = { J: "Oui", N: "Non", M: "Possible" };
const YN = { 1: "Oui", 0: "Non" };
const LANG = { de: "Allemand", fr: "Français", it: "Italien" };
const PAGE = 50;
const nf = (n) => n.toLocaleString("fr-CH");
const plural = (n, one, many) => `${nf(n)} ${n === 1 ? one : many}`;

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
const sectorName = (label) => String(label ?? "").replace(/^\s*\d+\s*/, "") || "Autre";

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
});
let S = Object.assign(blank(), store.get("zivi.state", {}));
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
L.control.layers({ "Carte nationale (gris)": swissGrey, "Carte nationale (couleur)": swiss, "OpenStreetMap": osm }, null, { position: "topright" }).addTo(map);
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
      <p class="pop-actions"><button class="btn primary" data-open="${id}">Détails</button> ${favBtn(id)}</p>`);
    if (!markersById.has(id)) markersById.set(id, []);
    markersById.get(id).push(m);
    return m;
  });
  cluster.addLayers(markers);
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
  addCenter({ label: `Point ${e.latlng.lat.toFixed(3)} ; ${e.latlng.lng.toFixed(3)}`, lat: e.latlng.lat, lon: e.latlng.lng });
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
  showWarnings(res.warnings || []);
  $("#list").innerHTML = res.items.map(itemHtml).join("");
  $("#more").hidden = res.items.length >= res.total;
  renderEmpty(res);
  renderFacets(res.facets || {});
  drawPoints(res.points || []);
  setFavCount(res.favorites);
  $("#mapInfo").textContent = plural(res.points.length, "lieu sur la carte", "lieux sur la carte") +
    (res.unlocated ? ` · ${plural(res.unlocated, "résultat sans localisation", "résultats sans localisation")}` : "");
}
const runSearch = debounce(runSearchNow, 250);

async function loadMore() {
  offset += PAGE;
  const res = await (await api("/api/search", query({ offset }))).json();
  $("#list").insertAdjacentHTML("beforeend", res.items.map(itemHtml).join(""));
  $("#more").hidden = offset + res.items.length >= res.total;
}

function renderCount(res) {
  const mode = res.mode_used === "semantic" ? " · classées par l’IA" : res.mode_used === "hybrid" ? " · mots-clés + IA" : "";
  const favNote = S.favorites_only ? " · favoris uniquement" : "";
  $("#count").innerHTML = `<b>${nf(res.total)}</b> ${res.total === 1 ? "affectation" : "affectations"}${favNote}${mode}`;
  $("#sort").value = S.sort || "";
}
function showWarnings(ws) { $("#warnings").innerHTML = ws.map((w) => `<p>${esc(w)}</p>`).join(""); }
function renderEmpty(res) {
  const el = $("#empty");
  if (res.total) { el.hidden = true; return; }
  el.hidden = false;
  if ((res.warnings || []).some((w) => w.startsWith("Aucune donnée"))) {
    el.innerHTML = `<p>Aucune affectation chargée pour l’instant.</p><label class="btn primary" for="fileInput">Charger un export JSON</label>`;
  } else if (S.favorites_only && !res.favorites) {
    el.innerHTML = `<p>Vous n’avez encore aucun favori. Cliquez sur l’étoile ☆ d’une affectation pour l’ajouter.</p>
      <button class="btn" type="button" id="emptyFav">Afficher toutes les affectations</button>`;
    $("#emptyFav").onclick = () => setFavoritesOnly(false);
  } else {
    el.innerHTML = `<p>Aucune affectation ne correspond à tous ces filtres.</p><button class="btn" type="button" id="emptyReset">Effacer tous les filtres</button>`;
    $("#emptyReset").onclick = resetAll;
  }
}

function itemHtml(it) {
  const facts = [];
  if (it.favorite) favs.add(it.id); else favs.delete(it.id);
  if (it.min_weeks) facts.push(`min. ${it.min_weeks} sem.`);
  if (it.hours_per_week) facts.push(`${it.hours_per_week} h/sem.`);
  if (it.housing) facts.push('<span class="yes">Logement</span>');
  if (it.meals) facts.push('<span class="yes">Repas</span>');
  if (it.weekend === "J") facts.push("Week-ends");
  if (it.night === "J") facts.push("Nuits");
  if (it.first_week) facts.push(`dès le ${fmtDate(it.first_week)}`);
  if (it.canton) facts.push(it.canton.replace(/,/g, ", "));
  const dist = it.distance_km != null ? `<span class="dist">${it.distance_km} km</span>` : "";
  const snip = it.snippet ? `<p class="snip">${sanitizeSnippet(it.snippet)}</p>` : "";
  const sec = it.sector ? `<span class="sec" style="--c:${sectorColor(sectorCode(it.sector))}">${esc(sectorName(it.sector))}</span>` : "";
  return `<li class="item" data-id="${it.id}" tabindex="0">${favBtn(it.id)}${dist}
    <h4>${esc(it.title)}</h4>${sec}
    <div class="who">${esc(it.employer)} · ${esc(it.place || "")}</div>
    <div class="facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
    <div class="facts">${(it.task_areas || []).map(esc).join(", ")}</div>${snip}</li>`;
}
function sanitizeSnippet(s) { return esc(s).replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>"); }
function fmtDate(iso) { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; }

/* ------------------------------------------------------------------ facets */
function renderFacets(f) {
  for (const el of $$(".tri")) renderTri(el, f[el.dataset.field] || []);
  for (const el of $$(".multi")) renderMulti(el, f[el.dataset.field] || []);
  renderChecks("task_areas", f.task_areas || [], (v) => v);
  renderChecks("canton", f.canton || [], (v) => v);
  renderChecks("language", f.language || [], (v) => LANG[v] || v, "Langue");
  renderChecks("sector", f.sector || [], (v) => v, "Domaine d’activité");
  renderChecks("work_model", f.work_model || [], (v) => v, "Modèle d’horaire");
}
function renderTri(el, counts) {
  const c = Object.fromEntries(counts.map(([k, n]) => [String(k), n]));
  const field = el.dataset.field, cur = S[field];
  const opts = [["any", "Tous", (c[1] || 0) + (c[0] || 0)], [1, "Oui", c[1] || 0], [0, "Non", c[0] || 0]];
  let box = el.querySelector(".opts");
  if (!box) { box = document.createElement("div"); box.className = "opts"; el.append(box); }
  box.innerHTML = opts.map(([v, l, n]) => `<button type="button" data-v="${v}" aria-pressed="${String(cur === null ? v === "any" : cur === v)}">${l} <small>${n}</small></button>`).join("");
}
function renderMulti(el, counts) {
  const c = Object.fromEntries(counts);
  const field = el.dataset.field;
  el.querySelector(".opts").innerHTML = ["J", "M", "N"].map((v) =>
    `<button type="button" data-v="${v}" aria-pressed="${S[field].includes(v)}">${JNM[v]} <small>${c[v] || 0}</small></button>`).join("");
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
    <input type="number" min="1" max="300" value="${c.radius_km}" data-i="${i}" aria-label="Rayon en km pour ${esc(c.label)}"> km
    <button type="button" data-del="${i}" aria-label="Retirer ${esc(c.label)}">×</button></li>`).join("");
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
  ul.innerHTML = sugItems.length ? sugItems.map((s, i) => `<li role="option" data-i="${i}">${esc(s.label)}</li>`).join("") : "<li>Aucun lieu trouvé</li>";
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
$("#q").addEventListener("input", (e) => { S.text = e.target.value; runSearch(); });
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
$("#more").addEventListener("click", loadMore);
function resetAll() {
  const keep = { text_mode: S.text_mode, ai_top_k: S.ai_top_k, favorites_only: S.favorites_only };
  S = Object.assign(blank(), keep); bindInputs(); runSearchNow();
}
$("#resetBtn").addEventListener("click", resetAll);

/* ------------------------------------------------------------------ favourites */
function favBtn(id) {
  const on = favs.has(id);
  return `<button type="button" class="fav${on ? " on" : ""}" data-fav="${id}" aria-pressed="${on}"
    title="${on ? "Retirer des favoris" : "Ajouter aux favoris"}" aria-label="${on ? "Retirer des favoris" : "Ajouter aux favoris"}">${on ? "★" : "☆"}</button>`;
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
async function openDetail(id) {
  let d;
  try { d = await (await api(`/api/assignment/${id}`)).json(); } catch (e) { alert(e.message); return; }
  if (d.favorite) favs.add(id); else favs.delete(id);
  const fact = (k, v) => (v === null || v === undefined || v === "" ? "" : `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`);
  const sec = (title, text) => (text ? `<section class="d-sec"><h3>${title}</h3><p>${esc(text)}</p></section>` : "");
  const addr = (d.locations || []).map((l) => l.label || [l.plz, l.city].filter(Boolean).join(" ")).filter(Boolean);
  $("#drawerBody").innerHTML = `
    <p class="d-num">N° ${esc(d.number)}${d.canton ? " · " + esc(d.canton) : ""}${d.sector ? ` · <span class="sec" style="--c:${sectorColor(sectorCode(d.sector))}">${esc(sectorName(d.sector))}</span>` : ""}</p>
    <h2 class="d-title" id="dTitle">${esc(d.title)}</h2>
    <p class="d-who">${esc(d.employer)} · ${esc(d.place || "")}</p>
    <p class="d-actions"><a class="btn primary" href="${esc(d.url)}" target="_blank" rel="noopener">Ouvrir sur ZiviConnect</a>
       ${d.locations.some((l) => l.lat != null) ? '<button class="btn" type="button" id="zoomTo">Voir sur la carte</button>' : ""}
       ${favBtn(id)}</p>
    <dl class="d-grid">
      ${fact("Durée minimale", d.min_weeks ? d.min_weeks + " semaines" : "")}
      ${fact("Heures par semaine", d.hours_per_week)}
      ${fact("Modèle d’horaire", d.work_model)}
      ${fact("Logement", YN[d.housing])}${fact("Repas", YN[d.meals])}
      ${fact("Travail le week-end", JNM[d.weekend])}${fact("Travail de nuit", JNM[d.night])}
      ${fact("Contrôle de réputation", YN[d.reputation_check])}
      ${fact("Programme prioritaire", YN[d.priority_program])}
      ${fact("À l’étranger", d.abroad ? "Oui" : "")}
    </dl>
    <p>${(d.task_areas || []).map(esc).join(", ")}</p>
    ${sec("Tâches", d.tasks)}
    ${sec("Compétences requises", d.required_skills)}
    ${sec("Compétences souhaitées", d.desired_skills)}
    ${sec("Cours du service civil", d.courses)}
    <section class="d-sec"><h3>Semaines de début disponibles</h3>${calendar(d.weeks || [])}</section>
    ${sec("À propos de l’établissement", d.employer_description)}
    ${sec("Adresse de l’affectation", addr.join("\n"))}
    <section class="d-sec"><h3>Contact</h3><p>${esc([d.contact_name, d.contact_role].filter(Boolean).join(", "))}
${d.contact_phone ? `<a href="tel:${esc(d.contact_phone.replace(/\s/g, ""))}">${esc(d.contact_phone)}</a>` : ""}
${d.contact_email ? `<a href="mailto:${esc(d.contact_email)}">${esc(d.contact_email)}</a>` : ""}</p></section>
    ${sec("Comment postuler", d.how_to_apply)}`;
  const z = $("#zoomTo");
  if (z) z.onclick = () => {
    closeDrawer(); showPane("mapPane");
    const l = d.locations.find((x) => x.lat != null); map.setView([l.lat, l.lon], 13);
    const m = (markersById.get(id) || [])[0];
    if (m) cluster.zoomToShowLayer(m, () => m.openPopup());
  };
  $("#drawer").hidden = false; $("#drawerClose").focus();
}
function calendar(weeks) {
  if (!weeks.length) return "<p>Aucune date publiée.</p>";
  const { from, to } = S.period;
  const months = {};
  for (const w of weeks) (months[w.slice(0, 7)] ||= []).push(w);
  const name = (ym) => new Date(ym + "-15").toLocaleDateString("fr-CH", { month: "long", year: "numeric" });
  return `<div class="cal">${Object.entries(months).map(([ym, ws]) => `<div class="m"><b>${name(ym)}</b>${ws.map((w) =>
    `<span class="${(from && w >= from && (!to || w <= to)) || (!from && to && w <= to) ? "in" : ""}">${w.slice(8)}.${w.slice(5, 7)}</span>`).join("")}</div>`).join("")}</div>`;
}
function closeDrawer() { $("#drawer").hidden = true; }
$("#drawerClose").addEventListener("click", closeDrawer);
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer(); });

/* ------------------------------------------------------------------ import */
$("#fileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  const dlg = $("#importDlg");
  $("#impMsg").textContent = `Envoi de ${file.name} (${(file.size / 1048576).toFixed(1).replace(".", ",")} Mo)…`;
  $("#impBar").style.width = "2%"; $("#impReport").hidden = true; $("#impClose").disabled = true;
  dlg.showModal();
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch("/api/import", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
  } catch (err) { $("#impMsg").textContent = `Échec de l’envoi : ${err.message}`; $("#impClose").disabled = false; return; }
  const poll = async () => {
    const st = await (await api("/api/import/status")).json();
    $("#impMsg").textContent = st.error ? `Échec de l’import : ${st.error}` : st.message;
    $("#impBar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
    if (st.running) { setTimeout(poll, 700); return; }
    $("#impClose").disabled = false;
    if (!st.error) $("#impBar").style.width = "100%";
    if (st.report) {
      const r = st.report;
      $("#impMsg").textContent = `${nf(r.assignments)} affectations chargées — ${nf(r.with_location)} placées sur la carte, ${nf(r.with_weeks)} avec des dates de début.`;
      const extra = { unknown_fields: r.unknown_detail_fields, missing_fields: r.fields_missing_in_some_records };
      if (Object.keys(r.unknown_detail_fields || {}).length || Object.keys(r.fields_missing_in_some_records || {}).length) {
        $("#impReport").textContent = JSON.stringify(extra, null, 2); $("#impReport").hidden = false;
      }
      fitted = false; runSearchNow();
    }
  };
  poll();
});

/* ------------------------------------------------------------------ AI */
const PRESETS = {
  openai: { base: "https://api.openai.com/v1", model: "text-embedding-3-small" },
  mistral: { base: "https://api.mistral.ai/v1", model: "mistral-embed" },
  ollama: { base: "http://localhost:11434/v1", model: "nomic-embed-text" },
};
$("#aiTopK").addEventListener("change", (e) => { S.ai_top_k = +e.target.value; runSearch(); });
$("#aiPreset").addEventListener("change", (e) => {
  const p = PRESETS[e.target.value]; if (!p) return;
  $("#aiBase").value = p.base; $("#aiModel").value = p.model;
});
$("#aiBtn").addEventListener("click", async () => {
  const info = await (await api("/api/ai")).json();
  const s = info.settings;
  $("#aiBase").value = s.base_url; $("#aiModel").value = s.model;
  $("#aiPreset").value = Object.entries(PRESETS).find(([, p]) => p.base === s.base_url)?.[0] || "custom";
  $("#aiKey").value = ""; $("#aiKey").placeholder = s.key_hint ? `enregistré (${s.key_hint}) — laisser vide pour le conserver` : "sk-…";
  $("#aiRemember").checked = !!store.get("zivi.aiKey", "");
  $("#aiTopK").value = String(S.ai_top_k);
  aiStatus(info);
  $("#aiDlg").showModal();
});
function aiStatus(info, err) {
  const el = $("#aiStatus"); el.classList.toggle("err", !!err);
  if (err) { el.textContent = err; return; }
  if (info.running) el.textContent = `Construction de l’index : ${nf(info.done)} / ${nf(info.total)}`;
  else if (info.error) { el.textContent = `La dernière construction a échoué : ${info.error}`; el.classList.add("err"); }
  else if (info.ready) el.textContent = `Index IA prêt : ${nf(info.vectors)} affectations (${info.model}). Choisissez « IA » ou « Les deux » à côté du champ de recherche.`;
  else el.textContent = "Pas encore d’index IA pour ce modèle.";
}
async function saveAI() {
  const body = { base_url: $("#aiBase").value, model: $("#aiModel").value };
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
    $("#aiStatus").textContent = `Connecté. Le modèle renvoie des vecteurs de ${r.dimensions} dimensions.`;
  } catch (e) { aiStatus(null, e.message); }
});
$("#aiBuild").addEventListener("click", async () => {
  try { await saveAI(); await api("/api/ai/index", {}); } catch (e) { aiStatus(null, e.message); return; }
  $("#aiProg").hidden = false;
  const poll = async () => {
    const info = await (await api("/api/ai")).json();
    aiStatus(info);
    $("#aiBar").style.width = !info.running && !info.error ? "100%" : info.total ? `${Math.round(100 * info.done / info.total)}%` : "5%";
    if (info.running) setTimeout(poll, 800);
    else if (S.text_mode !== "keyword") runSearchNow();
  };
  poll();
});
(async () => { // restore a remembered token into the local server after a restart
  const key = store.get("zivi.aiKey", "");
  if (key) { try { await api("/api/ai/config", { api_key: key }); } catch { /* ignore */ } }
})();

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

bindInputs();
$("#favOnly").setAttribute("aria-pressed", String(!!S.favorites_only));
document.body.classList.toggle("fav-mode", !!S.favorites_only);
runSearchNow();
