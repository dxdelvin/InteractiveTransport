Put the Augsburg GTFS feed here as `augsburg.zip` (or set `AUGSBURG_GTFS_PATH`).

Sources:
- AVV (Augsburger Verkehrs- und Tarifverbund) open data - GTFS for the region
- DELFI nationwide feed: https://gtfs.de (large; the loader keeps only trams and buses
  within `AUGSBURG_RADIUS_M` of the city centre)

Without a feed, the app uses the bundled approximation in `app/data/augsburg.py`.
