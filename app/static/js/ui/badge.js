// Line badge: trams square, buses rounded - the convention on swa's network map.
import { state } from "../core/store.js";
import { escapeHtml } from "../core/format.js";

export function lineBadge(lineId, fallback = "") {
  const l = state.lines.get(lineId);
  if (!l) return `<span class="lb" style="background:#5a6670">${escapeHtml(fallback || lineId)}</span>`;
  return `<span class="lb${l.mode === "bus" ? " bus" : ""}" style="background:${l.color};color:${l.text_color}" title="${escapeHtml(l.name)}">${escapeHtml(l.short_name)}</span>`;
}
