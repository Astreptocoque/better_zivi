/* Better Zivi — the writers of the entry export: one entry ("doc", built by entryDoc() in
   app.js) to Markdown, HTML or PDF. Plain functions, no library.

   doc = { lang, title, head, who, url, urlLabel, facts: [[label, value]], sections: [[heading, text]],
           footer, colon, map: { dataUrl, width, height, alt } | null } */
"use strict";

/* ------------------------------------------------------------------ map of Switzerland
   The national map (swisstopo, grey, as in the app) at zoom 8, cut to Switzerland, with the
   places of the entry marked. The tiles are drawn once, then copied for each entry. */
const MAP_Z = 8;
const MAP_BOX = { north: 47.88, west: 5.9, south: 45.73, east: 10.55 }; // Switzerland and a small margin
const mapTile = (x, y) => `https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/${MAP_Z}/${x}/${y}.jpeg`;
function worldPx(lat, lon) { // Web Mercator pixels at MAP_Z
  const n = 256 * 2 ** MAP_Z, s = Math.sin((lat * Math.PI) / 180);
  return [((lon + 180) / 360) * n, (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n];
}
function loadTile(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous"; // swisstopo allows it: the canvas stays exportable
    const timer = setTimeout(() => resolve(null), 15000);
    img.onload = () => { clearTimeout(timer); resolve(img); };
    img.onerror = () => { clearTimeout(timer); resolve(null); };
    img.src = url;
  });
}
let mapBase = null;
function baseMap() {
  mapBase ||= (async () => {
    const [x0, y0] = worldPx(MAP_BOX.north, MAP_BOX.west), [x1, y1] = worldPx(MAP_BOX.south, MAP_BOX.east);
    const ox = Math.floor(x0), oy = Math.floor(y0);
    const canvas = Object.assign(document.createElement("canvas"), { width: Math.ceil(x1) - ox, height: Math.ceil(y1) - oy });
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#F2F2F2"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    const jobs = [];
    for (let tx = Math.floor(x0 / 256); tx <= Math.floor((x1 - 1) / 256); tx++) {
      for (let ty = Math.floor(y0 / 256); ty <= Math.floor((y1 - 1) / 256); ty++) {
        jobs.push(loadTile(mapTile(tx, ty)).then((img) => { if (img) ctx.drawImage(img, tx * 256 - ox, ty * 256 - oy); return !!img; }));
      }
    }
    const ok = (await Promise.all(jobs)).filter(Boolean).length;
    if (ok < jobs.length) mapBase = null; // offline or a tile missing: try again on the next export
    return ok ? { canvas, ox, oy } : null;
  })();
  return mapBase;
}
/* the map with the places ({lat, lon}) marked; a single place also gets its name. null when
   there is nothing to mark, or no map could be loaded. */
async function swissMap(places, label, alt) {
  const pts = (places || []).filter((p) => p.lat != null && p.lon != null);
  if (!pts.length) return null;
  const base = await baseMap();
  if (!base) return null;
  const { canvas: bc, ox, oy } = base;
  const c = Object.assign(document.createElement("canvas"), { width: bc.width, height: bc.height });
  const ctx = c.getContext("2d");
  ctx.drawImage(bc, 0, 0);
  const xy = pts.map((p) => { const [x, y] = worldPx(p.lat, p.lon); return [x - ox, y - oy]; })
    .filter(([x, y]) => x >= 0 && y >= 0 && x <= c.width && y <= c.height);
  if (!xy.length) return null; // abroad: Switzerland alone would not show it
  for (const [x, y] of xy) {
    ctx.beginPath(); ctx.arc(x, y, 14, 0, 2 * Math.PI); ctx.fillStyle = "rgba(213, 43, 30, .22)"; ctx.fill();
    ctx.beginPath(); ctx.arc(x, y, 7, 0, 2 * Math.PI); ctx.fillStyle = "#D52B1E"; ctx.fill();
    ctx.lineWidth = 2.5; ctx.strokeStyle = "#FFFFFF"; ctx.stroke();
  }
  const tag = (text, x, y, font, pad, color) => { // text on a white box, kept inside the map
    ctx.font = font;
    const w = ctx.measureText(text).width + 2 * pad, h = parseInt(font.match(/(\d+)px/)[1], 10) + 2 * pad;
    const bx = Math.min(Math.max(x, 4), c.width - w - 4), by = Math.min(Math.max(y, 4), c.height - h - 4);
    ctx.fillStyle = "rgba(255, 255, 255, .9)"; ctx.fillRect(bx, by, w, h);
    ctx.fillStyle = color; ctx.textBaseline = "top"; ctx.fillText(text, bx + pad, by + pad + 1);
  };
  if (label && xy.length === 1) {
    const [x, y] = xy[0];
    ctx.font = "bold 15px Arial, Helvetica, sans-serif";
    const right = x + 16 + ctx.measureText(label).width + 14 < c.width; // else on the left of the point
    tag(label, right ? x + 16 : x - 16 - ctx.measureText(label).width - 12, y - 12, "bold 15px Arial, Helvetica, sans-serif", 6, "#17201D");
  }
  ctx.font = "11px Arial, Helvetica, sans-serif";
  tag("© swisstopo", c.width - ctx.measureText("© swisstopo").width - 12, c.height, "11px Arial, Helvetica, sans-serif", 3, "#55635E");
  ctx.lineWidth = 1; ctx.strokeStyle = "#D7DFDB"; ctx.strokeRect(0.5, 0.5, c.width - 1, c.height - 1);
  return { dataUrl: c.toDataURL("image/jpeg", 0.86), width: c.width, height: c.height, alt };
}

/* ------------------------------------------------------------------ ZIP
   Several exported files in one download. Files are added one by one and kept as Blobs (the
   browser may keep large ones on disk); compressed with the browser's deflate when it has
   one, else stored. */
const CRC_TABLE = (() => {
  const tbl = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    tbl[n] = c >>> 0;
  }
  return tbl;
})();
function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
async function deflateRaw(bytes) {
  if (typeof CompressionStream === "undefined") return null;
  try {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch { return null; }
}
class ZipWriter {
  constructor() {
    this.parts = []; this.central = []; this.offset = 0; this.count = 0;
    const d = new Date();
    this.time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
    this.date = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  }
  /* data: a string (written as UTF-8) or bytes */
  async add(name, data) {
    const raw = typeof data === "string" ? new TextEncoder().encode(data) : data;
    const crc = crc32(raw);
    const packed = await deflateRaw(raw);
    const [body, method] = packed && packed.length < raw.length ? [packed, 8] : [raw, 0];
    const fname = new TextEncoder().encode(name);
    const head = new DataView(new ArrayBuffer(30));
    head.setUint32(0, 0x04034b50, true); head.setUint16(4, 20, true); head.setUint16(6, 0x0800, true); // UTF-8 names
    head.setUint16(8, method, true); head.setUint16(10, this.time, true); head.setUint16(12, this.date, true);
    head.setUint32(14, crc, true); head.setUint32(18, body.length, true); head.setUint32(22, raw.length, true);
    head.setUint16(26, fname.length, true); head.setUint16(28, 0, true);
    this.parts.push(new Blob([head, fname, body]));
    const cen = new DataView(new ArrayBuffer(46));
    cen.setUint32(0, 0x02014b50, true); cen.setUint16(4, 20, true); cen.setUint16(6, 20, true);
    cen.setUint16(8, 0x0800, true); cen.setUint16(10, method, true); cen.setUint16(12, this.time, true);
    cen.setUint16(14, this.date, true); cen.setUint32(16, crc, true); cen.setUint32(20, body.length, true);
    cen.setUint32(24, raw.length, true); cen.setUint16(28, fname.length, true); // extra, comment, disk, attributes: 0
    cen.setUint32(42, this.offset, true);
    this.central.push(cen, fname);
    this.offset += 30 + fname.length + body.length;
    this.count += 1;
  }
  blob() {
    const size = this.central.reduce((a, p) => a + p.byteLength, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, this.count, true); end.setUint16(10, this.count, true);
    end.setUint32(12, size, true); end.setUint32(16, this.offset, true);
    return new Blob([...this.parts, ...this.central, end], { type: "application/zip" });
  }
}

/* ------------------------------------------------------------------ Markdown */
function docToMarkdown(doc) {
  const inline = (s) => String(s ?? "").replace(/([\\`*_[\]<>])/g, "\\$1");
  // keep the line breaks of the texts (two spaces = a line break), and a "#" at the start of
  // a line stays a character, not a heading
  const block = (s) => inline(s).split("\n").map((l) => l.replace(/^(\s*)(#|>|[-+](?=\s)|\d+\.(?=\s))/, "$1\\$2").trimEnd())
    .join("  \n").replace(/( {2}\n){2,}/g, "\n\n");
  const out = [`# ${inline(doc.title)}`, "", inline(doc.head)];
  if (doc.who) out.push("", `**${inline(doc.who)}**`);
  if (doc.url) out.push("", `[${inline(doc.urlLabel)}](<${doc.url}>)`);
  if (doc.facts.length) out.push("", ...doc.facts.map(([k, v]) => `- **${inline(k)}**${doc.colon}${inline(v)}`));
  // the image is inside the file (data URL), so the file stays on its own
  if (doc.map) out.push("", `![${inline(doc.map.alt)}](${doc.map.dataUrl})`);
  for (const [h, text] of doc.sections) out.push("", `## ${inline(h)}`, "", block(text));
  out.push("", "---", "", `*${inline(doc.footer)}*`, "");
  return out.join("\n");
}

/* ------------------------------------------------------------------ HTML */
function docToHtml(doc) {
  const e = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  return `<!doctype html>
<html lang="${e(doc.lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${e(doc.title)}</title>
<style>
  :root { --ink: #17201D; --slate: #55635E; --spruce: #1F4D3F; --line: #D7DFDB; --bg: #FFFFFF; --soft: #EEF2F0; }
  @media (prefers-color-scheme: dark) {
    :root { --ink: #E7EEEB; --slate: #9DADA7; --spruce: #7FC4A8; --line: #2C3833; --bg: #1A221F; --soft: #121816; }
  }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 46rem; margin: 0 auto; padding: 2rem 1rem 3rem; }
  .head { color: var(--slate); font-size: .9rem; margin: 0; }
  h1 { color: var(--spruce); font-size: 1.7rem; line-height: 1.25; margin: .3rem 0 .4rem; }
  .who { color: var(--slate); margin: 0 0 .8rem; }
  a { color: var(--spruce); }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: .3rem 1.2rem; margin: 1.2rem 0;
       padding: .9rem 1rem; background: var(--soft); border-radius: 8px; }
  dt { color: var(--slate); }
  dd { margin: 0; }
  h2 { color: var(--spruce); font-size: 1.1rem; margin: 1.6rem 0 .4rem; padding-bottom: .2rem; border-bottom: 1px solid var(--line); }
  figure.map { margin: 0 0 1.2rem; }
  figure.map img { display: block; width: 100%; height: auto; border-radius: 8px; }
  section p { white-space: pre-line; margin: 0; overflow-wrap: anywhere; }
  footer { margin-top: 2.5rem; color: var(--slate); font-size: .85rem; border-top: 1px solid var(--line); padding-top: .6rem; }
  @media (max-width: 30rem) { dl { grid-template-columns: 1fr; gap: 0; } dd { margin-bottom: .4rem; } }
  @media print { body { background: #fff; color: #000; } dl { background: none; padding: 0; } }
</style>
</head>
<body>
<main>
  <p class="head">${e(doc.head)}</p>
  <h1>${e(doc.title)}</h1>
  ${doc.who ? `<p class="who">${e(doc.who)}</p>` : ""}
  ${doc.url ? `<p><a href="${e(doc.url)}">${e(doc.urlLabel)}</a></p>` : ""}
  ${doc.facts.length ? `<dl>${doc.facts.map(([k, v]) => `<dt>${e(k)}</dt><dd>${e(v)}</dd>`).join("")}</dl>` : ""}
  ${doc.map ? `<figure class="map"><img src="${doc.map.dataUrl}" width="${doc.map.width}" height="${doc.map.height}" alt="${e(doc.map.alt)}"></figure>` : ""}
  ${doc.sections.map(([h, text]) => `<section><h2>${e(h)}</h2><p>${e(text)}</p></section>`).join("\n  ")}
  <footer>${e(doc.footer)}</footer>
</main>
</body>
</html>
`;
}

/* ------------------------------------------------------------------ PDF
   Helvetica, built into every PDF reader, in the Windows-1252 encoding (enough for French,
   German and Italian). Lines are measured with the browser's Arial, which has the same
   metrics. A character Windows-1252 lacks loses its accent, or becomes "?". */
const CP1252 = {
  0x20ac: 0x80, 0x201a: 0x82, 0x0192: 0x83, 0x201e: 0x84, 0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87,
  0x02c6: 0x88, 0x2030: 0x89, 0x0160: 0x8a, 0x2039: 0x8b, 0x0152: 0x8c, 0x017d: 0x8e, 0x2018: 0x91,
  0x2019: 0x92, 0x201c: 0x93, 0x201d: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02dc: 0x98,
  0x2122: 0x99, 0x0161: 0x9a, 0x203a: 0x9b, 0x0153: 0x9c, 0x017e: 0x9e, 0x0178: 0x9f,
};
function pdfChar(ch) {
  const c = ch.codePointAt(0);
  if (c === 0x0a) return ch;
  if (c === 0x09) return " ";
  if (c === 0xad || c < 0x20 || (c >= 0x7f && c < 0xa0)) return ""; // soft hyphen, control characters
  if (c < 0x7f || (c >= 0xa0 && c <= 0xff) || CP1252[c]) return ch;
  if (/\s/u.test(ch)) return " "; // narrow and thin spaces (numbers in French)
  if (c === 0x2010 || c === 0x2011 || c === 0x2212) return "-";
  const base = ch.normalize("NFD").replace(/[̀-ͯ]/g, "");
  return base && [...base].every((x) => { const k = x.codePointAt(0); return k >= 0x20 && k <= 0xff; }) ? base : "?";
}
const pdfText = (s) => [...String(s ?? "")].map(pdfChar).join("");
/* a literal string for the content stream (input already through pdfText) */
function pdfLit(s) {
  let out = "(";
  for (const ch of s) {
    const c = ch.codePointAt(0), b = CP1252[c] ?? c;
    if (ch === "(" || ch === ")" || ch === "\\") out += `\\${ch}`;
    else if (b > 0xff) out += "?";
    else if (b < 0x20 || b > 0x7e) out += `\\${b.toString(8).padStart(3, "0")}`;
    else out += ch;
  }
  return `${out})`;
}
/* the document title, in Unicode (UTF-16BE) for the reader's title bar */
const pdfHex = (s) => {
  const str = String(s ?? "");
  let hex = "";
  for (let i = 0; i < str.length; i++) hex += str.charCodeAt(i).toString(16).padStart(4, "0");
  return `<FEFF${hex.toUpperCase()}>`;
};

let measureCtx = null;
function textWidth(s, bold, size) {
  if (!measureCtx) {
    measureCtx = document.createElement("canvas").getContext("2d");
    if ("fontKerning" in measureCtx) measureCtx.fontKerning = "none"; // PDF text is not kerned
  }
  measureCtx.font = `${bold ? "bold " : ""}100px Arial, Helvetica, "Liberation Sans", Arimo, sans-serif`;
  return (measureCtx.measureText(s).width / 100) * size;
}
/* lines no wider than `width`; a word too long for a line is cut */
function wrapLines(text, bold, size, width) {
  const w = (s) => textWidth(s, bold, size);
  const lines = [];
  for (const para of text.split("\n")) {
    let line = "";
    for (const word of para.split(/ +/).filter(Boolean)) {
      const next = line ? `${line} ${word}` : word;
      if (w(next) <= width) { line = next; continue; }
      if (line) lines.push(line);
      line = word;
      while (line.length > 1 && w(line) > width) {
        let cut = line.length - 1;
        while (cut > 1 && w(line.slice(0, cut)) > width) cut--;
        lines.push(line.slice(0, cut));
        line = line.slice(cut);
      }
    }
    if (line || lines.at(-1) !== "") lines.push(line); // at most one empty line in a row
  }
  while (lines.at(-1) === "") lines.pop();
  return lines;
}

function docToPdf(doc) {
  const W = 595.28, H = 841.89, M = 56, CW = W - 2 * M, BOTTOM = M + 10; // A4, in points
  const COL = { ink: "0.090 0.125 0.114", slate: "0.333 0.388 0.369", spruce: "0.122 0.302 0.247", line: "0.843 0.875 0.859" };
  const pages = [];
  let page, y;
  const newPage = () => { page = { ops: [], annots: [] }; pages.push(page); y = H - M; };
  const room = (h) => { if (y - h < BOTTOM) newPage(); };
  const put = (x, base, s, { size, bold = false, col = "ink" }) => page.ops.push(
    `BT /${bold ? "F2" : "F1"} ${size} Tf ${COL[col]} rg ${x.toFixed(2)} ${base.toFixed(2)} Td ${pdfLit(s)} Tj ET`);
  /* a block of wrapped text; returns the lines it drew */
  const para = (text, { size = 10.5, lh = 1.4, bold = false, col = "ink", x = M, width = CW } = {}) => {
    const step = size * lh, lines = wrapLines(pdfText(text), bold, size, width);
    for (const s of lines) {
      if (!s) { y -= step * 0.55; continue; } // blank line between paragraphs
      room(step); y -= step;
      put(x, y + (step - size) / 2 + size * 0.2, s, { size, bold, col });
    }
    return lines;
  };
  const rule = (gap = 10) => {
    y -= gap / 2;
    page.ops.push(`${COL.line} RG 0.8 w ${M} ${y.toFixed(2)} m ${(W - M).toFixed(2)} ${y.toFixed(2)} l S`);
    y -= gap / 2;
  };

  newPage();
  para(doc.head, { size: 9.5, col: "slate" });
  y -= 2;
  para(doc.title, { size: 18, lh: 1.25, bold: true, col: "spruce" });
  if (doc.who) para(doc.who, { size: 11, col: "slate" });
  if (doc.url) {
    y -= 3;
    const size = 10, step = 14, s = pdfText(doc.urlLabel);
    room(step); y -= step;
    const base = y + (step - size) / 2 + size * 0.2, wid = textWidth(s, false, size);
    put(M, base, s, { size, col: "spruce" });
    page.ops.push(`${COL.spruce} RG 0.6 w ${M} ${(base - 1.5).toFixed(2)} m ${(M + wid).toFixed(2)} ${(base - 1.5).toFixed(2)} l S`);
    page.annots.push({ rect: [M, base - 3, M + wid, base + size], url: doc.url });
  }
  y -= 6; rule(12);

  // facts: label on the left, value on the right
  const LABEL_W = 150, size = 10, step = 14.5;
  for (const [k, v] of doc.facts) {
    const L = wrapLines(pdfText(k), true, size, LABEL_W - 10), V = wrapLines(pdfText(v), false, size, CW - LABEL_W);
    room(Math.max(L.length, V.length) * step);
    const top = y;
    L.forEach((s, i) => put(M, top - (i + 1) * step + 4, s, { size, bold: true, col: "slate" }));
    V.forEach((s, i) => put(M + LABEL_W, top - (i + 1) * step + 4, s, { size }));
    y = top - Math.max(L.length, V.length) * step;
  }
  if (doc.map) { // the map, across the text width, under the properties
    const h = (CW * doc.map.height) / doc.map.width;
    y -= 12; room(h); y -= h;
    page.ops.push(`q ${CW.toFixed(2)} 0 0 ${h.toFixed(2)} ${M} ${y.toFixed(2)} cm /Map Do Q`);
  }

  for (const [h, text] of doc.sections) {
    y -= 12;
    room(17 + 3 * 14.7); // a heading is never left alone at the bottom of a page
    para(h, { size: 12.5, lh: 1.3, bold: true, col: "spruce" });
    y -= 3;
    para(text);
  }
  y -= 14; rule(8);
  para(doc.footer, { size: 8.5, col: "slate" });

  // page numbers, then the file itself
  const foot = pdfText(`Better Zivi · ${doc.head}`);
  pages.forEach((p, i) => {
    const num = `${i + 1} / ${pages.length}`;
    p.ops.push(`BT /F1 8 Tf ${COL.slate} rg ${M} 30 Td ${pdfLit(foot)} Tj ET`);
    p.ops.push(`BT /F1 8 Tf ${COL.slate} rg ${(W - M - textWidth(num, false, 8)).toFixed(2)} 30 Td ${pdfLit(num)} Tj ET`);
  });
  const objs = [];
  const add = (s) => objs.push(s); // returns the object number
  const catalog = add(""), pagesObj = add("");
  const f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>");
  const f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>");
  let xobj = "";
  if (doc.map) { // the JPEG as it is (DCTDecode), written in hex so the file stays ASCII
    const bin = atob(doc.map.dataUrl.slice(doc.map.dataUrl.indexOf(",") + 1));
    const hex = new Array(bin.length);
    for (let i = 0; i < bin.length; i++) hex[i] = bin.charCodeAt(i).toString(16).padStart(2, "0");
    const data = `${hex.join("")}>`;
    const img = add(`<< /Type /XObject /Subtype /Image /Width ${doc.map.width} /Height ${doc.map.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter [/ASCIIHexDecode /DCTDecode] /Length ${data.length} >>\nstream\n${data}\nendstream`);
    xobj = ` /XObject << /Map ${img} 0 R >>`;
  }
  const kids = pages.map((p) => {
    const content = p.ops.join("\n");
    const contentObj = add(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
    const annots = p.annots.map((a) => add(`<< /Type /Annot /Subtype /Link /Rect [${a.rect.map((n) => n.toFixed(2)).join(" ")}] /Border [0 0 0] /A << /S /URI /URI ${pdfLit(pdfText(a.url))} >> >>`));
    return add(`<< /Type /Page /Parent ${pagesObj} 0 R /MediaBox [0 0 ${W} ${H}] /Resources << /Font << /F1 ${f1} 0 R /F2 ${f2} 0 R >>${xobj} >> /Contents ${contentObj} 0 R${
      annots.length ? ` /Annots [${annots.map((n) => `${n} 0 R`).join(" ")}]` : ""} >>`);
  });
  objs[catalog - 1] = `<< /Type /Catalog /Pages ${pagesObj} 0 R /Lang ${pdfLit(doc.lang)} >>`;
  objs[pagesObj - 1] = `<< /Type /Pages /Kids [${kids.map((n) => `${n} 0 R`).join(" ")}] /Count ${kids.length} >>`;
  const info = add(`<< /Title ${pdfHex(doc.title)} /Producer (Better Zivi) >>`);
  // everything above is ASCII, so string positions are byte offsets
  let out = "%PDF-1.4\n";
  const offsets = objs.map((o, i) => { const at = out.length; out += `${i + 1} 0 obj\n${o}\nendobj\n`; return at; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root ${catalog} 0 R /Info ${info} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return out;
}
