// home-media frontend - vanilla JS, no bundler.
// Talks to the local backend on 127.0.0.1:8790.

const API = "http://127.0.0.1:8790";
const app = document.getElementById("app");
const searchInput = document.getElementById("search");

let playPollTimer = null;
let nowPlaying = null; // { id, season, episode } of the current playback
let homeStash = null; // detached live home DOM while we're away from home

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

console.log("[DEBUG] __TAURI__:", typeof window.__TAURI__);
console.log("[DEBUG] __TAURI_INTERNALS__:", typeof window.__TAURI_INTERNALS__);
console.log("[DEBUG] __TAURI_INTERNALS__.invoke:", typeof window.__TAURI_INTERNALS__?.invoke);
console.log("[DEBUG] __TAURI_INTERNALS__ keys:", window.__TAURI_INTERNALS__ ? Object.keys(window.__TAURI_INTERNALS__) : "n/a");

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function tauriInvoke(cmd, args) {
  const t = window.__TAURI_INTERNALS__ || window.__TAURI__?.core;
  if (!t) return null;
  return t.invoke(cmd, args);
}

function openExternal(url) {
  const p = tauriInvoke("open_url", { url });
  if (p) {
    p.then(() => console.log("[openExternal] success:", url))
     .catch(err => console.error("[openExternal] FAILED:", err));
  } else {
    window.open(url, "_blank");
  }
}

// Persistent hero on document.body (outside #app so the MutationObserver
// never fires on its mutations). Shown on focus/hover, hidden on view change.
let heroEl = null;
let heroBackdrop = null;
let heroInfo = null;
let backdropHideTimer = null;
const titleCache = {};

function initHero() {
  if (heroEl) return;
  heroEl = document.createElement("div");
  heroEl.className = "hero";
  heroBackdrop = document.createElement("div");
  heroBackdrop.className = "backdrop";
  const img = document.createElement("img");
  img.src = "data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAA==";
  img.onerror = function () {
    const cur = this.src;
    if (cur.includes("w1920")) this.src = cur.replace("w1920", "w1280");
  };
  heroBackdrop.appendChild(img);
  heroInfo = document.createElement("div");
  heroInfo.className = "hero-info";
  const h1 = document.createElement("h1");
  h1.className = "hero-title";
  const p = document.createElement("p");
  p.className = "hero-synopsis";
  heroInfo.appendChild(h1);
  heroInfo.appendChild(p);
  heroEl.appendChild(heroBackdrop);
  heroEl.appendChild(heroInfo);
  document.body.appendChild(heroEl);
}

function showBackdrop(item) {
  if (!item || !item.id) return;
  if (backdropHideTimer) { clearTimeout(backdropHideTimer); backdropHideTimer = null; }
  const mt = item._type || item.type;
  const t = (mt === "movie" || mt === "tv") ? "?type=" + mt : "";
  heroBackdrop.querySelector("img").src = API + "/tmdb/backdrop/" + item.id + t + "&size=w1920";
  heroBackdrop.classList.add("show");
  const key = item.id + "-" + (mt || "");
  if (titleCache[key]) {
    heroInfo.querySelector(".hero-title").textContent = titleCache[key].title;
    heroInfo.querySelector(".hero-synopsis").textContent = titleCache[key].overview;
  } else {
    api("/tmdb/title/" + item.id + (t ? t : ""))
      .then((d) => {
        const info = { title: d.title || d.name || "", overview: d.overview || "" };
        titleCache[key] = info;
        if (heroBackdrop.classList.contains("show")) {
          heroInfo.querySelector(".hero-title").textContent = info.title;
          heroInfo.querySelector(".hero-synopsis").textContent = info.overview;
        }
      })
      .catch(() => {});
  }
}

function hideBackdrop() {
  if (backdropHideTimer) clearTimeout(backdropHideTimer);
  heroEl.classList.remove("full");
  backdropHideTimer = setTimeout(() => {
    heroBackdrop.classList.remove("show");
    backdropHideTimer = null;
  }, 250);
}

function hideHero() {
  if (backdropHideTimer) { clearTimeout(backdropHideTimer); backdropHideTimer = null; }
  heroBackdrop.classList.remove("show");
  heroEl.classList.remove("full");
  heroEl.style.display = "none";
  document.body.classList.add("no-hero");
  document.body.classList.remove("full-hero");
}

function showHero() {
  heroEl.style.display = "block";
  document.body.classList.remove("no-hero");
}

initHero();

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
  const body = d._type === "tv"
    ? { season, episode, media_type: d._type }
    : { media_type: d._type };
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
  if (view === "detail") return openDetail(params.id, params.type, params.season, params.episode);
  if (view === "settings") return renderSettings();
  if (view === "library") return renderLibrary();
  if (view === "librarytitle") return openLibraryTitle(params.id);
  if (view === "downloads") return renderDownloads();
  if (view === "nowplaying") {
    renderNowPlaying(cacheGet("detail:" + params.id) || {}, params.source, params.season, params.episode);
    return;
  }
  return renderHome(params && params.category);
}

function navPush(view, params) {
  if (view === "home") { renderHome(params && params.category); updateBackButton(); return; }
  // Leaving home: stash the live home DOM so "back" restores it exactly -
  // scroll, active tab, and focused card all preserved, no re-fetch.
  if (history.state && history.state.view === "home") {
    // Capture scroll + focus BEFORE detaching the DOM - detaching collapses the
    // page to height 0, which would reset scrollY to 0 and blur the focus.
    const fe = currentFocus() || document.activeElement;
    const savedScroll = window.scrollY;
    const savedFocus = (fe && fe.dataset && fe.dataset.id) ? fe.dataset.id : null;
    homeStash = document.createElement("div");
    while (app.firstChild) homeStash.appendChild(app.firstChild);
    history.replaceState({
      view: "home",
      params: { category: (history.state.params && history.state.params.category) || "all" },
      scroll: savedScroll,
      focus: savedFocus,
    }, "", "");
  }
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
  if (!s || s.view === "home") {
    showHero();
    heroEl.classList.remove("full");
    document.body.classList.remove("full-hero");
    if (homeStash && homeStash.childNodes.length) {
      // Restore the exact home DOM (no re-fetch): scroll, tab, and focus are
      // preserved because the same nodes come back.
      app.innerHTML = "";
      while (homeStash.firstChild) app.appendChild(homeStash.firstChild);
      homeStash = null;
      setTimeout(() => {
        window.scrollTo(0, (s && s.scroll) || 0);
        if (s && s.focus) {
          const fe = document.querySelector('.card[data-id="' + s.focus + '"]');
          if (fe) {
            for (let r = 0; r < focusRows.length; r++) {
              const c = focusRows[r].indexOf(fe);
              if (c !== -1) { focusRow = r; focusCol = c; fe.classList.add("focused"); break; }
            }
          }
        }
      }, 0);
    } else {
      renderHome(s && s.params && s.params.category);
    }
  } else {
    renderView(s.view, s.params);
    setTimeout(() => window.scrollTo(0, (s && s.scroll) || 0), 0);
  }
  updateBackButton();
});

function updateBackButton() {
  const b = document.getElementById("back");
  if (b) b.classList.toggle("hidden", !(history.state && history.state.view && history.state.view !== "home"));
}

document.getElementById("back").addEventListener("click", navBack);
document.getElementById("home").addEventListener("click", navHome);
document.getElementById("library").addEventListener("click", () => navPush("library", {}));
document.getElementById("downloads").addEventListener("click", () => navPush("downloads", {}));
document.getElementById("settings").addEventListener("click", () => navPush("settings", {}));
document.getElementById("power").addEventListener("click", exitAndSync);

// ---- service status (TMDB / Torbox / Trakt) --------------------------------
// A lightweight top-bar indicator: a dot per service that needs attention
// (no key / not signed in). Hovering a dot shows a box explaining how to fix
// it; "Open Settings" jumps to and highlights the relevant panel. Nothing
// renders when every service is healthy. Driven by the three /status endpoints
// (they read the in-app settings table, so no external calls — stays light).
let svcStatus = null; // { tmdb: bool, torbox: bool, trakt: bool }
let updateAvailable = false;

const SVC_MESSAGES = {
  tmdb: { name: "TMDB", msg: "Not configured. Add your token to enable metadata (trending, posters, titles)." },
  torbox: { name: "Torbox", msg: "Not connected. Add your key to enable debrid downloads." },
  trakt: { name: "Trakt", msg: "Not signed in. Sign in to sync watch history and continue-watching." },
};

async function getServiceStatus() {
  const [tmdb, torbox, trakt] = await Promise.allSettled([
    api("/tmdb/status"),
    api("/sources/status"),
    api("/trakt/status"),
  ]);
  svcStatus = {
    tmdb: tmdb.status === "fulfilled" && tmdb.value.configured,
    torbox: torbox.status === "fulfilled" && torbox.value.configured,
    trakt: trakt.status === "fulfilled" && trakt.value.authenticated,
  };
  return svcStatus;
}

function refreshSvcStatus() {
  return getServiceStatus().then(renderServiceStatus);
}

function renderServiceStatus() {
  const wrap = document.getElementById("svc-status");
  if (!wrap) return;
  wrap.innerHTML = "";
  if (!svcStatus) return;
  for (const key of ["tmdb", "torbox", "trakt"]) {
    if (svcStatus[key]) continue; // healthy -> no dot
    const m = SVC_MESSAGES[key];
    const dot = el("span", "svc-dot");
    dot.title = m.name + " needs attention";
    const tip = el("span", "svc-tip");
    tip.innerHTML =
      "<strong>" + m.name + "</strong>" +
      '<span class="svc-msg">' + m.msg + "</span>" +
      '<button class="svc-fix" data-panel="' + key + '-panel">Open Settings</button>';
    dot.appendChild(tip);
    wrap.appendChild(dot);
  }
  if (updateAvailable) {
    const dot = el("span", "svc-dot svc-dot-update");
    dot.title = "Update available";
    const tip = el("span", "svc-tip");
    tip.innerHTML =
      "<strong>Update available</strong>" +
      '<span class="svc-msg">A new version of Home Media is ready to install.</span>' +
      '<button class="svc-fix" data-panel="updates-panel">Open Settings</button>';
    dot.appendChild(tip);
    wrap.appendChild(dot);
  }
}

async function checkForUpdateOnStartup() {
  try {
    const update = await tauriInvoke("check_for_update");
    updateAvailable = (update === true);
    renderServiceStatus();
  } catch (e) { /* network error — stay hidden */ }
}

// One-time delegated handler: "Open Settings" from any status dot.
document.getElementById("svc-status").addEventListener("click", (e) => {
  const b = e.target.closest(".svc-fix");
  if (b) openSettingsFor(b.dataset.panel);
});

function openSettingsFor(panelId) {
  navPush("settings", {});
  // renderSettings sets the panels synchronously, so a short delay is enough to
  // scroll to + highlight the relevant one; retry briefly in case it's not up yet.
  let tries = 0;
  const go = () => {
    const p = document.getElementById(panelId);
    if (p) {
      p.scrollIntoView({ behavior: "smooth", block: "start" });
      p.classList.add("panel-highlight");
      setTimeout(() => p.classList.remove("panel-highlight"), 1600);
    } else if (++tries < 15) {
      setTimeout(go, 40);
    }
  };
  setTimeout(go, 40);
}

// ---- quit ----
// The power button asks before quitting. Cancel: stay.
// Confirm: stop playback and close the window.
function exitAndSync() {
  const btn = document.getElementById("power");
  if (btn) { btn.classList.add("syncing"); btn.title = "Syncing..."; }
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
  card.dataset.id = item.id; // stable id so "back" can restore focus to this card
  card._item = item; // reference for the hero backdrop
  const img = el("img", "poster");
  img.loading = "lazy";
  img.alt = item.title || item.name || "";
  const src = posterSrc(item);
  if (src) {
    img.src = POSTER_PLACEHOLDER; // fill in when (near) visible
    img.dataset.posterSrc = src;
    observePoster(img);
  }
  const posterWrap = el("div", "poster-wrap");
  posterWrap.appendChild(img);

  // Progress bar overlay at the bottom of the poster (red, Netflix-style).
  // Always visible (persistent), not hover-gated. Movies only: TV show
  // progress lives on individual episode cards, not the show card (the show
  // card's position is just the current episode).
  if (item.position_ms != null && (item.duration_ms > 0 || item.completed) && item.type !== "tv") {
    const frac = item.completed ? 1 : Math.min(1, item.position_ms / item.duration_ms);
    const bar = el("div", "card-progress");
    const fill = el("i");
    fill.style.width = (frac * 100) + "%";
    bar.appendChild(fill);
    posterWrap.appendChild(bar);
    if (item.completed) posterWrap.appendChild(el("span", "card-check", "\u2713"));
  }
  card.appendChild(posterWrap);

  const meta = el("div", "meta");
  meta.appendChild(el("div", "title", item.title || item.name || ""));
  card.appendChild(meta);
  card.tabIndex = 0; // focusable so "back" can restore focus here
  card.setAttribute("role", "button");
  card.addEventListener("click", () => navPush("detail", { id: item.id, type: item.type, season: item.season, episode: item.episode }));
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      navPush("detail", { id: item.id, type: item.type, season: item.season, episode: item.episode });
    }
  });
  // Netflix/Prime-style: hovering a card updates the backdrop at the top
  // and moves the focus there so keyboard nav picks up from the mouse.
  card.addEventListener("mouseenter", () => {
    // Ignore hovers that are just the page scrolling under a stationary cursor
    // (no physical mouse move, or still within the scroll lock). A real mouse
    // move sets pointerMoved, so deliberate hover-to-navigate still works; this
    // only stops the scroll-induced re-hover chain from re-focusing/re-scrolling.
    if (!pointerMoved || Date.now() < scrollLockUntil) return;
    pointerMoved = false;
    showBackdrop(item);
    focusFromHover(card);
  });
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
      completed: it.completed,
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
  showHero();
  heroEl.classList.remove("full");
  document.body.classList.remove("full-hero");
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
  // Restore the exact home state (scroll + focused card) if this history entry
  // carries one - this is what makes "back" return to exactly where you were.
  // The page can still grow as lazy posters land, so an early scrollTo gets
  // clamped; keep retrying (over a short window) until we actually reach the
  // target, then stop so we never fight a user who has started scrolling.
  const st = history.state;
  if (st && (st.scroll || st.focus)) {
    const target = st.scroll || 0;
    const fid = st.focus || null;
    let tries = 0;
    const restore = () => {
      window.scrollTo(0, target);
      if (fid) { const fe = document.querySelector('.card[data-id="' + fid + '"]'); if (fe) fe.focus(); }
      if (Math.abs(window.scrollY - target) > 4 && ++tries < 150) setTimeout(restore, 16);
    };
    setTimeout(restore, 16);
  }
}

async function renderHome(category) {
  stopPlayPoll();
  category = category || "all";
  app.innerHTML = "";
  const [watch, popular, trending, latest, recs, status] = await Promise.allSettled([
    api("/watch"),
    api("/tmdb/popular"),
    api("/tmdb/home"),
    api("/tmdb/latest"),
    api("/trakt/recommendations"),
    getServiceStatus(),
  ]);
  const rows = buildHomeRows(watch, popular, trending, latest, recs);
  const allRejected = [watch, popular, trending, latest, recs].every((r) => r.status === "rejected");
  renderHomeRows(rows, category, allRejected);
  // Surface a missing key right where that service's rows would be.
  if (status.status === "fulfilled" && !status.value.tmdb) {
    const note = el("div", "svc-note");
    note.appendChild(el("span", null, "Set your TMDB key to see trending, popular, and latest."));
    const btn = el("button", "btn", "Open Settings");
    btn.addEventListener("click", () => openSettingsFor("tmdb-panel"));
    note.appendChild(btn);
    app.appendChild(note);
  }
  renderServiceStatus();
}

// ---- offline library (works with no internet) ----
async function renderLibrary() {
  stopPlayPoll();
  hideHero();
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
  const trash = el("button", "lib-trash");
  trash.title = "Remove from library";
  trash.setAttribute("aria-label", "Remove from library");
  trash.innerHTML = TRASH_SVG;
  trash.addEventListener("click", (e) => {
    e.stopPropagation();
    removeFromLibrary(t.tmdb_id, null, t.title);
  });
  card.appendChild(trash);
  card.addEventListener("click", () => navPush("librarytitle", { id: t.tmdb_id }));
  return card;
}

async function openLibraryTitle(id) {
  stopPlayPoll();
  hideHero();
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
      const trash = el("button", "ep-trash");
      trash.title = "Remove episode";
      trash.setAttribute("aria-label", "Remove episode");
      trash.innerHTML = TRASH_SVG;
      trash.addEventListener("click", (e) => {
        e.stopPropagation();
        const label = t.title + (ep.episode != null ? ` E${ep.episode}` : "");
        removeFromLibrary(t.tmdb_id, ep.episode != null ? ep.episode : null, label);
      });
      rowEl.appendChild(trash);
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

// ---- library delete + downloads screen -------------------------------------

const TRASH_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6"/></svg>';

function playLabelFor(d, seasonSel, episodeSel) {
  const ep = (d._type === "tv" && seasonSel && episodeSel)
    ? ` S${seasonSel.value} E${episodeSel.value}` : "";
  return "Play/DL" + ep;
}

let toastTimer = null;
function toast(msg) {
  let t = document.getElementById("app-toast");
  if (!t) {
    t = el("div", "app-toast");
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

async function removeFromLibrary(titleId, episode, label) {
  const msg = episode != null
    ? `Remove "${label}" from your library? This deletes the downloaded file.`
    : `Remove "${label}" from your library? This deletes all of its downloaded files.`;
  if (!confirm(msg)) return;
  try {
    const path = episode != null ? `/library/${titleId}/episodes/${episode}` : `/library/${titleId}`;
    await api(path, { method: "DELETE" });
    if (history.state && history.state.view === "librarytitle") {
      openLibraryTitle(titleId);
    } else {
      renderLibrary();
    }
  } catch (e) {
    alert("Could not remove: " + e.message);
  }
}

let dlPollTimer = null;

async function renderDownloads() {
  stopPlayPoll();
  hideHero();
  app.innerHTML = "";
  const wrap = el("div", "downloads-view");
  wrap.appendChild(el("h1", null, "Downloads"));
  wrap.appendChild(el("p", "muted", "Active downloads. Finished items appear in your Library."));
  const list = el("div", "dl-list");
  wrap.appendChild(list);
  const empty = el("p", "muted dl-empty", "No active downloads.");
  empty.style.display = "none";
  wrap.appendChild(empty);
  app.appendChild(wrap);
  await refreshDownloads(list, empty);
  if (dlPollTimer) clearInterval(dlPollTimer);
  dlPollTimer = setInterval(() => refreshDownloads(list, empty), 3000);
  window.scrollTo(0, 0);
}

async function refreshDownloads(list, empty) {
  let data;
  try {
    data = await api("/downloads");
  } catch (e) {
    return;
  }
  const active = (data.downloads || []).filter(
    (d) => d.status === "queued" || d.status === "downloading"
  );
  updateDlBadge(active.length);
  list.innerHTML = "";
  empty.style.display = active.length ? "none" : "";
  for (const d of active) {
    const name = d.file_path ? String(d.file_path).split(/[\\/]/).pop()
      : `Title #${d.title_id}${d.episode != null ? " E" + d.episode : ""}`;
    const pct = d.status === "downloading" ? Math.round((d.progress || 0) * 100) : 0;
    const item = el("div", "dl-item");
    const top = el("div", "dl-item-top");
    top.appendChild(el("span", "dl-name", name));
    top.appendChild(el("span", "dl-pct", d.status === "queued" ? "Queued" : pct + "%"));
    item.appendChild(top);
    const track = el("div", "dl-progress-track");
    const bar = el("div", "dl-progress");
    bar.style.width = pct + "%";
    track.appendChild(bar);
    item.appendChild(track);
    const foot = el("div", "dl-item-foot");
    foot.appendChild(el("span", "dl-size", d.size_bytes ? fmtBytes(d.size_bytes) : ""));
    const cancel = el("button", "btn btn-ghost dl-cancel", "Cancel");
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      try {
        await api("/downloads/" + d.id, { method: "DELETE" });
        await refreshDownloads(list, empty);
      } catch (e) {
        alert("Could not cancel: " + e.message);
        cancel.disabled = false;
      }
    });
    foot.appendChild(cancel);
    item.appendChild(foot);
    list.appendChild(item);
  }
}

function updateDlBadge(n) {
  const badge = document.getElementById("dl-badge");
  if (!badge) return;
  badge.textContent = n;
  badge.classList.toggle("hidden", n === 0);
}

// Lightweight global poll so the downloads badge stays current on any view.
async function updateDlBadgeFromApi() {
  try {
    const data = await api("/downloads");
    const active = (data.downloads || []).filter(
      (d) => d.status === "queued" || d.status === "downloading"
    ).length;
    updateDlBadge(active);
  } catch (e) { /* ignore */ }
}

function startBadgePoll() {
  updateDlBadgeFromApi();
  setInterval(updateDlBadgeFromApi, 10000);
}

async function openDetail(id, type, season, episode) {
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
      if (season != null && episode != null) {
        // A Continue Watching card carries its resume episode - open that
        // directly instead of re-deriving the most-recent one.
        currentEp = { season, episode };
      } else {
        // Cheap localhost read of local watch progress; defaults the picker and
        // the background prefetch to the episode the user is on.
        const wp = await api("/watch/progress/" + id).catch(() => null);
        if (wp && wp.episode != null) currentEp = { season: wp.season, episode: wp.episode };
      }
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

  let seasonSel = null, episodeSel = null, selectedEp = 1, curSeason = 1, curEp = 1;
  let picker = null, pickerActiveEl = null, seasonBtn = null, grid = null;
  if (d._type === "tv" && Array.isArray(d.seasons) && d.seasons.length) {
    // Proxies so the downstream play/prefetch/mark-watched code that reads
    // *.value keeps working; the episode grid and the season dropdown are the
    // real selectors (they mutate selectedEp / curSeason).
    episodeSel = { get value() { return String(selectedEp); } };
    seasonSel = { get value() { return String(curSeason); } };
    picker = el("div", "ep-picker");
    // Custom season dropdown. A native <select> traps the arrow keys (focusing
    // it and pressing up/down changes the season, so you can never reach the
    // episodes). This is a button you "enter" with Enter/Space; only while the
    // list is open do the arrows do anything.
    seasonBtn = el("button", "season-btn", `Season ${curSeason}`);
    seasonBtn.setAttribute("aria-haspopup", "listbox");
    seasonBtn.setAttribute("aria-expanded", "false");
    const seasonList = el("div", "season-list");
    seasonList.setAttribute("role", "listbox");
    seasonList.tabIndex = -1;
    const seasonItems = [];
    for (const s of d.seasons) {
      if (s.season_number === 0) continue;
      const item = el("div", "season-item", `Season ${s.season_number}`);
      item.dataset.season = s.season_number;
      item.setAttribute("role", "option");
      seasonList.appendChild(item);
      seasonItems.push(item);
    }
    picker.appendChild(seasonBtn);
    picker.appendChild(seasonList);
    wrap.appendChild(picker);
    grid = el("div", "ep-grid");
    wrap.appendChild(grid);

    const setEp = (n) => {
      selectedEp = n;
      grid.querySelectorAll(".ep-card").forEach((c) =>
        c.classList.toggle("selected", Number(c.dataset.ep) === n));
    };
    // Keep keyboard focus in the episode list (the current card) rather than
    // letting it fall back to the Play button.
    const focusCurrentCard = () => {
      const cards = [...grid.querySelectorAll(".ep-card")];
      const cur = cards.find((c) => Number(c.dataset.ep) === selectedEp) || cards[0];
      if (cur) cur.focus();
    };
    // Grid keyboard nav, added once (not per render) so listeners don't
    // accumulate across season changes: arrows move between cards, Enter/Space
    // plays the focused card.
    grid.addEventListener("keydown", (e) => {
      // Stop the global document keydown handler from also running its own
      // grid-nav on the same key (that double-handling was the dual-focus bug).
      if (["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Enter", " ", "Spacebar"].includes(e.key)) {
        e.stopPropagation();
      }
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        const card = e.target.closest(".ep-card");
        if (!card) return;
        e.preventDefault();
        setEp(Number(card.dataset.ep));
        startPlayback(d, btn, seasonSel, episodeSel);
        return;
      }
      if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) return;
      e.preventDefault();
      const cards = [...grid.querySelectorAll(".ep-card")];
      const i = cards.indexOf(document.activeElement);
      if (i === -1) { cards[0].focus(); return; }
      // Down/Up move a full column (next/prev row); Left/Right move within a
      // row. The column count comes from the live grid so it tracks the
      // responsive breakpoints (4/3/2/1).
      const cols = (getComputedStyle(grid).gridTemplateColumns || "").split(" ").filter(Boolean).length || 1;
      // Up from the top row returns to the picker row (the control last used).
      if (e.key === "ArrowUp" && i < cols) { (pickerActiveEl || seasonBtn).focus(); return; }
      let next;
      if (e.key === "ArrowRight") next = Math.min(i + 1, cards.length - 1);
      else if (e.key === "ArrowLeft") next = Math.max(i - 1, 0);
      else if (e.key === "ArrowDown") next = Math.min(i + cols, cards.length - 1);
      else next = Math.max(i - cols, 0);
      cards[next].focus();
      cards[next].scrollIntoView({ block: "center" });
      // If the card ended up above the visible area (below the topbar),
      // scroll up so the picker row is in view.
      const rect = cards[next].getBoundingClientRect();
      const tb = document.querySelector(".topbar");
      const topLimit = (tb ? tb.offsetHeight : 0) + 8;
      if (rect.top < topLimit) {
        const pk = grid.closest(".detail")?.querySelector(".ep-picker");
        if (pk) {
          const pkPageY = pk.getBoundingClientRect().top + window.scrollY;
          window.scrollTo(0, pkPageY - topLimit);
        }
      }
      });

    // Fetch a season's episodes and render them as cards (still, title,
    // synopsis). Stills are proxied + disk-cached like posters (offline-safe).
    const renderEpisodes = async (season) => {
      grid.innerHTML = "";
      grid.appendChild(el("div", "ep-loading", "Loading episodes..."));
      let eps = [];
      let seasonProgress = {};
      try {
        const [data, prog] = await Promise.all([
          api(`/tmdb/season/${d.id}/${season}`),
          api(`/watch/progress/${d.id}/season/${season}`).catch(() => ({})),
        ]);
        eps = (data.episodes || []).slice()
          .sort((a, b) => a.episode_number - b.episode_number);
        seasonProgress = prog;
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
        card.tabIndex = 0;
        card.setAttribute("role", "button");
        const key = d.id + "-s" + season + "e" + ep.episode_number;
        const img = el("img", "ep-still");
        if (ep.still_path) {
          img.src = API + "/tmdb/image?path=" + encodeURIComponent(ep.still_path)
            + "&key=" + encodeURIComponent(key) + "&size=w500";
        } else {
          card.classList.add("no-still");
        }
        const posterWrap = el("div", "poster-wrap");
        posterWrap.appendChild(img);
        card.appendChild(posterWrap);
        const epProg = seasonProgress[ep.episode_number];
        if (epProg) {
          const frac = epProg.completed ? 1 : (epProg.duration_ms > 0 ? Math.min(1, epProg.position_ms / epProg.duration_ms) : 0);
          if (frac > 0) {
            const bar = el("div", "card-progress");
            const fill = el("i");
            fill.style.width = (frac * 100) + "%";
            bar.appendChild(fill);
            posterWrap.appendChild(bar);
            if (epProg.completed) posterWrap.appendChild(el("span", "card-check", "\u2713"));
          }
        }
        const body = el("div", "ep-body");
        body.appendChild(el("div", "ep-name", `E${ep.episode_number} \u00b7 ${ep.name || "Untitled"}`));
        if (ep.overview) body.appendChild(el("div", "ep-overview", ep.overview));
        card.appendChild(body);
        card.addEventListener("click", () => { setEp(ep.episode_number); startPlayback(d, btn, seasonSel, episodeSel); });
        card.addEventListener("mouseenter", () => {
          // Same pointer-moved guard as the home rows: card.focus() scrolls the
          // card into view, which would otherwise leave a different card under
          // the stationary cursor and chain into a re-hover loop. Ignore hovers
          // that are just scroll-induced (no physical move / within the lock); a
          // real mouse move re-arms pointerMoved so hover-to-select still works.
          if (!pointerMoved || Date.now() < scrollLockUntil) return;
          pointerMoved = false;
          card.focus();
          setEp(ep.episode_number);
          scrollLockUntil = Date.now() + 120;
        });
        grid.appendChild(card);
      }
      // Default to the user's current episode if it's in this season, else E1.
      const cur = currentEp && currentEp.season === season ? currentEp.episode : null;
      setEp(cur && cur >= 1 ? cur : 1);
      focusCurrentCard();
    };

    const openSeasons = () => {
      seasonList.classList.add("open");
      seasonBtn.setAttribute("aria-expanded", "true");
      seasonItems.forEach((it) => it.classList.toggle("active", Number(it.dataset.season) === curSeason));
      seasonList.focus();
    };
    const closeSeasons = () => {
      seasonList.classList.remove("open");
      seasonBtn.setAttribute("aria-expanded", "false");
      seasonBtn.focus();
    };
    const pickSeason = (n) => {
      seasonList.classList.remove("open");
      seasonBtn.setAttribute("aria-expanded", "false");
      curSeason = n;
      seasonBtn.textContent = `Season ${n}`;
      renderEpisodes(n);
    };
    seasonBtn.addEventListener("keydown", (e) => {
      // Only the dropdown keys are handled here; Left/Right/Down/Up are handled
      // by the picker-row handler (the season selector is part of that row).
      if (["Enter", " ", "Spacebar", "Escape"].includes(e.key)) e.stopPropagation();
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        e.preventDefault();
        openSeasons();
      } else if (e.key === "Escape") {
        closeSeasons();
      }
    });
    seasonBtn.addEventListener("click", () => {
      if (seasonList.classList.contains("open")) closeSeasons();
      else openSeasons();
    });
    seasonList.addEventListener("keydown", (e) => {
      if (["ArrowDown", "ArrowUp", "Enter", " ", "Spacebar", "Escape"].includes(e.key)) e.stopPropagation();
      const active = seasonList.querySelector(".season-item.active");
      let idx = seasonItems.indexOf(active);
      if (e.key === "ArrowDown") { e.preventDefault(); idx = Math.min(idx + 1, seasonItems.length - 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); idx = Math.max(idx - 1, 0); }
      else if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") { e.preventDefault(); pickSeason(Number(seasonItems[idx].dataset.season)); return; }
      else if (e.key === "Escape") { e.preventDefault(); closeSeasons(); return; }
      else return;
      seasonItems.forEach((it, i) => it.classList.toggle("active", i === idx));
      seasonItems[idx].scrollIntoView({ block: "nearest" });
    });
    seasonItems.forEach((it) => it.addEventListener("click", () => pickSeason(Number(it.dataset.season))));

    // Default to the user's current season/episode if listed, else S1E1.
    curSeason = d.seasons.find((s) => s.season_number !== 0).season_number;
    curEp = 1;
    if (currentEp && d.seasons.some((x) => x.season_number === currentEp.season)) {
      curSeason = currentEp.season;
      curEp = (currentEp.episode && currentEp.episode >= 1) ? currentEp.episode : 1;
    }
    seasonBtn.textContent = `Season ${curSeason}`;
    renderEpisodes(curSeason);
  }

  const playLabel = playLabelFor(d, seasonSel, episodeSel);
  const btn = el("button", "btn", playLabel);
  btn.addEventListener("click", () => startPlayback(d, btn, seasonSel, episodeSel));

  // Mark-as-watched controls (manual). A movie marks the title; a show marks
  // the selected episode, or the whole season.
  const actionRow = el("div", "btn-row");
  let epBtn = null, markSeasonBtn = null, markShowBtn = null;
  if (d._type === "tv") {
    epBtn = el("button", "btn btn-ghost", "Mark episode watched");
    epBtn.addEventListener("click", () => markComplete(
      d, Number(seasonSel.value), Number(episodeSel.value), false, epBtn));
    markSeasonBtn = el("button", "btn btn-ghost", "Mark season watched");
    const cnt = d.seasons.find((s) => s.season_number === Number(seasonSel.value));
    markSeasonBtn.addEventListener("click", () => markComplete(
      d, Number(seasonSel.value), Number(episodeSel.value), true, markSeasonBtn,
      cnt ? cnt.episode_count || 1 : 1));
    markShowBtn = el("button", "btn btn-ghost", "Mark show watched");
    markShowBtn.addEventListener("click", async () => {
      markShowBtn.disabled = true;
      markShowBtn.textContent = "Saving...";
      try {
        for (const s of d.seasons) {
          if (s.season_number === 0) continue;
          await api("/watch/complete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              title_id: d.id, season: s.season_number,
              whole_season: true, episode_count: s.episode_count || 1,
            }),
          });
        }
        markShowBtn.textContent = "Marked \u2713";
      } catch (e) {
        markShowBtn.textContent = "Failed";
      }
      setTimeout(() => { markShowBtn.textContent = "Mark show watched"; markShowBtn.disabled = false; }, 2000);
    });
    actionRow.appendChild(epBtn);
    actionRow.appendChild(markSeasonBtn);
    actionRow.appendChild(markShowBtn);
  } else {
    const mBtn = el("button", "btn btn-ghost", "Mark as watched");
    mBtn.addEventListener("click", () => markComplete(d, null, null, false, mBtn));
    actionRow.appendChild(mBtn);
  }

  // On TV the Play + mark-watched controls sit in the picker row (next to the
  // season selector) so they share one keyboard flow with the episode grid:
  // Left/Right move across the row, Down enters the grid, Enter/Space activates.
  // Movies keep them in the info panel.
  if (d._type === "tv") {
    picker.appendChild(btn);
    picker.appendChild(actionRow);
    const rowBtns = [seasonBtn, btn, epBtn, markSeasonBtn, markShowBtn];
    pickerActiveEl = seasonBtn;
    rowBtns.forEach((b) => b.addEventListener("focus", () => { pickerActiveEl = b; }));
    picker.addEventListener("keydown", (e) => {
      if (["ArrowLeft", "ArrowRight", "ArrowDown", "ArrowUp", "Enter", " ", "Spacebar"].includes(e.key)) {
        e.stopPropagation();
      }
      const idx = rowBtns.indexOf(document.activeElement);
      if (e.key === "ArrowRight") { e.preventDefault(); const n = rowBtns[Math.min(idx + 1, rowBtns.length - 1)]; if (n) n.focus(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); const n = rowBtns[Math.max(idx - 1, 0)]; if (n) n.focus(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); const cards = [...grid.querySelectorAll(".ep-card")]; if (cards.length) cards[0].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); }
      else if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        const a = document.activeElement;
        // The season selector opens its dropdown via its own handler
        // (stopPropagation); the plain buttons activate here.
        if (a && a !== seasonBtn && a.tagName === "BUTTON") { e.preventDefault(); a.click(); }
      }
    });
  } else {
    info.appendChild(btn);
    info.appendChild(actionRow);
  }

  top.appendChild(info);
  app.appendChild(wrap);
  window.scrollTo(0, 0);
  // Kick off the background source prefetch once the user has settled on the
  // card (debounced), so Play can show a populated or near-instant list.
  schedulePrefetch(d, seasonSel, episodeSel);
  showHero();
  heroEl.classList.add("full");
  document.body.classList.add("full-hero");
  showBackdrop(d);
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

  // Watch / Download mode toggle, inside the source list. Default is Watch
  // (the usual play flow); flipping to DL makes a picked source download the
  // episode to the library instead of playing it.
  const handles = { mode: "play" };
  const epLabel = (d._type === "tv" && seasonSel && episodeSel)
    ? ` S${seasonSel.value} E${episodeSel.value}` : "";
  const modeToggle = el("div", "mode-toggle");
  const playSeg = el("button", "mode-seg active", "Play");
  const dlSeg = el("button", "mode-seg", "DL");
  const epSeg = el("span", "mode-ep", epLabel);
  modeToggle.appendChild(playSeg);
  modeToggle.appendChild(dlSeg);
  modeToggle.appendChild(epSeg);
  panel.appendChild(modeToggle);
  function setMode(m) {
    handles.mode = m;
    playSeg.classList.toggle("active", m === "play");
    dlSeg.classList.toggle("active", m === "download");
  }
  playSeg.addEventListener("click", () => setMode("play"));
  dlSeg.addEventListener("click", () => setMode("download"));

  const list = el("div", "src-list picker-loading");
  list.appendChild(el("div", "spinner"));
  panel.appendChild(list);

  const close = el("button", "btn btn-ghost", "Cancel");
  close.addEventListener("click", () => dismiss());
  panel.appendChild(close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) dismiss(); });

  function dismiss() {
    overlay.remove();
    if (btn) { btn.disabled = false; btn.textContent = playLabelFor(d, seasonSel, episodeSel); }
  }
  overlay.appendChild(panel);
  app.appendChild(overlay);
  return Object.assign(handles, { overlay, panel, sub, list, dismiss });
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
    row.addEventListener("click", async () => {
      const resolved = await pickSource(d, s, seasonSel, episodeSel, handles.mode);
      if (resolved) handles.dismiss();
    });
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

async function pickSource(d, source, seasonSel, episodeSel, mode) {
  const season = seasonSel ? Number(seasonSel.value) : null;
  const episode = episodeSel ? Number(episodeSel.value) : null;
  if (mode === "download") return pickSourceDownload(d, source, season, episode);
  try {
    const resolved = await api("/sources/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hash: source.hash }),
    });
    const label = (source.title || d.title || d.name) + (episode ? ` S${season}E${episode}` : "");
    const start = await savedStartSeconds(d.id, season, episode);
    await api("/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: resolved.download_url, file_path: label, start_seconds: start }),
    });
    navPush("nowplaying", { id: d.id, source, season, episode });
    return true;
  } catch (e) {
    alert("Playback failed: " + e.message);
    return false;
  }
}

// Download a single resolved source to the library. Dedupes: an episode that
// is already done is skipped ("Already in library"); one already in progress
// is skipped ("Already downloading"). Returns true when resolved (so the
// caller can close the picker), false on failure.
async function pickSourceDownload(d, source, season, episode) {
  try {
    const existing = await findDownload(d.id, episode);
    if (existing && existing.status === "done") { toast("Already in library"); return true; }
    if (existing && (existing.status === "queued" || existing.status === "downloading")) {
      toast("Already downloading");
      return true;
    }
    const resolved = await api("/sources/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hash: source.hash }),
    });
    const target = (resolved.files && resolved.files[0]) || {};
    await api("/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        hash: source.hash,
        file_url: resolved.download_url,
        title_id: d.id,
        episode: episode,
        size_bytes: target.size || null,
        filename: target.name || null,
      }),
    });
    toast("Download started");
    return true;
  } catch (e) {
    alert("Download failed: " + e.message);
    return false;
  }
}

// Look up an existing download row for (title, episode) to dedupe before
// starting a new one.
async function findDownload(titleId, episode) {
  const data = await api("/downloads").catch(() => null);
  const list = (data && data.downloads) || [];
  return list.find((r) => r.title_id === titleId &&
    (episode == null ? r.episode == null : r.episode === episode)) || null;
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
          ? { season: season || 1, episode: episode || 1, media_type: d._type }
          : { media_type: d._type };
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
  hideHero();
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
  hideHero();
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
    t.addEventListener("click", () => {
      history.replaceState({ view: "search", params: { query: q, category: val }, scroll: 0 }, "", "");
      renderSearchGrid(q, res, val);
    });
    tabs.appendChild(t);
  }
  app.appendChild(tabs);
  let items = [];
  if (category === "all" || category === "movie") items = items.concat((res.movie || []).map(m => ({...m, type: "movie"})));
  if (category === "all" || category === "tv") items = items.concat((res.tv || []).map(t => ({...t, type: "tv"})));
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
// Pointer-moved discriminator: a card only grabs focus on hover if the mouse
// physically moved there. When a row scrolls into view, a different card ends
// up under the stationary cursor; without this guard that card's mouseenter
// would re-focus and re-scroll, chaining into a jitter loop. A short scroll
// lock swallows any spurious hover fired in the same frame as a programmatic
// scroll (touchpad drift, etc.).
let pointerMoved = false;
let scrollLockUntil = 0;

function focusables() {
  const overlay = document.querySelector(".overlay");
  if (overlay) return [...overlay.querySelectorAll(".src-row, .btn")];
  return [...app.querySelectorAll(".card, .btn, .src-row, select")];
}

function buildFocusMap() {
  // Preserve the currently focused element across rebuilds. The MutationObserver
  // fires on every DOM change (lazy image loads, progress bars, the Continue
  // Watching row updating) - each of those used to yank focus back to the first
  // card mid-navigation, so Up "went nowhere".
  const prev = currentFocus();
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
  // Category tabs lead the map so Up from the top card row reaches them, then
  // Down enters the grid. (The search bar stays reachable via "/" only.)
  const top = [];
  const tabs = [...app.querySelectorAll(".cat-tab")];
  if (tabs.length) top.push(tabs);
  // Drop empty rows so Up from the first populated row reaches the tabs (an
  // empty "Continue Watching" row used to swallow the keypress).
  focusRows = top.concat(rows).concat([...byRow.values()]).filter((r) => r.length > 0);
  // Restore focus to the previous element if it's still in the map; else default.
  let restore = null;
  if (prev) {
    for (let r = 0; r < focusRows.length; r++) {
      const c = focusRows[r].indexOf(prev);
      if (c !== -1) { restore = [r, c]; break; }
    }
  }
  focusRow = 0;
  focusCol = 0;
  if (restore) setFocus(restore[0], restore[1]);
  else setFocus(0, 0); // default focus: tabs at the top
}

function setFocus(row, col) {
  if (!focusRows.length) return null;
  focusRow = Math.max(0, Math.min(row, focusRows.length - 1));
  const r = focusRows[focusRow] || [];
  focusCol = Math.max(0, Math.min(col, r.length - 1));
  document.querySelectorAll(".focused").forEach((n) => n.classList.remove("focused"));
  const el = r[focusCol];
  if (el) {
    el.classList.add("focused");
    // Any focus change (keyboard or hover) scrolls the page. Arm a short scroll
    // lock and clear the pointer-moved flag so the next hover only re-focuses if
    // the mouse genuinely moves again (breaks the hover/scroll feedback loop).
    scrollLockUntil = Date.now() + 120;
    pointerMoved = false;
    if (el.tagName === "INPUT") el.focus(); // real focus so the search bar accepts typing
    el.scrollIntoView({ block: "nearest", inline: "nearest" });
    // Scroll so the row's header sits just below the hero banner. This keeps
    // the category name in view as you navigate through rows, and prevents the
    // hero from clipping the top of the cards.
    const row = el.closest(".row");
    const epGrid = el.closest(".ep-grid");
    const heroH = (heroEl && heroEl.style.display !== "none" && !heroEl.classList.contains("full")) ? heroEl.offsetHeight : 0;
    if (heroH || epGrid) {
      if (row) {
        const header = row.querySelector("h2");
        if (header) {
          window.scrollBy(0, header.getBoundingClientRect().top - heroH);
        }
      } else if (epGrid) {
        // Episode grid: scroll so the picker row sits just below the topbar.
        // Use absolute scrollTo (not relative scrollBy) so the result is
        // exact regardless of where scrollIntoView left the page.
        const picker = epGrid.closest(".detail")?.querySelector(".ep-picker");
        if (picker) {
          const topbar = document.querySelector(".topbar");
          const top = (topbar ? topbar.offsetHeight : 0) + 40;
          const pickerPageY = picker.getBoundingClientRect().top + window.scrollY;
          window.scrollTo(0, pickerPageY - top);
        }
      } else {
        // No row (tabs, etc.): scroll so the element sits just below the hero.
        window.scrollBy(0, el.getBoundingClientRect().top - heroH - 8);
      }
    }
    // If the card's bottom is still cut off at the page edge, scroll down.
    const rect = el.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) {
      window.scrollBy(0, rect.bottom - window.innerHeight);
    }
    if (el._item) showBackdrop(el._item); // update the hero backdrop to match focus
  }
  return el;
}

function currentFocus() { return (focusRows[focusRow] || [])[focusCol] || null; }

// Move focus to a card when the mouse hovers it, so keyboard nav picks up
// from there. Looks the card up in the focus map and calls setFocus.
function focusFromHover(cardEl) {
  for (let r = 0; r < focusRows.length; r++) {
    const c = focusRows[r].indexOf(cardEl);
    if (c !== -1) { setFocus(r, c); return; }
  }
}

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
  stopPlayPoll();
  hideHero();
  const app = document.getElementById("app");
  app.innerHTML =
    '<div class="settings"><h1>Settings</h1>' +
    '<section class="trakt-panel" id="trakt-panel"><h2>Trakt</h2>' +
    '<p class="muted">Loading&hellip;</p></section>' +
    '<section class="trakt-panel" id="sync-panel"><h2>Sync</h2>' +
    '<p class="muted">Loading&hellip;</p></section>' +
    '<section class="trakt-panel" id="tmdb-panel"><h2>TMDB</h2>' +
    '<p class="muted">Loading&hellip;</p></section>' +
    '<section class="trakt-panel" id="torbox-panel"><h2>Torbox</h2>' +
    '<p class="muted">Loading&hellip;</p></section>' +
    '<section class="trakt-panel" id="cache-panel"><h2>Cache</h2>' +
    '<p class="muted">Loading&hellip;</p></section>' +
    '<section class="trakt-panel" id="updates-panel"><h2>Updates</h2>' +
    '<p class="muted">Loading&hellip;</p></section></div>';
  await Promise.all([loadTraktStatus(), loadSyncStatus(), loadTmdbStatus(), loadTorboxStatus(), loadCacheStatus(), loadUpdatesStatus()]);
  refreshSvcStatus();
}

/* Settings: app updates (Tauri updater via GitHub Releases). */
async function loadUpdatesStatus() {
  const panel = document.getElementById("updates-panel");
  if (!panel) return;
  const hasTauri = !!(window.__TAURI_INTERNALS__ || window.__TAURI__?.core);
  if (!hasTauri) {
    panel.innerHTML = '<h2>Updates</h2><p class="muted">Not available in this build.</p>';
    return;
  }
  let current = "";
  try { current = await tauriInvoke("app_get_version"); } catch (e) { current = ""; }
  panel.innerHTML =
    '<h2>Updates</h2>' +
    '<p class="muted">Current version: ' + (current ? esc(current) : "unknown") + '</p>' +
    '<button class="btn" id="upd-check">Check for updates</button>' +
    '<p class="muted" id="upd-status"></p>' +
    '<button class="btn" id="upd-install" style="display:none">Install update &amp; restart</button>';
  panel.querySelector("#upd-check").addEventListener("click", () => checkForUpdate(panel));
}

async function checkForUpdate(panel) {
  const status = panel.querySelector("#upd-status");
  const installBtn = panel.querySelector("#upd-install");
  status.textContent = "Checking for updates\u2026";
  installBtn.style.display = "none";
  try {
    const update = await tauriInvoke("check_for_update");
    if (!update) {
      status.textContent = "You're up to date.";
      return;
    }
    status.textContent = "Update available: v" + update.version;
    installBtn.style.display = "";
    installBtn.onclick = async () => {
      installBtn.disabled = true;
      status.textContent = "Downloading and installing\u2026";
      try {
        await tauriInvoke("install_update");
        status.textContent = "Installed \u2014 restarting\u2026";
      } catch (e) {
        status.textContent = "Update failed: " + e;
        installBtn.disabled = false;
      }
    };
  } catch (e) {
    status.textContent = "Check failed: " + e;
  }
}

/* Settings: poster cache size readout + clear (GET /config, POST /config/clear-cache). */
function fmtBytes(n) {
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return n.toFixed(n < 10 ? 1 : 0) + " " + units[i];
}

async function loadCacheStatus() {
  const panel = document.getElementById("cache-panel");
  if (!panel) return;
  let st;
  try { st = await api("/config"); }
  catch (e) { panel.innerHTML = '<h2>Cache</h2><p class="muted">Status unavailable.</p>'; return; }
  renderCachePanel(st);
}

function renderCachePanel(st) {
  const panel = document.getElementById("cache-panel");
  if (!panel) return;
  panel.innerHTML =
    '<h2>Cache</h2>' +
    '<p>Poster cache: <strong>' + fmtBytes(st.cache_bytes) + '</strong></p>' +
    '<p class="muted">' + esc(st.poster_dir) + '</p>' +
    '<button class="btn" id="cache-clear">Clear cache</button>' +
    '<p class="trakt-msg muted" id="cache-msg"></p>';
  const b = document.getElementById("cache-clear");
  if (b) b.addEventListener("click", clearCache);
}

async function clearCache() {
  if (!confirm("Clear the poster cache? Posters will re-download when you view titles again.")) return;
  const msg = document.getElementById("cache-msg");
  if (msg) msg.textContent = "Clearing\u2026";
  try {
    await api("/config/clear-cache", { method: "POST" });
    const st = await api("/config");
    renderCachePanel(st);
    const m = document.getElementById("cache-msg");
    if (m) m.textContent = "Cache cleared.";
  } catch (e) {
    const m = document.getElementById("cache-msg");
    if (m) m.textContent = "Clear failed: " + e.message;
  }
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
      '<button class="btn" id="trakt-sync">Import from Trakt</button>' +
      '<p class="trakt-msg muted" id="trakt-msg"></p>';
    const b = document.getElementById("trakt-sync");
    if (b) b.addEventListener("click", doTraktSync);
  } else {
    panel.innerHTML = '<h2>Trakt</h2>' +
      '<p class="muted">Not connected. Sign in to import your Trakt watch history one time.</p>' +
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
    '<button class="btn" id="trakt-open-link">Open ' + esc(res.verification_url) + '</button>' +
    '<p class="trakt-msg muted">Waiting for approval&hellip;</p>';
  const ob = document.getElementById("trakt-open-link");
  if (ob) ob.addEventListener("click", () => openExternal(res.verification_url));
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
      setTimeout(() => { loadTraktStatus(); refreshSvcStatus(); }, 800);
    } else if (st.status === "expired") {
      clearInterval(traktPollTimer); traktPollTimer = null;
      if (msg) msg.textContent = "The code expired. Sign in again.";
    }
  }, 5000);
}

async function doTraktSync() {
  const msg = document.getElementById("trakt-msg");
  if (msg) msg.textContent = "Importing&hellip;";
  let res;
  try { res = await api("/trakt/import", { method: "POST" }); }
  catch (e) { if (msg) msg.textContent = "Import failed: " + e.message; return; }
  if (msg) msg.textContent = res.error
    ? "Import: " + res.error
    : "Imported " + res.imported + " items.";
  setTimeout(loadTraktStatus, 600);
}

/* Settings: sync panel (device id, backend, drive creds, sync now) */
async function loadSyncStatus() {
  const panel = document.getElementById("sync-panel");
  if (!panel) return;
  let s, d;
  try {
    [s, d] = await Promise.all([api("/settings"), api("/drive/status")]);
  } catch (e) {
    panel.innerHTML = '<h2>Sync</h2><p class="muted">Status unavailable.</p>';
    return;
  }
  const syncInfo = s.sync || {};
  const lastSync = syncInfo.last_sync || null;
  const pending = syncInfo.pending_count != null ? syncInfo.pending_count : 0;

  let html = '<h2>Sync</h2>';

  // 1. Device ID
  html +=
    '<p><label>Device ID </label>' +
    '<input type="text" class="key-input" id="sync-device-id" value="' + esc(s.device_id || "") + '" autocomplete="off" spellcheck="false">' +
    ' <button class="btn" id="sync-device-save">Save</button></p>' +
    '<p class="trakt-msg muted" id="sync-device-msg"></p>';

  // 2. Backend
  const backend = s.storage_backend || "local";
  html +=
    '<p><label>Backend </label>' +
    '<select id="sync-backend">' +
    '<option value="local"' + (backend === "local" ? " selected" : "") + '>local</option>' +
    '<option value="drive"' + (backend === "drive" ? " selected" : "") + '>drive</option>' +
    '</select> ' +
    '<button class="btn" id="sync-backend-save">Save</button></p>' +
    '<p class="trakt-msg muted" id="sync-backend-msg"></p>';

  // 3. Drive credentials
  html +=
    '<p>Drive credentials</p>' +
    '<input type="text" class="key-input" id="sync-drive-client-id" placeholder="Drive client ID" value="' + esc(s.drive_client_id || "") + '" autocomplete="off" spellcheck="false">' +
    '<input type="password" class="key-input" id="sync-drive-client-secret" placeholder="leave blank if already set" autocomplete="off" spellcheck="false">' +
    '<button class="btn" id="sync-drive-creds-save">Save</button>' +
    '<p class="trakt-msg muted" id="sync-drive-msg"></p>';

  // 4. Drive status
  if (s.drive_connected) {
    let driveLine = "Drive: connected";
    if (d && d.expires_at) {
      driveLine += ' &mdash; token expires ' + esc(new Date(d.expires_at * 1000).toLocaleString());
    }
    html += '<p class="muted">' + driveLine + '</p>';
  } else if (d && d.pending) {
    html += '<p class="muted">Drive: waiting for browser authorization&hellip;</p>';
  } else {
    html += '<button class="btn" id="sync-drive-connect">Connect Drive</button>' +
      '<p class="trakt-msg muted" id="sync-connect-msg"></p>';
  }

  // 5. Sync now
  html +=
    '<button class="btn" id="sync-now">Sync now</button>' +
    '<p class="trakt-msg muted" id="sync-now-msg"></p>';

  // 6. Last sync
  if (lastSync) {
    let lastLine = "Last sync: " + esc(lastSync.last_sync_at || "");
    if (lastSync.last_push_at) lastLine += " &mdash; pushed: " + esc(lastSync.last_push_at);
    if (lastSync.last_error) lastLine += " &mdash; error: " + esc(lastSync.last_error);
    html += '<p class="muted">' + lastLine + '</p>';
  }
  html += '<p class="muted">pending: ' + esc(String(pending)) + '</p>';

  panel.innerHTML = html;

  // Device ID save
  const devSave = document.getElementById("sync-device-save");
  if (devSave) devSave.addEventListener("click", async () => {
    const input = document.getElementById("sync-device-id");
    const msg = document.getElementById("sync-device-msg");
    const val = input ? input.value.trim() : "";
    if (!val) { if (msg) msg.textContent = "Enter a device ID first."; return; }
    if (msg) msg.textContent = "Saving\u2026";
    try {
      await api("/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_id: val }) });
      loadSyncStatus();
    } catch (e) { if (msg) msg.textContent = "Save failed: " + e.message; }
  });

  // Backend save
  const beSave = document.getElementById("sync-backend-save");
  if (beSave) beSave.addEventListener("click", async () => {
    const sel = document.getElementById("sync-backend");
    const msg = document.getElementById("sync-backend-msg");
    const val = sel ? sel.value : "local";
    if (msg) msg.textContent = "Saving\u2026";
    try {
      await api("/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ storage_backend: val }) });
      loadSyncStatus();
    } catch (e) { if (msg) msg.textContent = "Save failed: " + e.message; }
  });

  // Drive credentials save
  const credsSave = document.getElementById("sync-drive-creds-save");
  if (credsSave) credsSave.addEventListener("click", async () => {
    const idInput = document.getElementById("sync-drive-client-id");
    const secInput = document.getElementById("sync-drive-client-secret");
    const msg = document.getElementById("sync-drive-msg");
    const cid = idInput ? idInput.value.trim() : "";
    const csec = secInput ? secInput.value.trim() : "";
    if (!cid && !csec) { if (msg) msg.textContent = "Enter at least one field."; return; }
    if (msg) msg.textContent = "Saving\u2026";
    const body = {};
    if (cid) body.drive_client_id = cid;
    if (csec) body.drive_client_secret = csec;
    try {
      await api("/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      loadSyncStatus();
    } catch (e) { if (msg) msg.textContent = "Save failed: " + e.message; }
  });

  // Drive connect
  const connBtn = document.getElementById("sync-drive-connect");
  if (connBtn) connBtn.addEventListener("click", async () => {
    const msg = document.getElementById("sync-connect-msg");
    if (msg) msg.textContent = "Starting\u2026";
    try {
      const res = await api("/drive/connect", { method: "POST" });
      openExternal(res.authorization_url);
      if (msg) msg.textContent = "Waiting for browser authorization\u2026";
      // Poll /drive/status every ~2s for up to ~120s
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        if (attempts > 60) {
          clearInterval(poll);
          const p = document.getElementById("sync-panel");
          if (p) {
            p.innerHTML += '<p class="muted">Not connected yet \u2014 finish the browser flow and press Refresh</p>' +
              '<button class="btn" id="sync-refresh">Refresh</button>';
            const rb = document.getElementById("sync-refresh");
            if (rb) rb.addEventListener("click", () => loadSyncStatus());
          }
          return;
        }
        try {
          const st = await api("/drive/status");
          if (st.connected) {
            clearInterval(poll);
            loadSyncStatus();
          }
        } catch (_) { /* keep polling */ }
      }, 2000);
    } catch (e) {
      if (msg) msg.textContent = "Connect failed: " + e.message;
    }
  });

  // Sync now
  const syncBtn = document.getElementById("sync-now");
  if (syncBtn) syncBtn.addEventListener("click", async () => {
    const msg = document.getElementById("sync-now-msg");
    syncBtn.disabled = true;
    if (msg) msg.textContent = "Syncing\u2026";
    try {
      const res = await api("/sync", { method: "POST" });
      if (res.error) {
        if (msg) msg.textContent = res.error;
      } else {
        if (msg) msg.textContent = "Synced: " + (res.pushed || 0) + " pushed, " + (res.pulled || 0) + " pulled";
      }
    } catch (e) {
      if (msg) msg.textContent = "Sync failed: " + e.message;
    }
    syncBtn.disabled = false;
  });
}

/* Settings: TMDB API token (stored in-app, validated on save) */
async function loadTmdbStatus() {
  const panel = document.getElementById("tmdb-panel");
  if (!panel) return;
  let st;
  try { st = await api("/tmdb/status"); }
  catch (e) { panel.innerHTML = '<h2>TMDB</h2><p class="muted">Status unavailable.</p>'; return; }
  if (st.configured) {
    panel.innerHTML = '<h2>TMDB</h2>' +
      '<p>Connected. Metadata (posters, stills, titles) is enabled.</p>' +
      '<button class="btn" id="tmdb-remove">Remove token</button>' +
      '<p class="trakt-msg muted" id="tmdb-msg"></p>';
    const b = document.getElementById("tmdb-remove");
    if (b) b.addEventListener("click", removeTmdbKey);
  } else {
    panel.innerHTML = '<h2>TMDB</h2>' +
      '<p class="muted">Not connected. Add your TMDB v4 token to enable metadata lookups.</p>' +
      '<input type="password" class="key-input" id="tmdb-key" placeholder="TMDB v4 read token" autocomplete="off" spellcheck="false">' +
      '<button class="btn" id="tmdb-save">Save token</button> ' +
      '<button class="btn btn-ghost" id="tmdb-getkey">Get a token</button>' +
      '<p class="trakt-msg muted" id="tmdb-msg"></p>';
    const b = document.getElementById("tmdb-save");
    if (b) b.addEventListener("click", saveTmdbKey);
    const g = document.getElementById("tmdb-getkey");
    if (g) g.addEventListener("click", () => openExternal("https://www.themoviedb.org/settings/api"));
  }
}

async function saveTmdbKey() {
  const input = document.getElementById("tmdb-key");
  const msg = document.getElementById("tmdb-msg");
  const key = input ? input.value.trim() : "";
  if (!key) { if (msg) msg.textContent = "Enter a token first."; return; }
  if (msg) msg.textContent = "Saving&hellip;";
  try {
    await api("/tmdb/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    loadTmdbStatus();
    refreshSvcStatus();
  } catch (e) {
    if (msg) msg.textContent = "Save failed: " + e.message;
  }
}

async function removeTmdbKey() {
  try {
    await api("/tmdb/auth", { method: "DELETE" });
    loadTmdbStatus();
    refreshSvcStatus();
  } catch (e) { /* ignore */ }
}

/* Settings: Torbox API key (stored in-app, validated on save) */
async function loadTorboxStatus() {
  const panel = document.getElementById("torbox-panel");
  if (!panel) return;
  let st;
  try { st = await api("/sources/status"); }
  catch (e) { panel.innerHTML = '<h2>Torbox</h2><p class="muted">Status unavailable.</p>'; return; }
  if (st.configured) {
    panel.innerHTML = '<h2>Torbox</h2>' +
      '<p>Connected. Debrid downloads are enabled.</p>' +
      '<button class="btn" id="torbox-remove">Remove key</button>' +
      '<p class="trakt-msg muted" id="torbox-msg"></p>';
    const b = document.getElementById("torbox-remove");
    if (b) b.addEventListener("click", removeTorboxKey);
  } else {
    panel.innerHTML = '<h2>Torbox</h2>' +
      '<p class="muted">Not connected. Add your Torbox API key to enable debrid downloads.</p>' +
      '<input type="password" class="key-input" id="torbox-key" placeholder="Torbox API key" autocomplete="off" spellcheck="false">' +
      '<button class="btn" id="torbox-save">Save key</button> ' +
      '<button class="btn btn-ghost" id="torbox-getkey">Get a key</button>' +
      '<p class="trakt-msg muted" id="torbox-msg"></p>';
    const b = document.getElementById("torbox-save");
    if (b) b.addEventListener("click", saveTorboxKey);
    const g = document.getElementById("torbox-getkey");
    if (g) g.addEventListener("click", () => openExternal("https://torbox.app"));
  }
}

async function saveTorboxKey() {
  const input = document.getElementById("torbox-key");
  const msg = document.getElementById("torbox-msg");
  const key = input ? input.value.trim() : "";
  if (!key) { if (msg) msg.textContent = "Enter a key first."; return; }
  if (msg) msg.textContent = "Saving&hellip;";
  try {
    await api("/sources/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    loadTorboxStatus();
    refreshSvcStatus();
  } catch (e) {
    if (msg) msg.textContent = "Save failed: " + e.message;
  }
}

async function removeTorboxKey() {
  try {
    await api("/sources/auth", { method: "DELETE" });
    loadTorboxStatus();
    refreshSvcStatus();
  } catch (e) { /* ignore */ }
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
    case "Backspace": {
      // Back acts as "back" (close an overlay, else navigate back) - but never
      // while typing in a field, where it should delete a character.
      const t = document.activeElement;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if (!typing) { e.preventDefault(); goBack(); }
      break;
    }
    case "Escape": {
      // Escape closes open menus (season dropdown, source picker) rather than
      // navigating back - that's now Backspace's job.
      e.preventDefault();
      const openList = document.querySelector(".season-list.open");
      if (openList) {
        openList.classList.remove("open");
        const a = openList.querySelector(".season-item.active");
        if (a) a.classList.remove("active");
      } else {
        const overlay = document.querySelector(".overlay");
        if (overlay) {
          overlay.remove();
          const b = document.querySelector(".detail .btn");
          if (b) { b.disabled = false; b.textContent = "Play"; }
          buildFocusMap();
        }
      }
      break;
    }
  }
});

// Track physical pointer movement so hover-to-focus can be told apart from a
// card that merely scrolled under a stationary cursor (see the mouseenter
// guard in the card builder). A real move re-arms pointerMoved.
document.addEventListener("mousemove", () => { pointerMoved = true; });

// Clicking outside an open season dropdown closes it (dismiss the menu without
// re-clicking the season button).
document.addEventListener("click", (e) => {
  const openList = document.querySelector(".season-list.open");
  if (openList) {
    const picker = openList.closest(".ep-picker");
    if (picker && !picker.contains(e.target)) {
      openList.classList.remove("open");
      const a = openList.querySelector(".season-item.active");
      if (a) a.classList.remove("active");
    }
  }
});

// Rebuild the focus map whenever the app DOM changes (after each render).
new MutationObserver(() => {
  buildFocusMap();
}).observe(app, { childList: true, subtree: true });

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
  startBadgePoll();
  checkForUpdateOnStartup();
}

boot();
