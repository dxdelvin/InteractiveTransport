// Thin REST client for the FastAPI backend.
const BASE = "/api/v1";

async function request(path, { method = "GET", body, signal } = {}) {
  const res = await fetch(BASE + path, {
    method,
    signal,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not json */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const api = {
  health: () => request("/health"),
  providers: () => request("/providers"),
  network: (p) => request(`/${p}/network`),
  scenery: (p) => request(`/${p}/scenery`),
  vehicles: (p) => request(`/${p}/vehicles`),
  vehicle: (p, id, signal) => request(`/${p}/vehicles/${encodeURIComponent(id)}`, { signal }),
  station: (p, id, signal, limit = 12) => request(`/${p}/stations/${encodeURIComponent(id)}?limit=${limit}`, { signal }),
  alerts: (p) => request(`/${p}/alerts`),
  search: (p, q, signal) => request(`/${p}/stations/search?q=${encodeURIComponent(q)}`, { signal }),
  lineStatus: (p) => request(`/${p}/lines/status`),
  plan: (p, body) => request(`/${p}/plan`, { method: "POST", body }),
  ask: (p, body) => request(`/${p}/assistant`, { method: "POST", body }),
};
