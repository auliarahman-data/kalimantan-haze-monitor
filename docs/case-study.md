# Case study: Kalimantan haze monitor

**Question:** when and where does air quality get worse in Kalimantan, and does it follow fire hotspots and weather?

**Stack:** Python, GitHub Actions, BigQuery (SQL, window functions, geography functions), Looker Studio.
**Status (16 Oct 2026):** pipeline, views and Looker Studio dashboard are running. History covers 1 Jul – 15 Oct 2026 (complete days), backfilled from the same sources the daily run uses.

## 1. What I built

A daily pipeline that pulls three open datasets (NASA FIRMS fire detections, Open-Meteo air quality, Open-Meteo weather), keeps the raw history as CSV files in this repository, rebuilds BigQuery tables from those files, and exposes a set of SQL views for analysis.

The decisions below are the ones that shaped the result, including the mistakes I caught along the way.

## 2. Design decisions

**The repository is the source of truth, not BigQuery.** BigQuery sandbox tables expire after 60 days. I confirmed this on the tables themselves: expiration was set to 60 days after creation, and a `WRITE_TRUNCATE` reload did not extend it. So the CSVs in `data/` are the permanent record, and each loader rebuilds its table from them. Every loader refuses to truncate a table if its CSV is missing or empty.

**Overlapping fetch windows instead of fragile scheduling.** Each run re-fetches the last 3 days and merges by key, so a missed or late run never leaves a gap. The scheduled run on 16 Oct started about two hours after its 06:40 WITA slot (GitHub schedules are best-effort), and that did not matter.

**Backfill without double counting.** The daily run only reaches back 3 days, so the history from July came from a separate `backfill` workflow. NASA FIRMS publishes fire detections twice: near-real-time (NRT) first, standard processing (SP) months later, and the two versions of one detection are not guaranteed to match exactly. Mixing both for one day would count fires twice, so each day comes from exactly one product (SP where FIRMS has published it, NRT otherwise) and a `source` column records which. In practice SP only reached 30 Jun, so July–October is NRT. For weather, the live API stops at 92 days back, so the backfill uses Open-Meteo's archive of the same forecast models; the grid cells turned out identical to the daily data. A backfill fills only missing hours and never overwrites what the daily run collected.

**Monthly files.** Ten days of fire detections were already 3.3 MB, so the CSV is split per month (`data/fire_hotspots/YYYY-MM.csv`) before it could approach GitHub's file size limits. The busiest month, September, is 13 MB.

**Views as code.** `sql/views.sql` is applied by a workflow (`apply-views`) with the pipeline's service account, so rebuilding the BigQuery views after a sandbox expiry or a change is one run, not a copy-paste into the console.

**Join on `(city, time_utc)`, not on coordinates.** The weather grid (about 9–11 km) and the air quality grid (about 45 km) differ, so matching by coordinates would silently pair different cells.

**Keep rows I cannot place.** Fire hotspots are fetched for all of Borneo, so some fall outside the five Kalimantan provinces (3,562 of 270,280, about 1.3%, probably coast and neighbouring territory, though I have not inspected them). They are labelled and kept rather than dropped, so counts reconcile with the source table.

## 3. Problems found and fixed

These are the parts I would want a reviewer to see.

1. **A green workflow was not proof that everything worked.** The air quality and weather fetch steps use `continue-on-error`, so a failed fetch would not turn the run red. I checked every step of the first scheduled run individually before trusting it.
2. **Duplicates, checked rather than assumed.** After the first scheduled run overlapped earlier data, I counted rows against unique keys: 480 rows and 480 unique keys for both air quality and weather (5 cities × 96 hours), and no duplicate rows in 10 days of fire detections.
3. **Wrong health categories.** My first version of the air quality view classified single hours with older PM2.5 breakpoints. The EPA scale applies to 24-hour averages and the 2024 breakpoints differ, so I rebuilt the view with a trailing 24-hour mean, the 2024 breakpoints, and `NULL` until a full 24 hours exist. I tested it on known rows: the Palangka Raya value of 3,248.5 µg/m³ on 14 Oct has a 24-hour mean of about 1,154 and is correctly categorised as Hazardous.
4. **Time zone error.** Palangka Raya (Central Kalimantan) is on WIB (UTC+7), not WITA. A one-hour shift on the city with the worst haze would have misplaced peak hours and daily totals. Fixed and verified: 10:00 UTC shows as 17:00 for Palangka Raya and 18:00 for Banjarmasin.
5. **Incomplete days can look like trends.** The first and last air quality days are partial by construction (the data starts at 12 Oct 00:00 UTC and ends at the last full hour), and the latest day of fire detections may still be filling in as near-real-time data arrives. Daily fire counts also swing a lot on their own, from 1,053 to 5,505 per day in the first ten days of data, so a low final day is not proof of anything either way. Left unflagged, these days would draw misleading drops at the right edge of every chart. The views now carry `hari_lengkap` and `titik_api_lengkap` flags, and charts filter on them. I treat the latest fire day as incomplete as a precaution, not as something I have demonstrated.
6. **Guards that worked.** The province loader first found 0 matches because the boundary file uses English names ("South Kalimantan"), not Indonesian ones. It stopped without touching the table and printed every province name in the file, which made the fix a one-word change. The first download URL also returned a Git LFS pointer instead of data, so the script now falls back to a second URL and checks for that case.
7. **Averaging wind direction is wrong.** The mean of 350° and 10° is not 180°, so the daily view leaves wind direction out instead of publishing a misleading number.
8. **Missing is not zero.** After the backfill, 11–16 Jul and 3 Aug 2026 had no fire detections at all, while the days around them had 200–950 each. A second request returned the same, so the gap is in the source, not in my code. The first version of the joined view turned these days into "0 hotspots", which would draw a false drop. Days with no FIRMS data anywhere in Borneo now give `NULL` and are excluded by `titik_api_lengkap`; a province with no fires on a day that does have data still counts as a real 0. I did not fill the gap from another satellite, because counts from different satellites are not comparable. Completeness after the fix: 98.2% of city-days for air quality and weather (only the first and the current day are partial) and 92.7% of fire days; in the last 30 days only the current day is incomplete.
9. **Failures I could not see.** The first two backfill runs failed and the job logs were not readable from where I was working. Both failures were in the one part I had not been able to test against the real API: the FIRMS availability check and the SP file format (SP has an extra `type` column, so an exact header match rejected it). The script now writes its error as a GitHub annotation, which is readable without the log and never includes the request URL with the API key. The third run went through.

## 4. What the data shows so far

A dated snapshot (complete days, 1 Jul – 15 Oct 2026). PM2.5 values are model estimates, not station readings.

- **Fire season.** 270,280 VIIRS detections across Borneo: 7,410 in July, 93,207 in August, 116,531 in September and 53,132 in the first half of October (July is missing six days, see above). Central Kalimantan has 51% (139,017) and West Kalimantan 27% (74,043); East 26,915, South 24,131, North 2,612.
- **The provinces peak at different times.** West Kalimantan had the most detections in August (40,181, more than Central Kalimantan that month); Central Kalimantan peaked in September (68,123).
- **Haze follows the same split.** Palangka Raya's monthly mean of daily PM2.5 rose from about 25 µg/m³ in July to 124 in August, 465 in September and 747 in the first half of October. It had 48 Hazardous days (first on 21 Aug) and a highest daily mean of about 1,140 on 23 Sep. Pontianak, next to the August fires in West Kalimantan, had 64 days at Unhealthy or worse, starting 25 Jul, with its highest daily mean (about 199) on 27 Aug. Banjarmasin reached Unhealthy only from 12 Sep. Balikpapan never did (highest daily mean about 44).
- **Correlation, read with care.** Daily hotspots in Central Kalimantan and PM2.5 in Palangka Raya have a rank correlation of about 0.8. Most of that is the shared season: both rise from July to October. After removing a 15-day trend, the same-day correlation is about 0, and the correlation with the previous day's hotspots is about 0.4. That fits smoke taking time to build up and drift, but it is not proof.
- **The data is not independent.** CAMS, the model behind the PM2.5 values, uses satellite fire observations as an emission input, and has used VIIRS since December 2025 ([Copernicus](https://atmosphere.copernicus.eu/drifting-away-modis-cams-global-fire-assimilation-system)). So "modelled PM2.5 follows VIIRS hotspots" is partly built into the model. Testing the link properly needs station measurements.
- **Rain.** In Palangka Raya, August–October, the few rainy days were not cleaner than dry ones, but there were only 3 days with 10 mm or more. This data cannot say anything about rain.

What I claim: the timing and location of the worst modelled haze match the timing and location of the fires, province by province. I do not claim a measured effect size.

## 5. Limitations

- Air quality values are CAMS model estimates, not station measurements, on a coarse grid with roughly 3-hour time resolution. Hourly values are probably interpolated, so peak hours are approximate, and extreme values are model output.
- Fire counts are not normalised by province area.
- Local-date air quality is joined to UTC-date fire counts, so day boundaries differ by a few hours.
- Each province is represented by one reference city (Balikpapan for East Kalimantan, Tarakan for North Kalimantan).
- Counts are fire detections, not distinct fires: the same fire can be detected by several passes and on consecutive days (roughly half of the detections repeat a location already seen that day).
- The free Open-Meteo API is for non-commercial use only.

## 6. Next steps

1. Compare modelled PM2.5 with station measurements where they exist, to test the fire link with data that is independent of the satellite fire input.
2. Normalise hotspot counts by province area.
3. Re-fetch July–October from SP once NASA publishes it, and check how much the counts change.
