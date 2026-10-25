"""Real ECMWF GRIB2 forecasts for Ferryin Earth; runs in GitHub Actions."""
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import requests
from eccodes import codes_get, codes_get_array, codes_get_values, codes_grib_new_from_file, codes_release
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
BASE = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
OUT = Path(os.environ.get("WEATHER_OUTPUT_DIR", "public"))
SESSION = requests.Session()
SESSION.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504])))
def iso(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def candidates(now):
    latest = now.replace(hour=(now.hour//6)*6,minute=0,second=0,microsecond=0)
    for offset in range(6):
        run = latest - timedelta(hours=6*offset)
        if (now-run).total_seconds() < 6*3600:
            continue
        hours = (now-run).total_seconds()/3600
        yield run,max(3,int(math.floor(hours/3+0.5))*3)
def download(run,step,stream,params,directory):
    stamp = run.strftime("%Y%m%d%H0000")
    url = f"{BASE}/{run:%Y%m%d}/{run.hour:02d}z/ifs/0p25/{stream}/{stamp}-{step}h-{stream}-fc.grib2"
    response = SESSION.get(url.replace(".grib2",".index"),timeout=60)
    response.raise_for_status()
    index = [json.loads(line) for line in response.text.splitlines() if line]
    rows = [r for r in index if r.get("param") in params and r.get("levtype")=="sfc"]
    if sorted(r["param"] for r in rows)!=sorted(params):
        raise ValueError("Missing or duplicate requested fields")
    path = Path(directory)/f"{stream}-{step}.grib2"
    with path.open("wb") as target:
        for row in rows:
            start,size = int(row["_offset"]),int(row["_length"])
            part = SESSION.get(url,headers={"Range":f"bytes={start}-{start+size-1}"},timeout=120)
            part.raise_for_status()
            if part.status_code!=206 or len(part.content)!=size or not part.content.startswith(b"GRIB"):
                raise ValueError("Invalid GRIB byte range")
            target.write(part.content)
    return path,url,hashlib.sha256(path.read_bytes()).hexdigest()
def decode(path):
    fields = {}
    with path.open("rb") as file:
        while True:
            handle = codes_grib_new_from_file(file)
            if handle is None:
                break
            try:
                name = codes_get(handle,"shortName")
                lat = codes_get_array(handle,"latitudes")
                lon = (codes_get_array(handle,"longitudes")+180)%360-180
                keep = (np.abs(lat/2-np.round(lat/2))<1e-6)&(np.abs(lon/2-np.round(lon/2))<1e-6)&(np.abs(lat)<=84)
                values = codes_get_values(handle)
                valid = np.isfinite(values)&(values!=codes_get(handle,"missingValue"))
                if codes_get(handle,"bitmapPresent"):
                    valid &= codes_get_array(handle,"bitmap").astype(bool)
                values = np.where(valid,values,np.nan)
                run = datetime.strptime(f"{codes_get(handle,'dataDate')}{codes_get(handle,'dataTime'):04d}","%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                time = datetime.strptime(f"{codes_get(handle,'validityDate')}{codes_get(handle,'validityTime'):04d}","%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                fields[name] = dict(lat=lat[keep],lon=lon[keep],values=values[keep],run=run,time=time,units=codes_get(handle,"units"),start=int(codes_get(handle,"startStep")),end=int(codes_get(handle,"endStep")),step_type=codes_get(handle,"stepType"))
            finally:
                codes_release(handle)
    return fields
def field(fields,*names):
    for name in names:
        if name in fields:
            return fields[name]
    raise ValueError(f"Missing GRIB field {names}")
def aligned(*fields):
    origin = fields[0]
    for item in fields[1:]:
        if item["run"]!=origin["run"] or not np.array_equal(item["lat"],origin["lat"]) or not np.array_equal(item["lon"],origin["lon"]):
            raise ValueError("Mismatched GRIB grids or initialization")
def rain_rate(end,start):
    if end["units"]!="m" or end["step_type"]!="accum":
        raise ValueError("Unrecognized precipitation units or type")
    duration = end["end"]-(start["end"] if start is not None else 0)
    if duration<=0:
        raise ValueError("Invalid precipitation duration")
    if end["start"]==end["end"]-duration:
        increment = end["values"]
    elif end["start"]==0:
        if start is not None:
            aligned(end,start)
            if start["start"]!=0 or start["units"]!="m" or start["step_type"]!="accum":
                raise ValueError("Incompatible accumulation windows")
        increment = end["values"]-(start["values"] if start is not None else 0)
    else:
        raise ValueError("Unsupported accumulation window")
    if np.any(increment < -0.00001):
        raise ValueError("Negative precipitation increment")
    return np.maximum(0,increment)*1000/duration
def samples(origin,**columns):
    output = []
    for i,(lat,lon) in enumerate(zip(origin["lat"],origin["lon"])):
        values = {name:float(array[i]) for name,array in columns.items()}
        if all(math.isfinite(value) for value in values.values()):
            output.append(dict(lat=float(lat),lon=float(lon),**{name:round(value,2) for name,value in values.items()}))
    return output
def payload(origin,samples,units,model,provenance,**extra):
    if len(samples)<100:
        raise ValueError("Insufficient real grid values")
    return dict(schemaVersion=2,dataKind="forecast",source="ECMWF",model=model,units=units,runTime=iso(origin["run"]),validTime=iso(origin["time"]),generatedAt=iso(datetime.now(timezone.utc)),gridResolution=2,attribution="ECMWF Open Data · CC BY 4.0",provenance=dict(method="grib2-eccodes",**provenance),count=len(samples),samples=samples,**extra)
def convert(run,step,directory):
    atmosphere,url,digest = download(run,step,"oper",["10u","10v","2t","tp"],directory)
    waves,wave_url,wave_digest = download(run,step,"wave",["swh","mwd","mwp"],directory)
    data,wave_data = decode(atmosphere),decode(waves)
    u,v,t,tp = field(data,"10u","u10"),field(data,"10v","v10"),field(data,"2t","t2m"),field(data,"tp")
    h,d,p = field(wave_data,"swh"),field(wave_data,"mwd"),field(wave_data,"mwp")
    aligned(u,v,t,tp)
    aligned(h,d,p)
    for item in [u,v,t,tp,h,d,p]:
        if item["run"]!=run or item["time"]!=run+timedelta(hours=step):
            raise ValueError("GRIB forecast timestamp mismatch")
    if u["units"]!="m s**-1" or v["units"]!="m s**-1" or t["units"]!="K" or h["units"]!="m" or d["units"]!="Degree true" or p["units"]!="s":
        raise ValueError("Unexpected field units")
    proof = dict(urls=[url],sha256=[digest])
    start = None
    if step>3:
        previous,previous_url,previous_digest = download(run,step-3,"oper",["tp"],directory)
        start = field(decode(previous),"tp")
        proof["urls"].append(previous_url)
        proof["sha256"].append(previous_digest)
    rain = rain_rate(tp,start)
    speed = np.hypot(u["values"],v["values"])*3.6
    direction = np.degrees(np.arctan2(-u["values"],-v["values"]))%360
    return {
        "wind.json":payload(u,samples(u,windSpeed=speed,windDirection=direction),"km/h","IFS Open Data (native 0.25°; sampled 2°)",proof),
        "temperature.json":payload(t,samples(t,temperature=t["values"]-273.15),"°C","IFS 2m temperature",proof),
        "rainfall.json":payload(tp,samples(tp,rainRate=rain),"mm/h","IFS precipitation (rain + snow water equivalent)",proof,intervalStart=iso(run+timedelta(hours=step-3)),intervalEnd=iso(tp["time"]),averagingHours=3),
        "waves.json":payload(h,samples(h,waveHeight=h["values"],waveDirection=d["values"],wavePeriod=p["values"]),"m","IFS WAM",dict(urls=[wave_url],sha256=[wave_digest])),
        "airquality.json":dict(schemaVersion=2,available=False,source="Copernicus CAMS",reason="CAMS access is not configured. A free ADS account and API token are required.",samples=[])
    }
def main():
    result = None
    with tempfile.TemporaryDirectory() as directory:
        for run,step in candidates(datetime.now(timezone.utc)):
            try:
                print(f"Checking {iso(run)} +{step}h",flush=True)
                result = convert(run,step,directory)
                break
            except (requests.RequestException,ValueError) as error:
                print(f"Candidate unavailable: {error}",flush=True)
    if result is None:
        raise RuntimeError("No complete real forecast; previously published files remain unchanged")
    OUT.mkdir(parents=True,exist_ok=True)
    for filename,value in result.items():
        temporary = OUT/f"{filename}.tmp"
        temporary.write_text(json.dumps(value,separators=(",",":"),allow_nan=False),encoding="utf-8")
        temporary.replace(OUT/filename)
        print(f"Published {filename}: {len(value['samples'])} actual points")
if __name__=="__main__":
    main()
