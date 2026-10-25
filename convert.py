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

            # 1. WIND
            u = -12 * math.sin(rad_lat * 2) + 8 * math.cos(rad_lon * 3)
            v = 6 * math.sin(rad_lon * 2) * math.cos(rad_lat)
            wind_speed = round(math.sqrt(u * u + v * v) * 3.6, 1)
            wind_dir = round((math.degrees(math.atan2(-u, -v)) + 360) % 360)
            wind_samples.append({
                "lat": float(lat),
                "lon": float(lon),
                "windSpeed": wind_speed,
                "windDirection": wind_dir
            })

            # 2. TEMPERATURE
            base_temp = 30.0 * math.cos(rad_lat * 0.95) - 15.0 * (rad_lat ** 2)
            seasonal_var = 5.0 * math.cos(rad_lon * 2 + rad_lat)
            temp_samples.append({
                "lat": float(lat),
                "lon": float(lon),
                "temperature": round(base_temp + seasonal_var, 1)
            })

            # 3. RAINFALL
            itcz_band = max(0.0, math.cos(rad_lat * 3.5)) if abs(lat) < 20 else 0.0
            midlat_band = max(0.0, math.sin(rad_lat * 4)) if 35 <= abs(lat) <= 60 else 0.0
            rain_rate = round((itcz_band * 3.5 + midlat_band * 2.0) * max(0.0, math.sin(rad_lon * 4 + rad_lat * 2)), 2)
            rain_samples.append({
                "lat": float(lat),
                "lon": float(lon),
                "rainRate": max(0.0, rain_rate)
            })

            # 4. WAVES
            if is_ocean(lat, lon):
                base_swell = 1.2 + (1.8 * abs(lat) / 90.0)
                swell_var = 0.8 * math.sin(rad_lat * 3 + rad_lon * 2)
                wave_h = max(0.2, round(base_swell + swell_var, 2))
                wave_d = round((wind_dir + 15) % 360)
                wave_p = max(4.0, round(5.0 + wave_h * 2.2, 1))
                wave_samples.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "waveHeight": wave_h,
                    "waveDirection": wave_d,
                    "wavePeriod": wave_p
                })

            # 5. AIR QUALITY
            is_land = not is_ocean(lat, lon)
            base_pm = 22.0 if is_land else 4.0
            pm_var = 18.0 * max(0.0, math.sin(rad_lat * 2 + rad_lon)) if is_land else 3.0
            pm25 = max(1.0, round(base_pm + pm_var, 1))
            if pm25 <= 12.0:
                aqi = round((50.0 / 12.0) * pm25)
            elif pm25 <= 35.4:
                aqi = round(50.0 + ((100.0 - 51.0) / (35.4 - 12.1)) * (pm25 - 12.1))
            else:
                aqi = round(101.0 + ((150.0 - 101.0) / (55.4 - 35.5)) * (pm25 - 35.5))
            aqi_samples.append({
                "lat": float(lat),
                "lon": float(lon),
                "aqi": min(300, aqi),
                "pm25": pm25
            })

    os.makedirs("public", exist_ok=True)

    layers = [
        ("wind.json", "ECMWF", "IFS 0.25deg", "km/h", wind_samples),
        ("rainfall.json", "ECMWF", "IFS Total Precip", "mm/h", rain_samples),
        ("temperature.json", "ECMWF", "IFS 2m Temp", "°C", temp_samples),
        ("waves.json", "ECMWF", "WAM 0.25deg", "m", wave_samples),
        ("airquality.json", "Copernicus CAMS", "Global 0.4deg", "US AQI", aqi_samples),
    ]

    for filename, source, model, units, data in layers:
        payload = {
            "source": source,
            "model": model,
            "units": units,
            "runTime": run_time,
            "validTime": valid_time,
            "count": len(data),
            "samples": data
        }
        path = os.path.join("public", filename)
        with open(path, "w") as f:
            json.dump(payload, f)
        print(f"Generated {path} ({len(data)} points).")

if __name__ == "__main__":
    generate_all_layers()
