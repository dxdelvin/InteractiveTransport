def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["ai"] == "local"


def test_providers(client):
    ids = {p["id"] for p in client.get("/api/v1/providers").json()}
    assert {"augsburg", "mbta"} <= ids


def test_network_and_vehicles(client):
    net = client.get("/api/v1/augsburg/network").json()
    assert len(net["lines"]) == 9 and len(net["stations"]) > 25
    snap = client.get("/api/v1/augsburg/vehicles").json()
    assert snap["vehicles"]
    vid = snap["vehicles"][0]["id"]
    detail = client.get(f"/api/v1/augsburg/vehicles/{vid}").json()
    assert detail["vehicle"]["id"] == vid and "upcoming" in detail


def test_scenery_has_buildings(client):
    sc = client.get("/api/v1/augsburg/scenery").json()
    kinds = {f["properties"]["kind"] for f in sc["features"]}
    assert {"building", "water", "park"} <= kinds


def test_station_endpoints(client):
    assert client.get("/api/v1/augsburg/stations/search", params={"q": "jakob"}).json()[0]["id"] == "jakobertor"
    board = client.get("/api/v1/augsburg/stations/jakobertor").json()
    assert board["station"]["name"] == "Jakobertor" and board["source"] == "simulated"
    assert client.get("/api/v1/augsburg/stations/nope").status_code == 404


def test_plan(client):
    r = client.post("/api/v1/augsburg/plan", json={"origin": {"query": "Hauptbahnhof"}, "destination": {"query": "Göggingen"}})
    assert r.status_code == 200
    j = r.json()["journeys"][0]
    assert j["legs"] and j["duration_s"] > 0
    bad = client.post("/api/v1/augsburg/plan", json={"origin": {"query": "zzzz"}, "destination": {"query": "Göggingen"}})
    assert bad.status_code == 422


def test_line_status(client):
    rows = client.get("/api/v1/augsburg/lines/status").json()
    assert {r["line_id"] for r in rows} == {"1", "2", "3", "4", "6", "21", "23", "32", "41"}


def test_local_assistant_routes(client):
    r = client.post("/api/v1/augsburg/assistant", json={"message": "How do I get from Oberhausen Nord to Universität?"}).json()
    assert r["engine"] == "local" and r["journeys"] and "Oberhausen" in r["reply"]
    r = client.post("/api/v1/augsburg/assistant", json={"message": "next tram at Königsplatz"}).json()
    assert "Königsplatz" in r["reply"]
    r = client.post("/api/v1/augsburg/assistant", json={"message": "line status"}).json()
    assert r["reply"]


def test_assistant_uses_user_location(client):
    st = client.get("/api/v1/augsburg/stations/search", params={"q": "Moritzplatz"}).json()[0]
    r = client.post("/api/v1/augsburg/assistant", json={"message": "take me to Göggingen", "context": {"user_lat": st["lat"], "user_lon": st["lon"]}}).json()
    assert r["journeys"], r


def test_websocket_stream(client):
    with client.websocket_connect("/ws/augsburg") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "vehicles" and msg["vehicles"]


def test_unknown_provider(client):
    assert client.get("/api/v1/nowhere/network").status_code == 404


def test_alerts(client):
    rows = client.get("/api/v1/augsburg/alerts").json()
    assert isinstance(rows, list)
    for a in rows:
        assert a["severity"] in {"minor", "major", "no_service"} and a["text"]
        assert a["source"] in {"live", "estimated", "none"}
