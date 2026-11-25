# Public transit APIs

Sources this project uses, plus others worth plugging in as a new provider.

## Used here

| Source | What it gives | Key |
|---|---|---|
| **EFA Bayern** (`bahnland-bayern.de/efa`, rapidJSON) | Live departures for every stop in Bavaria, Augsburg included | No |
| **AVV / DELFI GTFS** (avv-augsburg.de open data, gtfs.de) | Official Augsburg network: lines, stops, shapes, timetables | No |
| **MBTA V3 API** (api-v3.mbta.com) | Live GPS vehicle positions, routes, shapes for Boston | Optional |
| **OpenFreeMap** (tiles.openfreemap.org) | OpenStreetMap vector tiles and fonts for the basemap | No |
| **OpenAI API** | The trip assistant (function calling) | Yes |

## Worth adding

| Source | Region | Live vehicle positions? |
|---|---|---|
| GTFS-Realtime feeds (realtime.gtfs.de for Germany) | Germany | Trip updates; positions vary by operator |
| transport.rest (HAFAS wrappers) | Germany / Europe | `radar` endpoint where the backend supports it |
| Entur (api.entur.io) | Norway | Yes (GraphQL) |
| Digitransit (digitransit.fi) | Finland | Yes (MQTT) |
| TfL Unified API | London | Arrivals, not positions |
| Transitland (transit.land) | Worldwide | Aggregates GTFS and GTFS-RT |
| MTA GTFS-RT | New York | Yes |

Each one becomes a provider: map its network into `Network` and its vehicles into
`Vehicle`, and the map, planner and assistant work unchanged.
