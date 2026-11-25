"""AI trip assistant.

With OPENAI_API_KEY set, an OpenAI model answers using function calls into the real
planner and live data (it never invents times). Without a key, a small rule-based parser handles the
common requests ("from A to B", "next train at X", "line status") so the feature still works.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import openai

from app.core.config import Settings
from app.schemas.assistant import ChatRequest, ChatResponse, Highlights
from app.schemas.planner import Journey, PlaceRef, PlanRequest
from app.services.providers.base import TransitProvider
from app.services.routing import Planner, PlanningError

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_stations",
        "description": "Find stations by (partial or misspelled) name. Returns ids, names and the lines serving each.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Station name or part of it"}},
            "required": ["query"],
        },
    },
    {
        "name": "plan_route",
        "description": (
            "Plan journeys between two places using live vehicle positions for wait times. "
            "origin/destination are station names or ids, or the literal 'my_location' for the user's position. "
            "Returns up to 3 options with lines, transfers, waits and total minutes. The options are also drawn on the user's map."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "preference": {"type": "string", "enum": ["fastest", "fewest_transfers", "least_walking"]},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "station_departures",
        "description": "Next live arrivals at a station: line, destination and minutes away.",
        "input_schema": {"type": "object", "properties": {"station": {"type": "string"}}, "required": ["station"]},
    },
    {
        "name": "network_status",
        "description": "Service health for every line: active vehicles, average delay, effective headway.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "vehicle_info",
        "description": "Where a specific vehicle is going and its upcoming stops with ETAs.",
        "input_schema": {"type": "object", "properties": {"vehicle_id": {"type": "string"}}, "required": ["vehicle_id"]},
    },
    {
        "name": "list_lines",
        "description": "All lines with their stations in order.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _mins(seconds: float) -> int:
    return max(0, round(seconds / 60))


def _when(minutes: int) -> str:
    return "now" if minutes <= 0 else f"in {minutes} min"


def journey_brief(j: Journey) -> dict:
    return {
        "minutes": _mins(j.duration_s),
        "transfers": j.transfers,
        "walking_m": j.walking_m,
        "tags": j.tags,
        "legs": [
            {
                "kind": l.kind,
                "line": l.line_name,
                "from": l.from_name,
                "to": l.to_name,
                "towards": l.headsign,
                "wait_min": _mins(l.wait_s) if l.kind == "ride" else None,
                "ride_min": _mins(l.duration_s),
                "stops": len(l.stops) - 1 if l.stops else None,
                "walk_m": l.distance_m if l.kind == "walk" else None,
            }
            for l in j.legs
        ],
    }


class ToolBox:
    """Executes assistant tools against a provider and remembers what to highlight."""

    def __init__(self, provider: TransitProvider, req: ChatRequest):
        self.p = provider
        self.req = req
        self.journeys: list[Journey] = []
        self.hl = Highlights()
        self.used: list[str] = []

    def _place(self, text: str) -> PlaceRef:
        ctx = self.req.context
        if text.strip().lower() in {"my_location", "my location", "here", "me"}:
            if ctx.user_lat is not None and ctx.user_lon is not None:
                return PlaceRef(lat=ctx.user_lat, lon=ctx.user_lon, label="Your location")
            if ctx.selected_station_id:
                return PlaceRef(station_id=ctx.selected_station_id)
            raise PlanningError("The user's location is unknown - ask them for a starting station")
        return PlaceRef(query=text)

    def _station(self, text: str):
        st = self.p.resolve_station(text)
        if st is None:
            raise PlanningError(f"No station matches '{text}'")
        if st.id not in self.hl.stations:
            self.hl.stations.append(st.id)
        return st

    def run(self, name: str, args: dict) -> Any:
        self.used.append(name)
        idx = self.p._idx()
        if name == "search_stations":
            return [{"id": s.id, "name": s.name, "lines": [idx.lines[l].name for l in s.lines]} for s in self.p.search_stations(str(args.get("query", "")))]
        if name == "plan_route":
            pref = args.get("preference") or "fastest"
            if pref not in ("fastest", "fewest_transfers", "least_walking"):
                pref = "fastest"
            plan = Planner(self.p).plan(
                PlanRequest(origin=self._place(str(args["origin"])), destination=self._place(str(args["destination"])), preference=pref)
            )
            self.journeys = plan.journeys
            for j in plan.journeys[:1]:
                for l in j.legs:
                    if l.line_id and l.line_id not in self.hl.lines:
                        self.hl.lines.append(l.line_id)
            return {"from": plan.origin_label, "to": plan.destination_label, "options": [journey_brief(j) for j in plan.journeys]}
        if name == "station_departures":
            st = self._station(str(args["station"]))
            board = self.p.station_arrivals(st.id, limit=10)
            return {
                "station": st.name,
                "departures": [{"line": a.line_name, "towards": a.headsign, "minutes": _mins(a.eta_s), "vehicle_id": a.vehicle_id} for a in board.arrivals],
            }
        if name == "network_status":
            return [s.model_dump() for s in (self.p.line_status(l) for l in idx.lines)]
        if name == "vehicle_info":
            vid = str(args["vehicle_id"])
            if vid not in self.p.vehicles:
                raise PlanningError(f"Vehicle {vid} is not in service right now")
            d = self.p.vehicle_detail(vid)
            self.hl.vehicles.append(vid)
            return {
                "vehicle": d.vehicle.label,
                "line": d.line_name,
                "towards": d.destination_name,
                "speed_kmh": d.vehicle.speed_kmh,
                "delay_min": _mins(d.vehicle.delay_s),
                "occupancy": d.vehicle.occupancy,
                "next_stops": [{"station": u.name, "minutes": _mins(u.eta_s)} for u in d.upcoming[:6]],
            }
        if name == "list_lines":
            return [{"id": l.id, "name": l.name, "mode": l.mode, "loop": l.loop, "stations": [idx.stations[s].name for s in l.stations]} for l in idx.network.lines]
        raise PlanningError(f"Unknown tool {name}")


OPENAI_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in TOOLS]


class TripAssistant:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: openai.AsyncOpenAI | None = None
        if settings.openai_api_key:
            kwargs = {"api_key": settings.openai_api_key}
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = openai.AsyncOpenAI(**kwargs)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _system(self, provider: TransitProvider, req: ChatRequest) -> str:
        info = provider.describe()
        ctx = req.context
        bits = []
        if ctx.user_lat is not None:
            bits.append("The user's location is known; use 'my_location' as origin when they say 'from here' or give no origin.")
        if ctx.selected_station_id and provider.index and ctx.selected_station_id in provider.index.stations:
            bits.append(f"The user has the stop '{provider.index.stations[ctx.selected_station_id].name}' selected on the map.")
        if ctx.selected_vehicle_id:
            bits.append(f"The user has vehicle '{ctx.selected_vehicle_id}' selected on the map.")
        return (
            f"You are the trip assistant inside a live 3D map of public transport in {info.city}. "
            f"Local time: {time.strftime('%H:%M')}. Reply in the language the user writes in.\n"
            "Answer with facts from your tools only - never guess times, lines or stops. "
            "When the user wants to get somewhere, call plan_route; the options appear on their map, so "
            "summarise the best one in a few short sentences (which tram or bus, where to change, wait, total minutes) "
            "and mention an alternative only if it trades time for fewer changes or less walking. "
            "If a place is ambiguous, call search_stations first. Keep replies under 90 words, plain text, no tables or markdown.\n"
            + " ".join(bits)
        )

    async def chat(self, provider: TransitProvider, req: ChatRequest) -> ChatResponse:
        box = ToolBox(provider, req)
        if self._client is None:
            return self._local(box, req)
        try:
            return await self._openai(box, provider, req)
        except openai.AuthenticationError:
            log.warning("OpenAI key rejected - using the local planner")
            note = "(The OpenAI key was rejected, so the built-in planner answered.) "
        except (openai.RateLimitError, openai.APIConnectionError, openai.APIStatusError) as exc:
            log.warning("OpenAI unavailable (%s) - using the local planner", type(exc).__name__)
            note = "(OpenAI couldn't be reached, so the built-in planner answered.) "
        resp = self._local(ToolBox(provider, req), req)
        resp.reply = note + resp.reply
        return resp

    async def _openai(self, box: ToolBox, provider: TransitProvider, req: ChatRequest) -> ChatResponse:
        assert self._client is not None
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system(provider, req)}]
        messages += [{"role": t.role, "content": t.content} for t in req.history]
        messages.append({"role": "user", "content": req.message})

        reply = ""
        for _ in range(self.settings.ai_max_tool_rounds):
            completion = await self._client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                tools=OPENAI_TOOLS,
                tool_choice="auto",
            )
            msg = completion.choices[0].message
            if not msg.tool_calls:
                reply = (msg.content or "").strip()
                break
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump(exclude_none=True) for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    out = json.dumps(box.run(tc.function.name, args if isinstance(args, dict) else {}), default=str, ensure_ascii=False)
                except (PlanningError, KeyError, ValueError) as exc:
                    out = f"Error: {exc}"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
        else:
            reply = "I ran out of steps working on that. Try asking more specifically."

        return ChatResponse(reply=reply or "Done.", engine="openai", journeys=box.journeys, highlights=box.hl, tools_used=box.used)

    # ---- offline fallback -------------------------------------------------------------
    ROUTE_RE = re.compile(r"(?:from\s+(?P<o>.+?)\s+)?to\s+(?P<d>.+?)(?:\s+(?:with|by|using|avoiding|via)\b.*)?[?.!]*$", re.I)

    def _local(self, box: ToolBox, req: ChatRequest) -> ChatResponse:
        text = req.message.strip()
        low = text.lower()
        pref = "fewest_transfers" if re.search(r"fewest|no (transfer|change)|direct", low) else "least_walking" if re.search(r"less walk|least walk|no walk", low) else "fastest"
        try:
            if re.search(r"\b(status|delay|disruption|running|health)\b", low) and " to " not in low:
                rows = box.run("network_status", {})
                bad = [r for r in rows if r["health"] != "good"]
                if not bad:
                    reply = f"All {len(rows)} lines are running normally."
                else:
                    reply = "; ".join(f"{r['name']}: {r['health'].replace('_', ' ')} (avg {round(r['avg_delay_s'] / 60)} min late)" for r in bad)
                return ChatResponse(reply=reply, engine="local", highlights=box.hl, tools_used=box.used)

            m_next = re.search(r"(?:next|when).*?(?:at|from)\s+(?P<s>[\w\s'.-]+?)[?.!]*$", text, re.I)
            if m_next and " to " not in low:
                out = box.run("station_departures", {"station": m_next.group("s")})
                deps = out["departures"][:4]
                if not deps:
                    return ChatResponse(reply=f"No departures predicted at {out['station']} in the next hour.", engine="local", highlights=box.hl, tools_used=box.used)
                lines = ", ".join(f"{d['line']} to {d['towards']} {_when(d['minutes'])}" for d in deps)
                return ChatResponse(reply=f"Next at {out['station']}: {lines}.", engine="local", highlights=box.hl, tools_used=box.used)

            m = self.ROUTE_RE.search(text)
            if m:
                origin = (m.group("o") or "").strip() or "my_location"
                origin = re.sub(r"^(?:get|go|travel|i want to go|how do i get|route|plan)\b\s*", "", origin, flags=re.I) or "my_location"
                dest = m.group("d").strip()
                out = box.run("plan_route", {"origin": origin, "destination": dest, "preference": pref})
                best = out["options"][0]
                rides = [l for l in best["legs"] if l["kind"] == "ride"]
                if not rides:
                    reply = f"Just walk - about {best['minutes']} min ({best['walking_m']} m)."
                else:
                    first = rides[0]
                    steps = [f"take the {first['line']} towards {first['towards']} (next one {_when(first['wait_min'])})"]
                    for leg in rides[1:]:
                        steps.append(f"change at {leg['from']} to the {leg['line']} towards {leg['towards']}")
                    steps.append(f"get off at {rides[-1]['to']}")
                    reply = f"From {out['from']}: " + ", ".join(steps) + f". About {best['minutes']} min in total"
                    reply += f" with {best['transfers']} transfer{'s' if best['transfers'] != 1 else ''}." if best["transfers"] else ", no transfers."
                    if len(out["options"]) > 1:
                        alt = out["options"][1]
                        reply += f" Alternative: {alt['minutes']} min, {alt['transfers']} transfer(s)."
                return ChatResponse(reply=reply, engine="local", journeys=box.journeys, highlights=box.hl, tools_used=box.used)
        except PlanningError as exc:
            return ChatResponse(reply=f"{exc}.", engine="local", highlights=box.hl, tools_used=box.used)

        return ChatResponse(
            reply=(
                "I can plan trips (\"from Hauptbahnhof to Universität\", \"to Göggingen with fewest changes\"), "
                "show departures (\"next tram at Königsplatz\") or check service (\"line status\"). "
                "Add an OPENAI_API_KEY for free-form conversation."
            ),
            engine="local",
        )
