// Probes the Python backend's /health endpoint on load and every 5s.
// This is a minimal shell - the real UI (browsing, playback, etc.) comes in later phases.
// The backend is a separate service at 127.0.0.1:8790; showing its status here
// gives immediate feedback that the native shell and backend are connected.

const statusEl = document.getElementById("backend-status");
const HEALTH_URL = "http://127.0.0.1:8790/health";

async function probe() {
  try {
    const res = await fetch(HEALTH_URL);
    statusEl.textContent = res.ok ? "backend: ok" : "backend: down";
  } catch {
    statusEl.textContent = "backend: down";
  }
}

probe();
setInterval(probe, 5000);
