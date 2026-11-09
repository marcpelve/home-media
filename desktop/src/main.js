// home-media frontend - vanilla JS, no bundler.
// Talks to the local backend on 127.0.0.1:8790.

const API = "http://127.0.0.1:8790";
const app = document.getElementById("app");
const searchInput = document.getElementById("search");

let playPollTimer = null;

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function posterSrc(item) {
  if (item.poster) return API + item.poster; // local /tmdb/poster proxy
  if (item.poster_path) return `https://image.tmdb.org/t/p/w342${item.poster_path}`;
  return "";
}

function stopPlayPoll() {
  if (playPollTimer) { clearInterval(playPollTimer); playPollTimer = null; }
}

function makeCard(item, { withProgress = false } = {}) {
  const card = el("div", "card");
  const img = el("img", "poster");
  img.loading = "lazy";
  img.src = posterSrc(item);
  img.alt = item.title || item.name || "";
  card.appendChild(img);

  const meta = el("div", "meta");
  meta.appendChild(el("div", "title", item.title || item.name || ""));
  if (withProgress && item.duration_ms) {
    const progress = el("div", "progress");
    const bar = el("i");
    bar.style.width = Math.min(100, (item.position_ms / item.duration_ms) * 100) + "%";
    progress.appendChild(bar);
    meta.appendChild(progress);
  }
  card.appendChild(meta);
  card.addEventListener("click", () => openDetail(item.id));
  return card;
}

function makeRow(title, items, opts) {
  if (!items || !items.length) return null;
  const row = el("section", "row");
  row.appendChild(el("h2", null, title));
  const cards = el("div", "cards");
  for (const it of items) cards.appendChild(makeCard(it, opts));
  row.appendChild(cards);
  return row;
}

async function renderHome() {
  stopPlayPoll();
  app.innerHTML = "";
  const [watch, home] = await Promise.allSettled([
    api("/watch"),
    api("/tmdb/home"),
  ]);
  if (watch.status === "fulfilled") {
    const row = makeRow("Continue Watching", watch.value.items, { withProgress: true });
    if (row) app.appendChild(row);
  }
  if (home.status === "fulfilled") {
    const row = makeRow("Trending This Week", home.value.results || []);
    if (row) app.appendChild(row);
  }
  if (watch.status === "rejected" && home.status === "rejected") {
    app.appendChild(el("p", "muted", "Backend unreachable. Is it running on 127.0.0.1:8790?"));
  }
}

async function openDetail(id) {
  try {
    const d = await api(`/tmdb/title/${id}`);
    renderDetail(d);
  } catch (e) {
    app.appendChild(el("p", "muted", "Could not load details: " + e.message));
  }
}

function renderDetail(d) {
  stopPlayPoll();
  app.innerHTML = "";
  const wrap = el("div", "detail");
  const img = el("img", "poster");
  img.src = d.poster_path ? `https://image.tmdb.org/t/p/w500${d.poster_path}` : "";
  wrap.appendChild(img);

  const info = el("div", "info");
  info.appendChild(el("h1", null, d.title || d.name || "Unknown"));
  const year = (d.release_date || d.first_air_date || "").slice(0, 4);
  const genres = (d.genres || []).map((g) => g.name).join(" \u00b7 ");
  info.appendChild(el("div", "meta", [year, genres, `\u2605 ${d.vote_average ?? "\u2014"}`].filter(Boolean).join("   ")));
  info.appendChild(el("div", "overview", d.overview || ""));

  let seasonSel = null, episodeSel = null;
  if (d._type === "tv" && Array.isArray(d.seasons) && d.seasons.length) {
    const picker = el("div", "ep-picker");
    seasonSel = el("select");
    for (const s of d.seasons) {
      if (s.season_number === 0) continue;
      const opt = document.createElement("option");
      opt.value = s.season_number;
      opt.textContent = `Season ${s.season_number}`;
      seasonSel.appendChild(opt);
    }
    episodeSel = el("select");
    const fillEps = (count) => {
      episodeSel.innerHTML = "";
      for (let i = 1; i <= count; i++) {
        const opt = document.createElement("option");
        opt.value = i;
        opt.textContent = `Episode ${i}`;
        episodeSel.appendChild(opt);
      }
    };
    const firstSeason = d.seasons.find((s) => s.season_number !== 0);
    if (firstSeason) fillEps(firstSeason.episode_count || 1);
    seasonSel.addEventListener("change", () => {
      const s = d.seasons.find((x) => x.season_number === Number(seasonSel.value));
      fillEps(s ? s.episode_count || 1 : 1);
    });
    picker.appendChild(seasonSel);
    picker.appendChild(episodeSel);
    info.appendChild(picker);
  }

  const btn = el("button", "btn", "Play");
  btn.addEventListener("click", () => startPlayback(d, btn, seasonSel, episodeSel));
  info.appendChild(btn);

  wrap.appendChild(info);
  app.appendChild(wrap);
  window.scrollTo(0, 0);
}

// ---- source picker ----
function openSourcePicker(d, sources, btn, seasonSel, episodeSel) {
  const overlay = el("div", "overlay");
  const panel = el("div", "picker");
  panel.appendChild(el("h2", null, `Play ${d.title || d.name}`));
  const sub = el("div", "muted");
  sub.textContent = seasonSel && episodeSel
    ? `Season ${seasonSel.value}, Episode ${episodeSel.value}  -  ${sources.length} sources`
    : `${sources.length} sources`;
  panel.appendChild(sub);

  const list = el("div", "src-list");
  for (const s of sources) {
    const row = el("div", "src-row");
    row.appendChild(el("span", "badge " + (s.quality === "4K" ? "q4k" : "qhd"), s.quality || "?"));
    const meta = el("span", "src-meta");
    meta.textContent = [
      s.size != null ? `${s.size} GB` : "",
      s.seeders != null ? `${s.seeders} seeders` : "",
      s.provider || "",
    ].filter(Boolean).join("  \u00b7  ");
    row.appendChild(meta);
    row.addEventListener("click", () => pickSource(d, s, seasonSel, episodeSel));
    list.appendChild(row);
  }
  panel.appendChild(list);

  const close = el("button", "btn btn-ghost", "Cancel");
  close.addEventListener("click", () => dismiss());
  panel.appendChild(close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) dismiss(); });

  function dismiss() {
    overlay.remove();
    if (btn) { btn.disabled = false; btn.textContent = "Play"; }
  }
  if (btn) btn.disabled = true;
  overlay.appendChild(panel);
  app.appendChild(overlay);
}

async function pickSource(d, source, seasonSel, episodeSel) {
  try {
    const resolved = await api("/sources/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hash: source.hash }),
    });
    const season = seasonSel ? Number(seasonSel.value) : null;
    const episode = episodeSel ? Number(episodeSel.value) : null;
    const label = (source.title || d.title || d.name) + (episode ? ` S${season}E${episode}` : "");
    await api("/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: resolved.download_url, file_path: label }),
    });
    renderNowPlaying(d, source, season, episode);
  } catch (e) {
    alert("Playback failed: " + e.message);
  }
}

async function startPlayback(d, btn, seasonSel, episodeSel) {
  btn.disabled = true;
  btn.textContent = "Finding sources...";
  try {
    const body = d._type === "tv"
      ? { season: seasonSel ? Number(seasonSel.value) : 1, episode: episodeSel ? Number(episodeSel.value) : 1 }
      : {};
    const srcs = await api(`/sources/${d.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!srcs.sources || !srcs.sources.length) {
      alert("No sources found for this title.");
      btn.textContent = "Play";
      btn.disabled = false;
      return;
    }
    openSourcePicker(d, srcs.sources, btn, seasonSel, episodeSel);
  } catch (e) {
    alert("Source lookup failed: " + e.message);
    btn.textContent = "Play";
    btn.disabled = false;
  }
}

// ---- now playing ----
function fmtTime(sec) {
  if (sec == null) return "-:--";
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
}

function renderNowPlaying(d, source, season, episode) {
  stopPlayPoll();
  app.innerHTML = "";
  const wrap = el("div", "nowplaying");
  wrap.appendChild(el("div", "np-title", d.title || d.name || "Now playing"));
  if (season != null) wrap.appendChild(el("div", "muted", `Season ${season}, Episode ${episode}`));
  wrap.appendChild(el("div", "muted", [source.quality || "", source.size != null ? source.size + " GB" : "", source.provider || ""].filter(Boolean).join(" \u00b7 ")));

  const bar = el("div", "np-bar");
  const fill = el("i");
  bar.appendChild(fill);
  wrap.appendChild(bar);

  const times = el("div", "np-times");
  const cur = el("span", null, "0:00");
  const dur = el("span", null, "0:00");
  times.appendChild(cur);
  times.appendChild(dur);
  wrap.appendChild(times);

  const controls = el("div", "np-controls");
  const pauseBtn = el("button", "btn", "Pause");
  const resumeBtn = el("button", "btn", "Resume");
  const backBtn = el("button", "btn btn-ghost", "-10s");
  const fwdBtn = el("button", "btn btn-ghost", "+10s");
  const quitBtn = el("button", "btn btn-danger", "Stop");
  for (const b of [pauseBtn, resumeBtn, backBtn, fwdBtn, quitBtn]) controls.appendChild(b);
  wrap.appendChild(controls);
  app.appendChild(wrap);

  pauseBtn.addEventListener("click", () => api("/play/pause", { method: "POST" }));
  resumeBtn.addEventListener("click", () => api("/play/resume", { method: "POST" }));
  backBtn.addEventListener("click", async () => {
    const p = await api("/play/position");
    await api("/play/seek", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seconds: Math.max(0, (p.time_pos || 0) - 10) }) });
  });
  fwdBtn.addEventListener("click", async () => {
    const p = await api("/play/position");
    await api("/play/seek", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seconds: (p.time_pos || 0) + 10 }) });
  });
  quitBtn.addEventListener("click", async () => {
    stopPlayPoll();
    try {
      await api("/play/progress", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title_id: d.id, season, episode }) });
    } catch (e) { /* best-effort save */ }
    await api("/play/quit", { method: "POST" });
    renderDetail(d);
  });

  playPollTimer = setInterval(async () => {
    try {
      const p = await api("/play/position");
      if (!p.playing) return;
      cur.textContent = fmtTime(p.time_pos);
      dur.textContent = fmtTime(p.duration);
      if (p.duration) fill.style.width = Math.min(100, (p.time_pos / p.duration) * 100) + "%";
    } catch (e) { /* ignore transient poll errors */ }
  }, 2000);
}

async function renderSearch(q) {
  stopPlayPoll();
  const res = await api(`/tmdb/search?q=${encodeURIComponent(q)}`);
  app.innerHTML = "";
  const rows = [];
  if (res.movie && res.movie.length) rows.push(makeRow("Movies", res.movie));
  if (res.tv && res.tv.length) rows.push(makeRow("TV Shows", res.tv));
  for (const r of rows) app.appendChild(r);
  if (!rows.length) app.appendChild(el("p", "muted", `No results for "${q}".`));
}

let searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (!q) { renderHome(); return; }
  searchTimer = setTimeout(() => renderSearch(q), 300);
});

// ---- keyboard / focus navigation (10-foot / TV UI) ----
// One item is focused at a time. Arrows move within/between rows, Enter
// activates, Esc goes back, "/" focuses search. A visible .focused ring
// marks the current item.
let focusRows = [];
let focusRow = 0;
let focusCol = 0;

function focusables() {
  const overlay = document.querySelector(".overlay");
  if (overlay) return [...overlay.querySelectorAll(".src-row, .btn")];
  return [...document.querySelectorAll(".card, .btn, .src-row, select")];
}

function buildFocusMap() {
  const els = focusables();
  const byRow = new Map();
  for (const e of els) {
    const rowEl = e.closest(".cards, .info, .np-controls, .ep-picker") || e;
    if (!byRow.has(rowEl)) byRow.set(rowEl, []);
    byRow.get(rowEl).push(e);
  }
  focusRows = [...byRow.values()];
  focusRow = 0;
  focusCol = 0;
  setFocus(0, 0);
}

function setFocus(row, col) {
  if (!focusRows.length) return null;
  focusRow = Math.max(0, Math.min(row, focusRows.length - 1));
  const r = focusRows[focusRow] || [];
  focusCol = Math.max(0, Math.min(col, r.length - 1));
  document.querySelectorAll(".focused").forEach((n) => n.classList.remove("focused"));
  const el = r[focusCol];
  if (el) { el.classList.add("focused"); el.scrollIntoView({ block: "nearest", inline: "nearest" }); }
  return el;
}

function currentFocus() { return (focusRows[focusRow] || [])[focusCol] || null; }

function activate() { const el = currentFocus(); if (el) el.click(); }

function goBack() {
  const overlay = document.querySelector(".overlay");
  if (overlay) {
    overlay.remove();
    const btn = document.querySelector(".detail .btn");
    if (btn) { btn.disabled = false; btn.textContent = "Play"; }
    buildFocusMap();
    return;
  }
  renderHome();
}

function stepSelect(el, delta) {
  const idx = el.selectedIndex + delta;
  if (idx < 0 || idx >= el.options.length) return;
  el.selectedIndex = idx;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

document.addEventListener("keydown", (e) => {
  const search = document.getElementById("search");
  const inSearch = document.activeElement === search;
  if (inSearch && /^[a-z0-9 ]$/i.test(e.key)) return; // typing: pass through
  if (inSearch) search.blur();
  if (e.key === "/") { e.preventDefault(); search.focus(); return; }

  const cur = currentFocus();
  if (cur && cur.tagName === "SELECT") {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); stepSelect(cur, 1); return; }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); stepSelect(cur, -1); return; }
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); return; }
  }

  switch (e.key) {
    case "ArrowLeft":  e.preventDefault(); setFocus(focusRow, focusCol - 1); break;
    case "ArrowRight": e.preventDefault(); setFocus(focusRow, focusCol + 1); break;
    case "ArrowUp":    e.preventDefault(); setFocus(focusRow - 1, focusCol); break;
    case "ArrowDown":  e.preventDefault(); setFocus(focusRow + 1, focusCol); break;
    case "Enter":
    case " ":          e.preventDefault(); activate(); break;
    case "Escape":     e.preventDefault(); goBack(); break;
  }
});

// Rebuild the focus map whenever the app DOM changes (after each render).
new MutationObserver(() => buildFocusMap()).observe(app, { childList: true, subtree: true });

renderHome();
