# kalimantan-haze-monitor

When and where does air quality get worse in Kalimantan, and does it follow fire hotspots and weather?

This project collects three open data sources every day, loads them into BigQuery, and turns them into a small set of analysis views and a Looker Studio dashboard. It covers five reference cities (Banjarmasin, Palangka Raya, Pontianak, Samarinda, Balikpapan) and the five Kalimantan provinces.

Dashboard: _link coming soon_ · [Case study](docs/case-study.md)

## What it does

```
NASA FIRMS (VIIRS S-NPP NRT)  ─┐
Open-Meteo Air Quality (CAMS) ─┼─► Python fetch scripts ─► CSV files in data/ ─► BigQuery tables ─► SQL views ─► Looker Studio
Open-Meteo Weather            ─┘        (GitHub Actions, daily)   (source of truth)   (rebuilt daily)
```

- **Fire hotspots:** VIIRS active-fire detections for all of Borneo, labelled with the Kalimantan province they fall in.
- **Air quality:** hourly PM2.5, PM10 and US AQI for the five cities, converted to local time (WIB or WITA) and summarised as 24-hour means with US EPA 2024 PM2.5 categories.
- **Weather:** hourly temperature, humidity, precipitation, cloud cover and wind for the same cities, joined to air quality on `(city, time_utc)`.

## Data sources and attribution

| Data | Source | Notes |
|---|---|---|
| Fire detections | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/), VIIRS S-NPP: near-real-time (`VIIRS_SNPP_NRT`) for the daily run, standard processing (`VIIRS_SNPP_SP`) for older days where available | Needs a free MAP_KEY |
| Air quality | [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api), CAMS ENSEMBLE data provider | Model output on a coarse grid (about 45 km), not station measurements |
| Weather | [Open-Meteo Weather API](https://open-meteo.com/); backfill from the [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api) | Grid of about 9–11 km. Free API use is non-commercial only |
| Province boundaries | [geoBoundaries](https://www.geoboundaries.org/) gbOpen IDN ADM1, pinned to commit `9469f09` | © OpenStreetMap contributors, ODbL 1.0 |

The boundary file is downloaded when `load_provinces.py` runs and is not stored in this repository.

## Repository layout

| Path | Purpose |
|---|---|
| `fetch_firms.py`, `fetch_openmeteo.py`, `fetch_weather.py` | Fetch the last 3 days (or a date range with `--start`/`--end`) and merge into the CSVs in `data/` without duplicates |
| `load_to_bigquery.py`, `load_air_quality.py`, `load_weather.py` | Rebuild each BigQuery table from its CSV (`WRITE_TRUNCATE`) |
| `load_provinces.py` | One-off load of the five Kalimantan province polygons (GeoJSON text) |
| `sql/views.sql` | All analysis views, in run order |
| `.github/workflows/daily-update.yml` | Daily run at 22:40 UTC (06:40 WITA); can also be started manually |
| `.github/workflows/backfill.yml` | Manual workflow that fills history between two dates for all three sources, then reloads BigQuery |
| `.github/workflows/load-provinces.yml` | Manual workflow for the province load |
| `data/` | CSV files committed by the workflows; these are the source of truth. Fire detections are split per month in `data/fire_hotspots/YYYY-MM.csv` to stay well under GitHub's file size limits |

## Method

- **Why CSV first.** BigQuery sandbox tables expire after 60 days, so the CSVs in this repo are the permanent record and BigQuery is rebuilt from them. The loaders refuse to truncate a table if the CSV is missing or empty.
- **No duplicates.** Each daily run re-fetches the last 3 days, so one missed run leaves no gap. Fire detections are keyed on `(detected_at_utc, latitude, longitude)`, air quality and weather on `(city, time_utc)`.
- **Backfill.** The `backfill` workflow takes a start and end date. Fire detections come from SP where FIRMS has published it and from NRT otherwise, one source per day: when a day is fetched from one source, that day's rows from the other source are dropped, so the same fire is never counted twice. The `source` column records which product each row came from. Air quality uses the same Open-Meteo endpoint and model as the daily run. Weather older than 92 days is not available from the live API, so the backfill uses Open-Meteo's archive of the same forecast models; its grid point can differ slightly from the live API (see `grid_lat`, `grid_lon`). A backfill only fills hours that are missing and never overwrites rows collected by the daily run.
- **Complete hours only.** The air quality and weather scripts drop the current partial hour and forecast hours.
- **Time zones.** Data is stored in UTC. Pontianak and Palangka Raya use WIB (UTC+7); the other three cities use WITA (UTC+8). Fire hotspots get a local time based on the province they fall in.
- **PM2.5 categories.** US EPA 2024 breakpoints (µg/m³) applied to the **24-hour mean**, not to single hours: Good 0–9.0, Moderate 9.1–35.4, Unhealthy for sensitive groups 35.5–55.4, Unhealthy 55.5–125.4, Very unhealthy 125.5–225.4, Hazardous 225.5 and above. A day or window counts only when all 24 hours are present.
- **Province labels.** Hotspot points are matched to province polygons with BigQuery geography functions (`ST_CONTAINS`, `make_valid`). Points outside the five provinces (Sabah, Sarawak, Brunei, sea) are labelled "Di luar 5 provinsi" and kept, so row counts reconcile with the source table.
- **Wind.** Speed is in km/h (API default). Wind direction is not averaged, because a plain mean of angles is wrong.

## Limitations

- **Modelled, not measured.** Air quality values are CAMS model estimates, not station readings. The source model has about 3-hour time resolution, so hourly values are probably interpolated and "peak hour" should be read as approximate. Extreme values (Palangka Raya reaches over 3,000 µg/m³ hourly in the model) should be read as model estimates, not as official measurements.
- **Missing fire days.** FIRMS returned no VIIRS S-NPP detections for Borneo on 11–16 Jul and 3 Aug 2026 (UTC), also on a second request, while the days around them have data. I treat these as missing, not as zero fires, and did not fill them from another satellite because counts from different satellites are not comparable.
- **History and mixed sources.** The daily run started on 12 Oct 2026; anything earlier comes from the backfill. Fire counts from SP and NRT are close but not identical products, so a small step at the SP/NRT boundary date is possible.
- **Fire counts are not normalised** by province area, and the most recent day may still be filling in while near-real-time data arrives (it is flagged as incomplete in the views as a precaution). Daily counts also vary a lot on their own.
- **Local date vs UTC date.** Daily air quality uses local dates while fire counts use the FIRMS UTC date, so there is an offset of a few hours at day boundaries.
- **One reference city per province** (two for East Kalimantan). City values are not province averages.
- **`us_aqi_pm2_5` from Open-Meteo** is a rolling 24-hour value on the EPA 2024 scale and can exceed 500; I use PM2.5 directly and apply the categories above.
- **Sandbox expiry.** Tables and views in the BigQuery sandbox expire; `sql/views.sql` recreates the views and the loaders rebuild the tables from the CSVs.

## Snapshot (16 Oct 2026)

These numbers are a dated snapshot, not a conclusion.

- Between 6 and 15 Oct 2026 the table holds 34,138 hotspots; about 70% (23,729) fall in Central Kalimantan.
- Over the same window the other provinces have far fewer: South 3,587, East 3,517, West 3,009, North 58, and 238 outside the five provinces.
- Palangka Raya's modelled daily mean PM2.5 on 13, 14 and 15 Oct was about 977, 1,135 and 805 µg/m³ (all in the Hazardous category), against roughly 28–82 µg/m³ in the other four cities.
- With three complete days of air quality data, any link between PM2.5, hotspots and weather is only suggestive. The dashboard fills in as daily data accumulates.

## Run it yourself

1. Fork the repository.
2. Create a BigQuery project and dataset (`haze`) and a service account that can write to it.
3. Add three GitHub Actions secrets: `FIRMS_MAP_KEY` (from NASA FIRMS), `GCP_SA_KEY` (service account key) and `BQ_PROJECT` (project ID). Never commit these values.
4. Run **load-provinces** once, then the **backfill** workflow with the start date you want (the daily run keeps it up to date afterwards).
5. Run `sql/views.sql` in the BigQuery console.

## License

Code: MIT (see `LICENSE`). Data remains under the terms of its sources listed above.
