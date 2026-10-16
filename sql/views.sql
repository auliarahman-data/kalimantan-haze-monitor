-- Views for kalimantan-haze-monitor (BigQuery project: kalimantan-haze, dataset: haze)
-- Run order: v_air_quality_clean -> v_air_quality_daily -> v_air_weather_hourly;
-- v_fire_hotspots_province is independent.
-- Time zones: Pontianak and Palangka Raya use WIB (UTC+7); other cities WITA (UTC+8).
-- PM2.5 categories use US EPA 2024 breakpoints on the 24-hour average (ug/m3).

-- 1. Hourly air quality: local time, trailing 24h mean, category
CREATE OR REPLACE VIEW `kalimantan-haze.haze.v_air_quality_clean` AS
WITH base AS (
  SELECT
    city,
    time_utc,
    DATETIME(TIMESTAMP_ADD(time_utc, INTERVAL
      IF(city IN ('Pontianak', 'Palangka Raya'), 7, 8) HOUR)) AS time_local,
    IF(city IN ('Pontianak', 'Palangka Raya'), 'WIB', 'WITA') AS tz_label,
    pm2_5, pm10, us_aqi_pm2_5,
    AVG(pm2_5) OVER w AS pm25_avg24,
    COUNT(pm2_5) OVER w AS jam_dalam_window
  FROM `kalimantan-haze.haze.air_quality_hourly`
  WINDOW w AS (
    PARTITION BY city
    ORDER BY UNIX_SECONDS(time_utc)
    RANGE BETWEEN 82800 PRECEDING AND CURRENT ROW
  )
)
SELECT
  city, time_utc, time_local, tz_label, pm2_5, pm10, us_aqi_pm2_5,
  IF(jam_dalam_window = 24, pm25_avg24, NULL) AS pm25_avg24,
  CASE
    WHEN jam_dalam_window < 24 THEN NULL
    WHEN ROUND(pm25_avg24, 1) <= 9.0   THEN 'Baik'
    WHEN ROUND(pm25_avg24, 1) <= 35.4  THEN 'Sedang'
    WHEN ROUND(pm25_avg24, 1) <= 55.4  THEN 'Tidak sehat bagi kelompok sensitif'
    WHEN ROUND(pm25_avg24, 1) <= 125.4 THEN 'Tidak sehat'
    WHEN ROUND(pm25_avg24, 1) <= 225.4 THEN 'Sangat tidak sehat'
    ELSE 'Berbahaya'
  END AS kategori_pm25_24j
FROM base;

-- 2. Daily air quality (by local date)
CREATE OR REPLACE VIEW `kalimantan-haze.haze.v_air_quality_daily` AS
SELECT
  city,
  DATE(time_local) AS tanggal_lokal,
  COUNT(*) AS jam_tercatat,
  COUNT(*) = 24 AS hari_lengkap,
  ROUND(AVG(pm2_5), 1) AS pm25_rata2,
  ROUND(MAX(pm2_5), 1) AS pm25_maks,
  ARRAY_AGG(time_local ORDER BY pm2_5 DESC LIMIT 1)[OFFSET(0)] AS sekitar_jam_maks_lokal,
  CASE
    WHEN COUNT(*) < 24 THEN NULL
    WHEN ROUND(AVG(pm2_5), 1) <= 9.0   THEN 'Baik'
    WHEN ROUND(AVG(pm2_5), 1) <= 35.4  THEN 'Sedang'
    WHEN ROUND(AVG(pm2_5), 1) <= 55.4  THEN 'Tidak sehat bagi kelompok sensitif'
    WHEN ROUND(AVG(pm2_5), 1) <= 125.4 THEN 'Tidak sehat'
    WHEN ROUND(AVG(pm2_5), 1) <= 225.4 THEN 'Sangat tidak sehat'
    ELSE 'Berbahaya'
  END AS kategori_pm25_harian
FROM `kalimantan-haze.haze.v_air_quality_clean`
GROUP BY city, tanggal_lokal;

-- 3. Air quality + weather per hour (joined on city + time_utc)
CREATE OR REPLACE VIEW `kalimantan-haze.haze.v_air_weather_hourly` AS
SELECT
  a.city, a.time_utc, a.time_local, a.tz_label,
  a.pm2_5, a.pm25_avg24, a.kategori_pm25_24j, a.us_aqi_pm2_5,
  w.temperature_2m, w.relative_humidity_2m, w.precipitation,
  w.cloud_cover, w.wind_speed_10m, w.wind_direction_10m
FROM `kalimantan-haze.haze.v_air_quality_clean` a
JOIN `kalimantan-haze.haze.weather_hourly` w
  ON a.city = w.city AND a.time_utc = w.time_utc;

-- 4. Fire hotspots labelled with province (boundaries: geoBoundaries gbOpen,
--    (c) OpenStreetMap contributors, ODbL 1.0)
CREATE OR REPLACE VIEW `kalimantan-haze.haze.v_fire_hotspots_province` AS
WITH prov AS (
  SELECT
    province_name,
    CASE province_name
      WHEN 'West Kalimantan'    THEN 'Kalimantan Barat'
      WHEN 'Central Kalimantan' THEN 'Kalimantan Tengah'
      WHEN 'South Kalimantan'   THEN 'Kalimantan Selatan'
      WHEN 'East Kalimantan'    THEN 'Kalimantan Timur'
      WHEN 'North Kalimantan'   THEN 'Kalimantan Utara'
    END AS provinsi,
    IF(province_name IN ('West Kalimantan', 'Central Kalimantan'), 7, 8) AS tz_offset_jam,
    ST_GEOGFROMGEOJSON(geojson, make_valid => TRUE) AS geom
  FROM `kalimantan-haze.haze.kalimantan_provinces`
)
SELECT
  h.detected_at_utc, h.acq_date, h.latitude, h.longitude,
  h.satellite, h.instrument, h.confidence, h.frp, h.daynight,
  COALESCE(p.provinsi, 'Di luar 5 provinsi') AS provinsi,
  DATETIME(TIMESTAMP_ADD(h.detected_at_utc, INTERVAL p.tz_offset_jam HOUR)) AS detected_at_local
FROM `kalimantan-haze.haze.fire_hotspots` h
LEFT JOIN prov p
  ON ST_CONTAINS(p.geom, ST_GEOGPOINT(h.longitude, h.latitude));
