"""Bundled Augsburg network (swa trams and a selection of city buses).

This is a hand-built approximation of the network so the app runs out of the box.
Stop positions are close to the real ones, but the routes are simplified and the line
colours follow swa (from OpenStreetMap). For the exact network, drop the AVV/DELFI GTFS feed at
the path in AUGSBURG_GTFS_PATH and the provider will build everything from it instead.
"""
from __future__ import annotations

CITY = "Augsburg"
CENTER = (10.8978, 48.3668)  # (lon, lat) - between Königsplatz and Rathausplatz

# id: (name, lat, lon)
STOPS: dict[str, tuple[str, float, float]] = {
    "koenigsplatz": ("Königsplatz", 48.3653, 10.8941),
    "hauptbahnhof": ("Hauptbahnhof", 48.3657, 10.8856),
    "moritzplatz": ("Moritzplatz", 48.3685, 10.8978),
    "jakobertor": ("Jakobertor", 48.3697, 10.9090),
    "staatstheater": ("Staatstheater", 48.3712, 10.8913),
    "theodor-heuss-platz": ("Theodor-Heuss-Platz", 48.3609, 10.8997),
    "rotes-tor": ("Rotes Tor", 48.3573, 10.9024),
    "goegginger-bruecke": ("Gögginger Brücke", 48.3540, 10.8830),
    "goeggingen": ("Göggingen", 48.3398, 10.8668),
    "schloessle": ("Schlössle", 48.3796, 10.9294),
    "neuer-ostfriedhof": ("Neuer Ostfriedhof", 48.3893, 10.9435),
    "augsburg-west": ("Augsburg West P+R", 48.3767, 10.8356),
    "kriegshaber": ("Kriegshaber", 48.3741, 10.8558),
    "haunstetter-strasse": ("Haunstetter Straße", 48.3505, 10.9035),
    "haunstetten-nord": ("Haunstetten Nord", 48.3198, 10.9037),
    "univiertel": ("Univiertel", 48.3420, 10.8930),
    "universitaet": ("Universität", 48.3338, 10.8978),
    "haunstetten-suedwest": ("Haunstetten Südwest", 48.3032, 10.8889),
    "stadtbergen": ("Stadtbergen", 48.3645, 10.8395),
    "pfersee": ("Pfersee", 48.3650, 10.8735),
    "oberhausen-nord": ("Oberhausen Nord P+R", 48.3967, 10.8770),
    "oberhausen-bahnhof": ("Oberhausen Bahnhof", 48.3843, 10.8783),
    "textilviertel": ("Textilviertel", 48.3595, 10.9120),
    "hochzoll-mitte": ("Hochzoll Mitte", 48.3572, 10.9372),
    "friedberg-west": ("Friedberg West", 48.3573, 10.9606),
    "herrenbach": ("Herrenbach", 48.3543, 10.9175),
    "spickel": ("Spickel", 48.3460, 10.9185),
    "messe": ("Messe", 48.3395, 10.9105),
    "inningen": ("Inningen", 48.3090, 10.8650),
    "baerenkeller": ("Bärenkeller", 48.3958, 10.8555),
    "lechhausen": ("Lechhausen", 48.3760, 10.9220),
    "firnhaberau": ("Firnhaberau", 48.4050, 10.9180),
}

LINES: list[dict] = [
    # trams
    {"id": "1", "name": "Tram 1", "color": "#E3000F", "mode": "tram", "speed": 20, "headway": 300,
     "stops": ["neuer-ostfriedhof", "schloessle", "jakobertor", "moritzplatz", "koenigsplatz", "goegginger-bruecke", "goeggingen"]},
    {"id": "2", "name": "Tram 2", "color": "#0068B3", "mode": "tram", "speed": 20, "headway": 300,
     "stops": ["augsburg-west", "kriegshaber", "staatstheater", "koenigsplatz", "theodor-heuss-platz", "rotes-tor",
               "haunstetter-strasse", "haunstetten-nord"]},
    {"id": "3", "name": "Tram 3", "color": "#EF7C00", "mode": "tram", "speed": 21, "headway": 300,
     "stops": ["stadtbergen", "pfersee", "hauptbahnhof", "koenigsplatz", "theodor-heuss-platz", "univiertel",
               "universitaet", "haunstetten-suedwest"]},
    {"id": "4", "name": "Tram 4", "color": "#941680", "mode": "tram", "speed": 19, "headway": 450,
     "stops": ["oberhausen-nord", "oberhausen-bahnhof", "staatstheater", "koenigsplatz", "hauptbahnhof"]},
    {"id": "6", "name": "Tram 6", "color": "#94C11C", "mode": "tram", "speed": 21, "headway": 450,
     "stops": ["friedberg-west", "hochzoll-mitte", "textilviertel", "jakobertor", "moritzplatz", "koenigsplatz", "hauptbahnhof"]},
    # buses
    {"id": "21", "name": "Bus 21", "color": "#008B70", "mode": "bus", "speed": 18, "headway": 600,
     "stops": ["hauptbahnhof", "pfersee", "kriegshaber", "baerenkeller"]},
    {"id": "23", "name": "Bus 23", "color": "#ABCA97", "mode": "bus", "speed": 18, "headway": 600,
     "stops": ["moritzplatz", "rotes-tor", "herrenbach", "spickel", "messe"]},
    {"id": "32", "name": "Bus 32", "color": "#3EA67B", "mode": "bus", "speed": 19, "headway": 900,
     "stops": ["inningen", "univiertel", "universitaet", "messe", "spickel", "hochzoll-mitte"]},
    {"id": "41", "name": "Bus 41", "color": "#D35D03", "mode": "bus", "speed": 18, "headway": 900,
     "stops": ["firnhaberau", "lechhausen", "jakobertor", "moritzplatz", "staatstheater", "oberhausen-bahnhof"]},
]

# Rivers as (lon, lat) centre lines with a width in metres - drawn by the offline basemap.
RIVERS = [
    {"name": "Lech", "width": 70, "line": [(10.9175, 48.296), (10.9210, 48.320), (10.9240, 48.340), (10.9235, 48.355),
                                            (10.9210, 48.368), (10.9190, 48.380), (10.9150, 48.395), (10.9080, 48.410), (10.9030, 48.428)]},
    {"name": "Wertach", "width": 40, "line": [(10.8550, 48.318), (10.8660, 48.335), (10.8740, 48.350), (10.8770, 48.362),
                                               (10.8800, 48.375), (10.8870, 48.390), (10.8950, 48.405), (10.9030, 48.420)]},
]

# Parks as (lon, lat) rings
PARKS = [
    [(10.8870, 48.3560), (10.8935, 48.3560), (10.8935, 48.3600), (10.8870, 48.3600)],   # Wittelsbacher Park
    [(10.9030, 48.3390), (10.9150, 48.3370), (10.9170, 48.3330), (10.9040, 48.3330)],   # Botanischer Garten
    [(10.9110, 48.3720), (10.9150, 48.3720), (10.9150, 48.3660), (10.9110, 48.3660)],   # Stadtgraben
    [(10.8900, 48.3250), (10.9050, 48.3250), (10.9050, 48.3200), (10.8900, 48.3200)],   # Siebentischwald edge
]

# Where the city is densest (lon, lat, weight) - the old town most of all.
DENSITY = [(10.8985, 48.3688, 1.0), (10.8900, 48.3655, 0.7), (10.9000, 48.3600, 0.55), (10.8780, 48.3650, 0.45),
           (10.9250, 48.3780, 0.45), (10.9000, 48.3500, 0.4), (10.8800, 48.3850, 0.4), (10.9370, 48.3570, 0.35)]
