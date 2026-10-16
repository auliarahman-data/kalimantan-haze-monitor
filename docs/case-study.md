# Case study: Kalimantan haze monitor

**Question:** when and where does air quality get worse in Kalimantan, and does it follow fire hotspots and weather?

**Stack:** Python, GitHub Actions, BigQuery (SQL, window functions, geography functions), Looker Studio.
**Status (16 Oct 2026):** the pipeline, data model and views are running and verified. The dashboard is being built.

## 1. What I built

A daily pipeline that pulls three open datasets (NASA FIRMS fire detections, Open-Meteo air quality, Open-Meteo weather), keeps the raw history as CSV files in this repository, rebuilds BigQuery tables from those files, and exposes a set of SQL views for analysis.

The decisions below are the ones that shaped the result, including the mistakes I caught along the way.

## 2. Design decisions

**The repository is the source of truth, not BigQuery.** BigQuery sandbox tables expire after 60 days. I confirmed this on the tables themselves: expiration was set to 60 days after creation, and a `WRITE_TRUNCATE` reload did not extend it. So the CSVs in `data/` are the permanent record, and each loader rebuilds its table from them. Every loader refuses to truncate a table if its CSV is missing or empty.

**Overlapping fetch windows instead of fragile scheduling.** Each run re-fetches the last 3 days and merges by key, so a missed or late run never leaves a gap. The scheduled run on 16 Oct started about two hours after its 06:40 WITA slot (GitHub schedules are best-effort), and that did not matter.

**Join on `(city, time_utc)`, not on coordinates.** The weather grid (about 9–11 km) and the air quality grid (about 45 km) differ, so matching by coordinates would silently pair different cells.

**Keep rows I cannot place.** Fire hotspots are fetched for all of Borneo, so some fall outside the five Kalimantan provinces (238 of 34,138, probably coast and neighbouring territory, though I have not inspected them). They are labelled and kept rather than dropped, so counts reconcile with the source table.

## 3. Problems found and fixed

These are the parts I would want a reviewer to see.

1. **A green workflow was not proof that everything worked.** The air quality and weather fetch steps use `continue-on-error`, so a failed fetch would not turn the run red. I checked every step of the first scheduled run individually before trusting it.
2. **Duplicates, checked rather than assumed.** After the first scheduled run overlapped earlier data, I counted rows against unique keys: 480 rows and 480 unique keys for both air quality and weather (5 cities × 96 hours), and no duplicate rows in 10 days of fire detections.
3. **Wrong health categories.** My first version of the air quality view classified single hours with older PM2.5 breakpoints. The EPA scale applies to 24-hour averages and the 2024 breakpoints differ, so I rebuilt the view with a trailing 24-hour mean, the 2024 breakpoints, and `NULL` until a full 24 hours exist. I tested it on known rows: the Palangka Raya value of 3,248.5 µg/m³ on 14 Oct has a 24-hour mean of about 1,154 and is correctly categorised as Hazardous.
4. **Time zone error.** Palangka Raya (Central Kalimantan) is on WIB (UTC+7), not WITA. A one-hour shift on the city with the worst haze would have misplaced peak hours and daily totals. Fixed and verified: 10:00 UTC shows as 17:00 for Palangka Raya and 18:00 for Banjarmasin.
5. **Incomplete days can look like trends.** The first and last air quality days are partial by construction (the data starts at 12 Oct 00:00 UTC and ends at the last full hour), and the latest day of fire detections may still be filling in as near-real-time data arrives. Daily fire counts also swing a lot on their own, from 1,053 to 5,505 per day in this window, so a low final day is not proof of anything either way. Left unflagged, these days would draw misleading drops at the right edge of every chart. The views now carry `hari_lengkap` and `titik_api_lengkap` flags, and charts filter on them. I treat the latest fire day as incomplete as a precaution, not as something I have demonstrated.
6. **Guards that worked.** The province loader first found 0 matches because the boundary file uses English names ("South Kalimantan"), not Indonesian ones. It stopped without touching the table and printed every province name in the file, which made the fix a one-word change. The first download URL also returned a Git LFS pointer instead of data, so the script now falls back to a second URL and checks for that case.
7. **Averaging wind direction is wrong.** The mean of 350° and 10° is not 180°, so the daily view leaves wind direction out instead of publishing a misleading number.

## 4. What the data shows so far

This is a dated snapshot, not a conclusion. I only have three complete days of air quality and weather data.

- 34,138 fire detections between 6 and 15 Oct 2026. About 70% (23,729) are in Central Kalimantan. Other provinces: South 3,587, East 3,517, West 3,009, North 58.
- Palangka Raya's modelled daily mean PM2.5 on 13, 14 and 15 Oct was about 977, 1,135 and 805 µg/m³, all Hazardous, against roughly 28–82 µg/m³ in the other four cities.
- Banjarmasin rose each day (about 51, 76, 82 µg/m³) and the last two days fall in the Unhealthy category on daily means.
- Rain and low PM2.5 coincide in a few city-days (for example Balikpapan on 13 Oct), but three days cannot support a causal claim.

The spatial pattern (most fire detections in Central Kalimantan, most severe modelled haze in Palangka Raya) is consistent, and that is all I claim.

## 5. Limitations

- Air quality values are CAMS model estimates, not station measurements, on a coarse grid with roughly 3-hour time resolution. Hourly values are probably interpolated, so peak hours are approximate, and extreme values are model output.
- Fire counts are not normalised by province area.
- Local-date air quality is joined to UTC-date fire counts, so day boundaries differ by a few hours.
- Each province is represented by one reference city (two for East Kalimantan).
- The free Open-Meteo API is for non-commercial use only.

## 6. Next steps

1. Finish the Looker Studio dashboard: city selector, daily PM2.5, PM2.5 against hotspot counts, hotspots by province, and a weather table.
2. Let the daily pipeline run for a few weeks, then test the PM2.5–hotspot–weather relationship with lagged correlations, with enough data to say something.
3. Normalise hotspot counts by province area.
4. Add a monthly split of the hotspot CSV before it nears GitHub's file size limits.
