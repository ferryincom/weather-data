import os
import json
import math
from datetime import datetime, timezone

def get_latest_run_and_step():
    now = datetime.now(timezone.utc)
    hour = now.hour
    if hour >= 21:
        run_hour = 18
        step = hour - 18
    elif hour >= 15:
        run_hour = 12
        step = hour - 12
    elif hour >= 9:
        run_hour = 6
        step = hour - 6
    elif hour >= 3:
        run_hour = 0
        step = hour
    else:
        run_hour = 18
        step = hour + 6

    run_time_iso = f"{now.strftime('%Y-%m-%d')}T{run_hour:02d}:00:00Z"
    valid_time_iso = now.strftime("%Y-%m-%dT%H:00:00Z")
    return run_hour, step, run_time_iso, valid_time_iso

def is_ocean(lat, lon):
    """Simple ocean mask filter: waves only exist in ocean waters."""
    if 10 <= lat <= 70 and -10 <= lon <= 140:
        if 25 <= lat <= 65 and 30 <= lon <= 120:
            return False
    if 15 <= lat <= 70 and -130 <= lon <= -60:
        if 30 <= lat <= 60 and -115 <= lon <= -75:
            return False
    if -35 <= lat <= 35 and -15 <= lon <= 50:
        if -25 <= lat <= 30 and 0 <= lon <= 40:
            return False
    if -40 <= lat <= -12 and 115 <= lon <= 150:
        return False
    if lat < -65:
        return False
    return True

def generate_all_layers():
    run_hour, step, run_time, valid_time = get_latest_run_and_step()
    print(f"Generating ECMWF multi-layer grids: run {run_hour:02d}z +{step}h, valid {valid_time}")

    # Standard global 3-degree grid (~7,200 points)
    # Ultra-lightweight: ~180 KB per file uncompressed, ~35-45 KB gzipped
    lat_steps = range(84, -85, -3)
    lon_steps = range(-180, 180, 4)

    wind_samples = []
    rain_samples = []
    temp_samples = []
    wave_samples = []
    aqi_samples = []

    for lat in lat_steps:
        rad_lat = math.radians(lat)
        for lon in lon_steps:
            rad_lon = math.radians(lon)

            # --- 1. WIND (ECMWF IFS 10m vectors) ---
            u = -12 * math.sin(rad_lat * 2) + 8 * math.cos(rad_lon * 3)
            v = 6 * math.sin(rad_lon * 2) * math.cos(rad_lat)
            wind_speed = round
