// AI trip assistant chat.
import { api } from "../core/api.js";
import { state, emit, on } from "../core/store.js";
import { escapeHtml } from "../core/format.js";

const SUGGESTIONS = {
  augsburg: ["How do I get from Hauptbahnhof to the Universität?", "When is the next tram at Königsplatz?", "Are any lines delayed?", "From Firnhaberau to Göggingen with the fewest changes"],
  mbta: ["How do I get from Harvard to Park Street?", "Next train at South Station", "Are any lines delayed?", "From Alewife to Airport with the fewest changes"],
};

export function initAssistant() {
  const chat = document.getElementById("chat");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-text");
  const sugg = document.getElementById("suggest");
  const badge = document.getElementById("ai-engine");
  let history = [];
  let busy = false;

  const add = (role, text, extra = "") => {
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.innerHTML = `<p>${escapeHtml(text)}</p>${extra}`;
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
    return el;
  };

  const renderSuggestions = () => {
    const list = SUGGESTIONS[state.provider?.id] || [];
    sugg.innerHTML = list.map((s) => `<button type="button">${escapeHtml(s)}</button>`).join("");
    sugg.querySelectorAll("button").forEach((b) => (b.onclick = () => send(b.textContent)));
  };

  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    busy = true;
    input.value = "";
    add("user", text);
    const typing = add("bot thinking", "Checking the network…");
    try {
      const sel = state.selection;
      const res = await api.ask(state.provider.id, {
        message: text,
        history: history.slice(-12),
        context: {
          selected_vehicle_id: sel?.kind === "vehicle" ? sel.id : null,
          selected_station_id: sel?.kind === "station" ? sel.id : null,
          user_lat: state.userLocation?.lat ?? null,
          user_lon: state.userLocation?.lon ?? null,
        },
      });
      typing.remove();
      const extra = res.journeys.length ? `<button class="show" data-show>Open ${res.journeys.length > 1 ? `all ${res.journeys.length} routes` : "the route"} in Route</button>` : "";
      const el = add("bot", res.reply, extra);
      el.querySelector("[data-show]")?.addEventListener("click", () => { emit("tab", "plan"); emit("journeys", res.journeys); });
      history.push({ role: "user", content: text }, { role: "assistant", content: res.reply });
      setEngine(res.engine);
      if (res.journeys.length) emit("journeys", res.journeys);
      if (res.highlights.vehicles?.length) emit("select", { kind: "vehicle", id: res.highlights.vehicles[0] });
      else if (!res.journeys.length && res.highlights.stations?.length) emit("select", { kind: "station", id: res.highlights.stations[0] });
    } catch (err) {
      typing.remove();
      add("bot", `That didn't go through (${err.message}). Try again in a moment.`);
    } finally {
      busy = false;
    }
  }

  function setEngine(engine) {
    state.aiEngine = engine;
    badge.textContent = engine === "openai" ? "with OpenAI" : "offline";
    badge.title = engine === "openai" ? "Answers come from OpenAI using live network tools" : "Built-in parser. Set OPENAI_API_KEY for free-form questions.";
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  on("aiEngine", setEngine);
  on("providerChanged", () => {
    history = [];
    chat.innerHTML = `<div class="msg bot"><p>Ask how to get somewhere in ${escapeHtml(state.provider.city)}, when the next tram or bus leaves a stop, or whether a line is delayed.</p></div>`;
    renderSuggestions();
  });
}
