"""Synthetic schema-faithful SHMU data; no downloaded place data."""
from datetime import UTC, datetime

RUN = 1893456000  # 2030-01-01T00:00:00Z
NAMES = {"aladin": "ALADIN SHMU/SK 4.5km 87L", "alaef": "ALADIN ENS 4.8km 60L", "ecmwf": "ECMWF ENS 9km 137L"}
PATHS = {"aladin": "aladin", "alaef": "a-laef", "ecmwf": "ecmwf"}
STATIONS = [{"station_id": 99901, "station_name": "Synthetic Valley", "lat": "0.0", "lon": "0.0", "district_code": 0}, {"station_id": 99902, "station_name": "Synthetic Hill", "lat": "1.0", "lon": "1.0", "district_code": 0}]


def payload(model="alaef", runtime=RUN, station="99901"):
    steps = [0, 3, 6, 12, 18] if model == "ecmwf" else [0, 1, 2, 3, 4]
    base = {"aladin": 10, "alaef": 20, "ecmwf": 30}[model]
    def field(unit, values):
        if model == "aladin":
            return {"comment": "Synthetic field", "unit": unit, "data": [[runtime + h * 3600, v] for h, v in values]}
        return {"comment": "Synthetic field", "unit": unit,
                "columns": ["Time", "Maximum", "Median", "Minimum"],
                "data": [[runtime + h * 3600, None if v is None else v + 5, v, None if v is None else v - 5] for h, v in values]}
    return {"location_name": "Synthetic Valley", "lat": 0.0, "lon": 0.0, "elevation": 100, "si_id": int(station),
            "model_name": NAMES[model], "data_date_time": datetime.fromtimestamp(runtime, UTC).strftime("%Y-%m-%dT%H:%MZ"),
            "Air_temperature_at_2m": field("°C", [(h, base + i) for i, h in enumerate(steps)]),
            "Total_precipitation": field("mm", [(h, 2.5) for h in steps if h > 0 and (model != "ecmwf" or h % 6 == 0)]),
            "Wind_speed_at_10m": field("m/s", [(h, 3.5) for h in steps]),
            "Mean_sea_level_pressure": field("hPa", [(h, 1010) for h in steps]),
            "Total_cloud_cover": field("%", [(h, None) for h in steps]),
            "Wind_direction_at_10m": {"unit": "°" if model == "aladin" else "direction", "columns": ["Time", "N", "NE"], "data": [[runtime, 90] if model == "aladin" else [runtime, 3, 4]]}}


def products(model="alaef", runtimes=(RUN,), station="99901"):
    return {"station": {**STATIONS[0], "station_id": int(station)}, "data": [
        {"type": model, "runtime": t, "dt_runtime": datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M:%S"),
         "file_link": f"{PATHS[model]}/{datetime.fromtimestamp(t, UTC):%Y-%m-%d}/{station}_{datetime.fromtimestamp(t, UTC):%Y-%m-%d_%H}.json"} for t in runtimes]}
