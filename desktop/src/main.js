// home-media frontend - vanilla JS, no bundler.
// Talks to the local backend on 127.0.0.1:8790.

const API = "http://127.0.0.1:8790";
const app = document.getElementById("app");
const searchInput = document.getElementById("search");

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

  const btn = el("button", "btn", "Play");
  btn.addEventListener("click", () => startPlayback(d, btn));
  info.appendChild(btn);

  wrap.appendChild(info);
  app.appendChild(wrap);
  window.scrollTo(0, 0);
}

async function startPlayback(d, btn) {
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "Loading sources...";
  try {
    const srcs = await api(`/sources/${d.id}`, { method: "POST" });
    if (!srcs.sources || !srcs.sources.length) {
      alert("No sources found for this title.");
      return;
    }
    const first = srcs.sources[0];
    const resolved = await api("/sources/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hash: first.hash }),
    });
    await api("/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: resolved.download_url, file_path: first.title || d.title || d.name }),
    });
    btn.textContent = "Now playing";
  } catch (e) {
    alert("Playback failed: " + e.message);
    btn.textContent = prev;
  }
}

async function renderSearch(q) {
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

renderHome();
