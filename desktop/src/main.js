// home-media frontend - vanilla JS, no bundler.
// Talks to the local backend on 127.0.0.1:8790.

const API = "http://127.0.0.1:8790";
const app = document.getElementById("app");
const searchInput = document.getElementById("search");

let playPollTimer = null;
let nowPlaying = null; // { id, season, episode } of the current playback

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

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function posterSrc(item) {
  if (item.poster) return API + item.poster; // local poster (continue watching)
  if (item.poster_path) {
    // type (movie/tv) disambiguates TMDB's shared numeric ids; without it the
    // proxy can resolve a poster-less movie for a tv id (and vice versa).
    const t = item.type ? "?type=" + item.type : "";
    return API + "/tmdb/poster/" + item.id + t; // proxy, disk-cached (offline)
  }
  return "";
}

// Lazy poster loading: a full home page has ~90 cards, and setting every
// img.src at once fires ~90 concurrent downloads (heavy on the CDN and slow).
// Native loading="lazy" only defers images below the vertical fold, not cards
// off to the side in a horizontally-scrolled row. An IntersectionObserver with
// a rootMargin loads a poster only when its card is (near) visible in the
// viewport, in both axes. The observer is created once and reused.
const POSTER_PLACEHOLDER = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
let posterObserver = null;
function observePoster(img) {
  if (!posterObserver) {
    posterObserver = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            const el = e.target;
            el.src = el.dataset.posterSrc;
            posterObserver.unobserve(el);
          }
        }
      },
      { rootMargin: "200px 0px 200px 0px" }
    );
  }
  posterObserver.observe(img);
}

function stopPlayPoll() {
  if (playPollTimer) { clearInterval(playPollTimer); playPollTimer = null; }
}

// ---- background source prefetch ----
// Fire the source scrape as soon as the user settles on a detail card
// (debounced), so Play can show a populated list - or a near-instant one if
// it's still in flight. One scrape runs at a time; leaving the card or
// opening another aborts the current one. Resolved payloads live in a small
// in-memory cache (never persisted: sources go stale quickly).
const sourceInFlight = new Map(); // key -> { promise, controller }
const sourceCache = new Map();    // key -> { sources, count }
let sourcePrefetchTimer = null;

function srcKey(d, season, episode) {
  return d._type === "tv"
    ? "src:" + d.id + ":" + season + ":" + episode
    : "src:" + d.id;
}

function rememberSources(key, payload) {
  if (sourceCache.size >= 24) sourceCache.delete(sourceCache.keys().next().value);
  sourceCache.set(key, payload);
}

function prefetchSources(d, season, episode) {
  const key = srcKey(d, season, episode);
  if (sourceCache.has(key) || sourceInFlight.has(key)) return;
  const controller = new AbortController();
  const body = d._type === "tv" ? { season, episode } : {};
  const p = api("/sources/" + d.id, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  });
  sourceInFlight.set(key, { promise: p, controller });
  p.then((payload) => {
    sourceInFlight.delete(key);
    rememberSources(key, payload);
  }).catch(() => {
    sourceInFlight.delete(key);
  });
}

// Abort any in-flight prefetch whose key is not keepKey (single-flight).
function abortStalePrefetch(keepKey) {
  for (const [key, entry] of sourceInFlight) {
    if (key !== keepKey) { entry.controller.abort(); sourceInFlight.delete(key); }
  }
}

// Debounced prefetch for the card's current selection. Called on open and on
// any season/episode change.
function schedulePrefetch(d, seasonSel, episodeSel) {
  const season = seasonSel ? Number(seasonSel.value) : null;
  const episode = episodeSel ? Number(episodeSel.value) : null;
  abortStalePrefetch(srcKey(d, season, episode));
  if (sourcePrefetchTimer) clearTimeout(sourcePrefetchTimer);
  sourcePrefetchTimer = setTimeout(() => prefetchSources(d, season, episode), 400);
}

// ---- navigation / history (lightweight SPA back-stack) ----
// Back returns to the previous screen (and where you were in it), not always
// home. The stack is the browser's native pushState/popstate (cheap, and it
// wires the browser/remote back button for free). A small bounded in-memory
// data cache makes back instant - no re-fetch, no rebuild from scratch - so
// it stays light on low-end hardware.
const dataCache = new Map();
function cacheGet(key) { return dataCache.get(key); }
function cacheSet(key, val) {
  if (dataCache.size >= 40) dataCache.delete(dataCache.keys().next().value);
  dataCache.set(key, val);
}

function renderView(view, params) {
  if (sourcePrefetchTimer) { clearTimeout(sourcePrefetchTimer); sourcePrefetchTimer = null; }
  if (view !== "detail") abortStalePrefetch(null);
  if (view === "search") return renderSearch(params.query, params.category || "all");
  if (view === "detail") return openDetail(params.id, params.type);
  if (view === "settings") return renderSettings();
  if (view === "library") return renderLibrary();
  if (view === "librarytitle") return openLibraryTitle(params.id);
  if (view === "nowplaying") {
    renderNowPlaying(cacheGet("detail:" + params.id) || {}, params.source, params.season, params.episode);
    return;
  }
  return renderHome(params && params.category);
}

function navPush(view, params) {
  history.pushState({ view, params, scroll: window.scrollY }, "", "");
  renderView(view, params);
  updateBackButton();
}

function navBack() {
  if (history.state && history.state.view && history.state.view !== "home") history.back();
  else navHome();
}

function navHome() {
  if (!history.state || history.state.view !== "home") {
    history.pushState({ view: "home", params: {}, scroll: 0 }, "", "");
    renderHome();
  } else {
    window.scrollTo(0, 0);
  }
  updateBackButton();
}

window.addEventListener("popstate", (e) => {
  const s = e.state;
  if (!s || s.view === "home") renderHome();
  else renderView(s.view, s.params);
  requestAnimationFrame(() => window.scrollTo(0, (s && s.scroll) || 0));
  updateBackButton();
});

function updateBackButton() {
  const b = document.getElementById("back");
  if (b) b.classList.toggle("hidden", !(history.state && history.state.view && history.state.view !== "home"));
}

document.getElementById("back").addEventListener("click", navBack);
document.getElementById("home").addEventListener("click", navHome);
document.getElementById("library").addEventListener("click", () => navPush("library", {}));
document.getElementById("settings").addEventListener("click", () => navPush("settings", {}));
document.getElementById("power").addEventListener("click", exitAndSync);

// ---- sync & quit ----
// The power button fires a best-effort Trakt sync in the background (like
// the source prefetch), then asks before quitting. Cancel: stay (the sync
// finishes quietly). Confirm: stop playback and close the window.
function exitAndSync() {
  const btn = document.getElementById("power");
  if (btn) { btn.classList.add("syncing"); btn.title = "Syncing..."; }
  api("/trakt/sync", { method: "POST" }).catch(() => {});
  if (!confirm("Quit Home Media?")) return;
  if (nowPlaying) {
    api("/play/progress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(nowPlaying),
    }).catch(() => {});
  }
  stopPlayPoll();
  nowPlaying = null;
  api("/play/quit", { method: "POST" }).catch(() => {});
  setTimeout(() => {
    if (window.__TAURI__) window.__TAURI__.window.getCurrent().close();
    else window.close();
  }, 300);
}

function makeCard(item, { withProgress = false } = {}) {
  const card = el("div", "card");
  const img = el("img", "poster");
  img.loading = "lazy";
  img.alt = item.title || item.name || "";
  const src = posterSrc(item);
  if (src) {
    img.src = POSTER_PLACEHOLDER; // fill in when (near) visible
    img.dataset.posterSrc = src;
    observePoster(img);
  }
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
  card.addEventListener("click", () => navPush("detail", { id: item.id, type: item.type }));
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

function normTmdb(it, type) {
  return { id: it.id, title: it.title || it.name, type, poster_path: it.poster_path };
}

function normRec(it, type) {
  return {
    id: it.id,
    title: it.title || "",
    type,
    poster_path: it.poster_path,
  };
}

function filterByCat(items, cat) {
  return cat === "all" ? items : items.filter((it) => it.type === cat);
}

function tmdbItems(movie, tv) {
  return [
    ...(movie || []).map((it) => normTmdb(it, "movie")),
    ...(tv || []).map((it) => normTmdb(it, "tv")),
  ].filter((it) => it.poster_path);
}

function buildHomeRows(watch, popular, trending, latest, recs) {
  const rows = [];
  if (watch.status === "fulfilled") {
    const items = (watch.value.items || []).map((it) => ({
      id: it.title_id,
      title: it.title,
      type: it.type,
      poster: it.poster,
      position_ms: it.position_ms,
      duration_ms: it.duration_ms,
      season: it.season,
      episode: it.episode,
    }));
    rows.push(["Continue Watching", items, { withProgress: true }]);
  }
  if (popular.status === "fulfilled") {
    rows.push(["Popular", tmdbItems(popular.value.movie, popular.value.tv)]);
  }
  if (trending.status === "fulfilled") {
    rows.push(["Trending This Week", (trending.value.results || []).map((it) => normTmdb(it, it.media_type)).filter((it) => it.poster_path)]);
  }
  if (latest.status === "fulfilled") {
    rows.push(["Latest Releases", tmdbItems(latest.value.movie, latest.value.tv)]);
  }
  if (recs.status === "fulfilled") {
    rows.push(["Because You Watched", [
      ...(recs.value.movie || []).map((it) => normRec(it, "movie")),
      ...(recs.value.show || []).map((it) => normRec(it, "tv")),
    ].filter((it) => it.poster_path)]);
  }
  return rows;
}

function renderHomeRows(rows, category, allRejected) {
  app.innerHTML = "";
  const tabs = el("div", "cat-tabs");
  for (const [val, label] of [["all", "All"], ["movie", "Movies"], ["tv", "TV"]]) {
    const t = el("button", "cat-tab" + (val === category ? " active" : ""), label);
    t.addEventListener("click", () => {
      history.replaceState({ view: "home", params: { category: val }, scroll: 0 }, "", "");
      renderHomeRows(rows, val, allRejected);
    });
    tabs.appendChild(t);
  }
  app.appendChild(tabs);

  let rendered = 0;
  for (const [title, items, opts] of rows) {
    const row = makeRow(title, filterByCat(items, category), opts);
    if (row) { app.appendChild(row); rendered++; }
  }
  if (!rendered) {
    const box = el("div", "boot");
    if (allRejected) {
      box.appendChild(el("p", "muted", "Backend unreachable."));
      const btn = el("button", "btn", "Retry");
      btn.addEventListener("click", () => renderHome(category));
      box.appendChild(btn);
    } else {
      box.appendChild(el("p", "muted", "Nothing to show here yet."));
    }
    app.appendChild(box);
  }
  window.scrollTo(0, 0);
}

async function renderHome(category) {
  stopPlayPoll();
  category = category || "all";
  app.innerHTML = "";
  const [watch, popular, trending, latest, recs] = await Promise.allSettled([
    api("/watch"),
    api("/tmdb/popular"),
    api("/tmdb/home"),
    api("/tmdb/latest"),
    api("/trakt/recommendations"),
  ]);
  const rows = buildHomeRows(watch, popular, trending, latest, recs);
  const allRejected = [watch, popular, trending, latest, recs].every((r) => r.status === "rejected");
  renderHomeRows(rows, category, allRejected);
}

// ---- offline library (works with no internet) ----
async function renderLibrary() {
  stopPlayPoll();
  app.innerHTML = "";
  let data;
  try {
    data = await api("/library");
  } catch (e) {
    app.appendChild(el("p", "muted", "Could not load library: " + e.message));
    return;
  }
  const titles = data.titles || [];
  if (!titles.length) {
    const box = el("div", "boot");
    box.appendChild(el("p", "muted", "No local files yet. Download content to build your library."));
    const btn = el("button", "btn", "Browse");
    btn.addEventListener("click", () => navPush("search", { query: "" }));
    box.appendChild(btn);
    app.appendChild(box);
    return;
  }
  const grid = el("div", "grid");
  for (const t of titles) grid.appendChild(makeLibraryCard(t));
  const row = el("section", "row");
  row.appendChild(el("h2", null, `My Library (${titles.length})`));
  row.appendChild(grid);
  app.appendChild(row);
  window.scrollTo(0, 0);
}

function makeLibraryCard(t) {
  const card = el("div", "card");
  const img = el("img", "poster");
  img.loading = "lazy";
  img.src = posterSrc(t);
  img.alt = t.title || "";
  card.appendChild(img);
  const meta = el("div", "meta");
  meta.appendChild(el("div", "title", t.title || ""));
  const sub = [t.type, t.file_count ? t.file_count + (t.type === "tv" ? " eps" : " file") : ""].filter(Boolean).join(" \u00b7 ");
  if (sub) meta.appendChild(el("div", "muted", sub));
  card.appendChild(meta);
  card.addEventListener("click", () => navPush("librarytitle", { id: t.tmdb_id }));
  return card;
}

async function openLibraryTitle(id) {
  stopPlayPoll();
  app.innerHTML = "";
  let data;
  try {
    data = await api("/library");
  } catch (e) {
    app.appendChild(el("p", "muted", "Could not load library: " + e.message));
    return;
  }
  const t = (data.titles || []).find((x) => x.tmdb_id === id);
  if (!t) {
    app.appendChild(el("p", "muted", "Title not found."));
    return;
  }
  const wrap = el("div", "detail");
  const img = el("img", "poster");
  img.src = posterSrc(t);
  img.alt = t.title || "";
  wrap.appendChild(img);

  const info = el("div", "info");
  info.appendChild(el("h1", null, t.title || ""));
  info.appendChild(el("div", "meta", [t.type, t.year].filter(Boolean).join(" \u00b7 ")));
  if (t.overview) info.appendChild(el("div", "overview", t.overview));

  const eps = t.episodes || [];
  if (!eps.length) {
    info.appendChild(el("div", "muted", "No local files for this title."));
  } else {
    const list = el("div", "ep-list");
    for (const ep of eps) {
      const rowEl = el("div", "ep-row");
      rowEl.appendChild(el("span", "ep-label", t.type === "tv" ? `Episode ${ep.episode}` : "Movie"));
      rowEl.appendChild(el("span", "ep-status", ep.completed ? "Completed" : (ep.position_ms ? "In progress" : "Not started")));
      const playBtn = el("button", "btn", "Play");
      playBtn.addEventListener("click", () => playLocal(t, ep));
      rowEl.appendChild(playBtn);
      list.appendChild(rowEl);
    }
    info.appendChild(list);
  }
  wrap.appendChild(info);
  app.appendChild(wrap);
  window.scrollTo(0, 0);
}

async function playLocal(t, ep) {
  try {
    const season = t.type === "tv" && ep.episode != null ? 1 : null;
    const episode = ep.episode != null ? ep.episode : null;
    const start = await savedStartSeconds(t.tmdb_id, season, episode);
    await api("/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: ep.file_path, start_seconds: start }),
    });
    cacheSet("detail:" + t.tmdb_id, { ...t, id: t.tmdb_id });
    navPush("nowplaying", { id: t.tmdb_id, source: { quality: "Local" }, season, episode });
  } catch (e) {
    alert("Playback failed: " + e.message);
  }
}

async function openDetail(id, type) {
  try {
    const key = "detail:" + id;
    let d = cacheGet(key);
    if (!d) {
      const qs = (type === "movie" || type === "tv") ? "?type=" + type : "";
      d = await api(`/tmdb/title/${id}${qs}`);
      cacheSet(key, d);
    }
    let currentEp = null;
    if (d._type === "tv") {
      // Cheap localhost read of local watch progress; defaults the picker and
      // the background prefetch to the episode the user is on.
      const wp = await api("/watch/progress/" + id).catch(() => null);
      if (wp && wp.episode != null) currentEp = { season: wp.season, episode: wp.episode };
    }
    renderDetail(d, currentEp);
  } catch (e) {
    app.appendChild(el("p", "muted", "Could not load details: " + e.message));
  }
}

function renderDetail(d, currentEp) {
  stopPlayPoll();
  app.innerHTML = "";
  const wrap = el("div", "detail");
  const top = el("div", "detail-top");
  const img = el("img", "poster");
  img.src = d.poster_path ? `https://image.tmdb.org/t/p/w500${d.poster_path}` : "";
  top.appendChild(img);
  wrap.appendChild(top);

  const info = el("div", "info");
  info.appendChild(el("h1", null, d.title || d.name || "Unknown"));
  const year = (d.release_date || d.first_air_date || "").slice(0, 4);
  const genres = (d.genres || []).map((g) => g.name).join(" \u00b7 ");
  info.appendChild(el("div", "meta", [year, genres, `\u2605 ${d.vote_average ?? "\u2014"}`].filter(Boolean).join("   ")));
  info.appendChild(el("div", "overview", d.overview || ""));

  let seasonSel = null, episodeSel = null, selectedEp = 1;
  if (d._type === "tv" && Array.isArray(d.seasons) && d.seasons.length) {
    // Proxy so the downstream play/prefetch/mark-watched code that reads
    // episodeSel.value keeps working; the episode grid is the real selector.
    episodeSel = { get value() { return String(selectedEp); } };
    const picker = el("div", "ep-picker");
    seasonSel = el("select");
    for (const s of d.seasons) {
      if (s.season_number === 0) continue;
      const opt = document.createElement("option");
      opt.value = s.season_number;
      opt.textContent = `Season ${s.season_number}`;
      seasonSel.appendChild(opt);
    }
    picker.appendChild(seasonSel);
    wrap.appendChild(picker);
    const grid = el("div", "ep-grid");
    wrap.appendChild(grid);

    const setEp = (n) => {
      selectedEp = n;
      grid.querySelectorAll(".ep-card").forEach((c) =>
        c.classList.toggle("selected", Number(c.dataset.ep) === n));
    };

    // Fetch a season's episodes and render them as cards (still, title,
    // synopsis). Stills are proxied + disk-cached like posters (offline-safe).
    const renderEpisodes = async (season) => {
      grid.innerHTML = "";
      grid.appendChild(el("div", "ep-loading", "Loading episodes..."));
      let eps = [];
      try {
        const data = await api(`/tmdb/season/${d.id}/${season}`);
        eps = (data.episodes || []).slice()
          .sort((a, b) => a.episode_number - b.episode_number);
      } catch (e) {
        grid.innerHTML = "";
        grid.appendChild(el("div", "muted", "Could not load episodes."));
        return;
      }
      grid.innerHTML = "";
      if (!eps.length) { grid.appendChild(el("div", "muted", "No episodes found.")); return; }
      for (const ep of eps) {
        const card = el("div", "ep-card");
        card.dataset.ep = ep.episode_number;
        const key = d.id + "-s" + season + "e" + ep.episode_number;
        const img = el("img", "ep-still");
        if (ep.still_path) {
          img.src = API + "/tmdb/image?path=" + encodeURIComponent(ep.still_path)
            + "&key=" + encodeURIComponent(key) + "&size=w300";
        } else {
          card.classList.add("no-still");
        }
        card.appendChild(img);
        const body = el("div", "ep-body");
        body.appendChild(el("div", "ep-name", `E${ep.episode_number} \u00b7 ${ep.name || "Untitled"}`));
        if (ep.overview) body.appendChild(el("div", "ep-overview", ep.overview));
        card.appendChild(body);
        card.addEventListener("click", () => { setEp(ep.episode_number); schedulePrefetch(d, seasonSel, episodeSel); });
        grid.appendChild(card);
      }
      // Default to the user's current episode if it's in this season, else E1.
      const cur = currentEp && currentEp.season === season ? currentEp.episode : null;
      setEp(cur && cur >= 1 ? cur : 1);
    };

    seasonSel.addEventListener("change", () => renderEpisodes(Number(seasonSel.value)));
    // Default season: the user's current episode's season if listed, else first.
    let defSeason = d.seasons.find((s) => s.season_number !== 0).season_number;
    if (currentEp && d.seasons.some((x) => x.season_number === currentEp.season)) defSeason = currentEp.season;
    seasonSel.value = String(defSeason);
    renderEpisodes(defSeason);
  }

  const btn = el("button", "btn", "Play");
  btn.addEventListener("click", () => startPlayback(d, btn, seasonSel, episodeSel));
  info.appendChild(btn);

  // Mark-as-watched controls (manual). A movie marks the title; a show marks
  // the selected episode, or the whole season.
  const actionRow = el("div", "btn-row");
  if (d._type === "tv") {
    const epBtn = el("button", "btn btn-ghost", "Mark episode watched");
    epBtn.addEventListener("click", () => markComplete(
      d, Number(seasonSel.value), Number(episodeSel.value), false, epBtn));
    const seasonBtn = el("button", "btn btn-ghost", "Mark season watched");
    const cnt = d.seasons.find((s) => s.season_number === Number(seasonSel.value));
    seasonBtn.addEventListener("click", () => markComplete(
      d, Number(seasonSel.value), Number(episodeSel.value), true, seasonBtn,
      cnt ? cnt.episode_count || 1 : 1));
    actionRow.appendChild(epBtn);
    actionRow.appendChild(seasonBtn);
  } else {
    const mBtn = el("button", "btn btn-ghost", "Mark as watched");
    mBtn.addEventListener("click", () => markComplete(d, null, null, false, mBtn));
    actionRow.appendChild(mBtn);
  }
  info.appendChild(actionRow);

  top.appendChild(info);
  app.appendChild(wrap);
  window.scrollTo(0, 0);
  // Kick off the background source prefetch once the user has settled on the
  // card (debounced), so Play can show a populated or near-instant list.
  schedulePrefetch(d, seasonSel, episodeSel);
}

// POST /watch/complete for the current selection. wholeSeason marks every
// episode in the season (1..episodeCount).
async function markComplete(d, season, episode, wholeSeason, btn, episodeCount) {
  const body = { title_id: d.id };
  if (season != null) body.season = season;
  if (episode != null) body.episode = episode;
  if (wholeSeason) { body.whole_season = true; body.episode_count = episodeCount; }
  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Saving...";
  try {
    await api("/watch/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    btn.textContent = "Marked \u2713";
  } catch (e) {
    btn.textContent = prev;
    alert("Could not mark as watched: " + e.message);
  }
}

// ---- source picker ----
// Map a capability tag to a CSS class slug for color coding (e.g. "Dolby
// Vision" -> "cap-dolby-vision", "H.264" -> "cap-h-264").
function tagSlug(t) {
  return "cap-" + t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

// Open the source modal immediately (with a loading state) so the user sees
// it working; the list is populated by fillSourceList once sources return.
function openSourcePickerLoading(d, btn, seasonSel, episodeSel) {
  const overlay = el("div", "overlay");
  const panel = el("div", "picker");
  panel.appendChild(el("h2", null, `Play ${d.title || d.name}`));
  const sub = el("div", "muted");
  sub.textContent = "Finding sources via Torbox...";
  panel.appendChild(sub);

  // Make it explicit that downloads resolve through the Torbox debrid, not a
  // direct torrent (the user's ISP should never see a raw peer connection).
  const via = el("div", "via");
  via.appendChild(el("span", "badge debrid", "Torbox debrid"));
  via.appendChild(el("span", "via-note", "- downloads resolve via debrid, no direct torrent"));
  panel.appendChild(via);

  const list = el("div", "src-list picker-loading");
  list.appendChild(el("div", "spinner"));
  panel.appendChild(list);

  const close = el("button", "btn btn-ghost", "Cancel");
  close.addEventListener("click", () => dismiss());
  panel.appendChild(close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) dismiss(); });

  function dismiss() {
    overlay.remove();
    if (btn) { btn.disabled = false; btn.textContent = "Play"; }
  }
  overlay.appendChild(panel);
  app.appendChild(overlay);
  return { overlay, panel, sub, list, dismiss };
}

function fillSourceList(handles, d, sources, seasonSel, episodeSel) {
  const { sub, list } = handles;
  sub.textContent = seasonSel && episodeSel
    ? `Season ${seasonSel.value}, Episode ${episodeSel.value}  -  ${sources.length} sources`
    : `${sources.length} sources`;
  list.classList.remove("picker-loading");
  list.innerHTML = "";
  for (const s of sources) {
    const row = el("div", "src-row");
    const top = el("div", "src-row-top");
    // Tags live in a flex:1 wrapper so they take all the width, pinning the
    // Cached badge (a sibling) to the right edge of the row.
    const tags = el("div", "src-tags");
    tags.appendChild(el("span", "badge " + (s.quality === "4K" ? "q4k" : "qhd"), s.quality || "?"));
    for (const t of (s.tags || [])) tags.appendChild(el("span", "badge cap " + tagSlug(t), t));
    top.appendChild(tags);
    if (s.cached) top.appendChild(el("span", "badge cached", "Cached"));
    row.appendChild(top);
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
}

function showPickerMessage(handles, msg) {
  const { list } = handles;
  list.classList.remove("picker-loading");
  list.innerHTML = "";
  list.appendChild(el("div", "muted picker-empty", msg));
}

// Seconds to resume a title/episode at, from local watch progress. A saved
// position under 30s (or a completed item) starts from the top.
async function savedStartSeconds(id, season, episode) {
  const params = new URLSearchParams();
  if (season != null) params.set("season", season);
  if (episode != null) params.set("episode", episode);
  const qs = params.toString() ? "?" + params.toString() : "";
  const wp = await api("/watch/progress/" + id + qs).catch(() => null);
  if (wp && !wp.completed && wp.position_ms > 30000) return wp.position_ms / 1000;
  return 0;
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
    const start = await savedStartSeconds(d.id, season, episode);
    await api("/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: resolved.download_url, file_path: label, start_seconds: start }),
    });
    navPush("nowplaying", { id: d.id, source, season, episode });
  } catch (e) {
    alert("Playback failed: " + e.message);
  }
}

async function startPlayback(d, btn, seasonSel, episodeSel) {
  btn.disabled = true;
  // Open the modal up front so the user sees it working; the list fills in
  // once sources come back.
  const handles = openSourcePickerLoading(d, btn, seasonSel, episodeSel);
  const season = seasonSel ? Number(seasonSel.value) : null;
  const episode = episodeSel ? Number(episodeSel.value) : null;
  const key = srcKey(d, season, episode);
  try {
    // Reuse a background prefetch if one is cached or in flight; only fall
    // back to a fresh fetch when Play was clicked before it had started.
    let srcs = sourceCache.get(key) || null;
    if (!srcs) {
      const inflight = sourceInFlight.get(key);
      if (inflight) {
        srcs = await inflight.promise;
      } else {
        const body = d._type === "tv"
          ? { season: season || 1, episode: episode || 1 }
          : {};
        srcs = await api(`/sources/${d.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
    }
    if (!srcs.sources || !srcs.sources.length) {
      showPickerMessage(handles, "No sources found for this title.");
    } else {
      fillSourceList(handles, d, srcs.sources, seasonSel, episodeSel);
    }
  } catch (e) {
    showPickerMessage(handles, "Source lookup failed: " + e.message);
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
  nowPlaying = { id: d.id, season, episode };
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
    nowPlaying = null;
    try {
      await api("/play/progress", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title_id: d.id, season, episode }) });
    } catch (e) { /* best-effort save */ }
    await api("/play/quit", { method: "POST" });
    navBack();
  });

  let lastSavedPos = 0;
  playPollTimer = setInterval(async () => {
    try {
      const p = await api("/play/position");
      if (!p.playing) return;
      cur.textContent = fmtTime(p.time_pos);
      dur.textContent = fmtTime(p.duration);
      if (p.duration) fill.style.width = Math.min(100, (p.time_pos / p.duration) * 100) + "%";
      // Autosave roughly every 10s so a crash or quit does not lose progress
      // (Stop also saves; this covers the gap between saves).
      if (p.time_pos - lastSavedPos >= 10) {
        lastSavedPos = p.time_pos;
        api("/play/progress", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title_id: d.id, season, episode }) }).catch(() => {});
      }
    } catch (e) { /* ignore transient poll errors */ }
  }, 2000);
}

async function renderSearch(q, category) {
  stopPlayPoll();
  category = category || "all";
  // Empty query: show a prompt and focus the search box; no API call.
  if (!q) {
    app.innerHTML = "";
    const box = el("div", "boot");
    box.appendChild(el("p", "muted", "Search for a movie or show to add it to your library."));
    app.appendChild(box);
    if (searchInput) searchInput.focus();
    window.scrollTo(0, 0);
    return;
  }
  const key = "search:" + q;
  let res = cacheGet(key);
  if (!res) { res = await api(`/tmdb/search?q=${encodeURIComponent(q)}`); cacheSet(key, res); }
  renderSearchGrid(q, res, category);
}

function renderSearchGrid(q, res, category) {
  app.innerHTML = "";
  const tabs = el("div", "cat-tabs");
  for (const [val, label] of [["all", "All"], ["movie", "Movies"], ["tv", "TV"]]) {
    const t = el("button", "cat-tab" + (val === category ? " active" : ""), label);
    t.addEventListener("click", () => renderSearchGrid(q, res, val));
    tabs.appendChild(t);
  }
  app.appendChild(tabs);
  let items = [];
  if (category !== "movie") items = items.concat(res.movie || []);
  if (category !== "tv") items = items.concat(res.tv || []);
  if (!items.length) { app.appendChild(el("p", "muted", `No results for "${q}".`)); return; }
  const grid = el("div", "grid");
  for (const it of items) grid.appendChild(makeCard(it));
  app.appendChild(grid);
}

let searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (!q) { navHome(); return; }
  searchTimer = setTimeout(() => {
    const st = history.state;
    if (st && st.view === "search") {
      // Refine the current search in place - don't flood the back-stack.
      const cat = (st.params && st.params.category) || "all";
      history.replaceState({ view: "search", params: { query: q, category: cat }, scroll: 0 }, "", "");
      renderSearch(q, cat);
      updateBackButton();
    } else {
      navPush("search", { query: q, category: "all" });
    }
  }, 300);
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
  return [...app.querySelectorAll(".card, .btn, .src-row, select")];
}

function buildFocusMap() {
  const els = focusables();
  const rows = [];
  const inGrid = new Set();
  // Vertical grids: group cards by their visual row (shared offsetTop) so
  // arrows navigate 2D across and along the grid. offsetTop forces layout,
  // so this is reliable even right after the DOM update.
  for (const g of app.querySelectorAll(".grid")) {
    const cards = [...g.querySelectorAll(":scope > .card")];
    const byTop = new Map();
    for (const c of cards) {
      const top = c.offsetTop;
      if (!byTop.has(top)) byTop.set(top, []);
      byTop.get(top).push(c);
    }
    for (const [top, rowCards] of [...byTop.entries()].sort((a, b) => a[0] - b[0])) {
      rows.push(rowCards);
      for (const c of rowCards) inGrid.add(c);
    }
  }
  // Everything else: one focus row per container (.cards/.info/.np-controls/.ep-picker).
  const byRow = new Map();
  for (const e of els) {
    if (inGrid.has(e)) continue;
    const rowEl = e.closest(".cards, .info, .np-controls, .ep-picker") || e;
    if (!byRow.has(rowEl)) byRow.set(rowEl, []);
    byRow.get(rowEl).push(e);
  }
  focusRows = rows.concat([...byRow.values()]);
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
  navBack();
}

function stepSelect(el, delta) {
  const idx = el.selectedIndex + delta;
  if (idx < 0 || idx >= el.options.length) return;
  el.selectedIndex = idx;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

/* Settings: Trakt sign-in (device flow) + sync */
async function renderSettings() {
  const app = document.getElementById("app");
  app.innerHTML =
    '<div class="settings"><h1>Settings</h1>' +
    '<section class="trakt-panel" id="trakt-panel"><h2>Trakt</h2>' +
    '<p class="muted">Loading&hellip;</p></section></div>';
  await loadTraktStatus();
}

async function loadTraktStatus() {
  const panel = document.getElementById("trakt-panel");
  if (!panel) return;
  let st;
  try { st = await api("/trakt/status"); }
  catch (e) { panel.innerHTML = '<h2>Trakt</h2><p class="muted">Status unavailable.</p>'; return; }
  if (st.authenticated) {
    let syncLine = "";
    if (st.last_sync) {
      syncLine = '<p class="muted">Last sync: ' + esc(st.last_sync.last_sync_at) +
        (st.last_sync.last_error ? ' &mdash; ' + esc(st.last_sync.last_error) : '') + '</p>';
    }
    panel.innerHTML = '<h2>Trakt</h2>' +
      '<p>Connected as <strong>' + esc(st.username || "unknown") + '</strong></p>' +
      syncLine +
      '<button class="btn" id="trakt-sync">Sync now</button>' +
      '<p class="trakt-msg muted" id="trakt-msg"></p>';
    const b = document.getElementById("trakt-sync");
    if (b) b.addEventListener("click", doTraktSync);
  } else {
    panel.innerHTML = '<h2>Trakt</h2>' +
      '<p class="muted">Not connected. Sign in to sync watch history and continue-watching.</p>' +
      '<button class="btn" id="trakt-signin">Sign in with Trakt</button>' +
      '<div class="trakt-auth-flow" id="trakt-auth-flow"></div>';
    const b = document.getElementById("trakt-signin");
    if (b) b.addEventListener("click", startTraktAuth);
  }
}

let traktPollTimer = null;
async function startTraktAuth() {
  const flow = document.getElementById("trakt-auth-flow");
  if (!flow) return;
  flow.innerHTML = '<p class="muted">Requesting code&hellip;</p>';
  let res;
  try { res = await api("/trakt/auth", { method: "POST" }); }
  catch (e) { flow.innerHTML = '<p class="muted">Could not start sign-in: ' + esc(e.message) + '</p>'; return; }
  flow.innerHTML =
    '<p>Enter this code at the link, then approve:</p>' +
    '<div class="user-code">' + esc(res.user_code) + '</div>' +
    '<a class="btn" href="' + esc(res.verification_url) + '" target="_blank" rel="noopener">Open ' + esc(res.verification_url) + '</a>' +
    '<p class="trakt-msg muted">Waiting for approval&hellip;</p>';
  pollTraktAuth();
}

function pollTraktAuth() {
  if (traktPollTimer) clearInterval(traktPollTimer);
  traktPollTimer = setInterval(async () => {
    let st;
    try { st = await api("/trakt/auth"); } catch (e) { return; }
    const msg = document.querySelector("#trakt-auth-flow .trakt-msg");
    if (st.status === "authorized") {
      clearInterval(traktPollTimer); traktPollTimer = null;
      if (msg) msg.textContent = "Connected as " + (st.username || "your account") + ".";
      setTimeout(loadTraktStatus, 800);
    } else if (st.status === "expired") {
      clearInterval(traktPollTimer); traktPollTimer = null;
      if (msg) msg.textContent = "The code expired. Sign in again.";
    }
  }, 5000);
}

async function doTraktSync() {
  const msg = document.getElementById("trakt-msg");
  if (msg) msg.textContent = "Syncing&hellip;";
  let res;
  try { res = await api("/trakt/sync", { method: "POST" }); }
  catch (e) { if (msg) msg.textContent = "Sync failed: " + e.message; return; }
  if (msg) msg.textContent = res.error
    ? "Sync: " + res.error + " (pushed " + res.pushed + ", pulled " + res.pulled + ")"
    : "Synced: pushed " + res.pushed + ", pulled " + res.pulled + ".";
  setTimeout(loadTraktStatus, 600);
}

document.addEventListener("keydown", (e) => {
  const search = document.getElementById("search");
  const inSearch = document.activeElement === search;
  if (inSearch) {
    // Pass text editing through (typing, backspace, delete, punctuation);
    // only hand off to grid navigation for arrows / enter / escape.
    const nav = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Enter", "Escape"].includes(e.key);
    if (!nav) return;
    search.blur();
  }
  if (e.key === "/") { e.preventDefault(); search.focus(); return; }
  if (!inSearch && e.key === "s") { e.preventDefault(); navPush("settings", {}); return; }

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

// ---- startup grace ----
// The desktop webview loads before the sidecar binds :8790, so poll /health
// until the backend is up (or time out) before rendering. This turns the
// "frontend loads first" race into a brief "Starting..." state instead of a
// dead-end error screen.
async function ensureBackend(timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(API + "/health");
      if (res.ok) return;
    } catch (_) {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 750));
  }
  throw new Error("backend timeout");
}

function bootScreen(lines) {
  app.innerHTML = "";
  const box = el("div", "boot");
  for (const t of lines) box.appendChild(el("p", "muted pulse", t));
  app.appendChild(box);
  return box;
}

function showBackendDown(retryFn) {
  const box = bootScreen(["Backend didn't start."]);
  const btn = el("button", "btn", "Retry");
  btn.addEventListener("click", retryFn);
  box.appendChild(btn);
}

async function boot() {
  history.replaceState({ view: "home", params: {}, scroll: 0 }, "", "");
  bootScreen(["Starting..."]);
  try {
    await ensureBackend();
  } catch (_) {
    showBackendDown(boot);
    updateBackButton();
    return;
  }
  renderHome();
  updateBackButton();
}

boot();
