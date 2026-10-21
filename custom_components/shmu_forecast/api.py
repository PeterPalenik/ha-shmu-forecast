"""Direct official SHMU product parser and asynchronous transport."""
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping
import math

import aiohttp

from .const import API_ROOT, DATA_ROOT, MODELS, MODEL_PATHS


class ShmuError(Exception):
    """Safe source error without response bodies or location identifiers."""


class ShmuTransportError(ShmuError):
    """Network/HTTP/decoding failed."""


class ShmuPublicationError(ShmuError):
    """Advertised publication is not available."""


class ShmuSelectionError(ShmuError):
    """Selected station/product is invalid or not advertised."""


class ShmuSchemaError(ShmuError):
    """Required schema, units or provenance do not match."""


class ShmuStaleError(ShmuError):
    """Run expired or contains no future valid temperatures."""


@dataclass(frozen=True)
class ForecastSnapshot:
    """A single selected product/run, never a merge of model runs."""
    selected_model: str
    model_name: str
    model_run: datetime
    source_url: str
    station: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]
    forecast_steps_hours: tuple[float, ...]
    precipitation_interval_hours: int
    omitted_fields: tuple[str, ...] = ()


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _epoch(value):
    if not _finite(value) or value <= 0 or int(value) != value:
        raise ShmuSchemaError("Invalid timestamp")
    try:
        datetime.fromtimestamp(value, UTC)
    except (ValueError, OverflowError, OSError):
        raise ShmuSchemaError("Invalid timestamp") from None
    return int(value)


def _series(field, *, ensemble, unit):
    if not isinstance(field, dict) or field.get("unit") != unit:
        raise ShmuSchemaError("unit")
    columns = field.get("columns")
    if ensemble:
        if (not isinstance(columns, list) or not all(isinstance(c, str) for c in columns)
                or len(set(columns)) != len(columns) or "Time" not in columns or "Median" not in columns):
            raise ShmuSchemaError("columns")
        time_index, index, width = columns.index("Time"), columns.index("Median"), len(columns)
    else:
        time_index, index, width = 0, 1, 2
    rows = field.get("data")
    if not isinstance(rows, list) or not rows:
        raise ShmuSchemaError("rows")
    values, epochs = {}, []
    for row in rows:
        if not isinstance(row, list) or len(row) != width:
            raise ShmuSchemaError("rows")
        t = _epoch(row[time_index])
        if t in epochs:
            raise ShmuSchemaError("duplicate timestamp")
        epochs.append(t)
        if _finite(row[index]):
            values[t] = float(row[index])
    return values, sorted(epochs)


def parse_forecast(payload: dict, *, model: str, station_id: str, runtime: int, source_url: str) -> ForecastSnapshot:
    """Validate selected identity, then read values at their native timestamps."""
    if model not in MODELS:
        raise ShmuSelectionError("Unsupported model")
    if not isinstance(payload, dict):
        raise ShmuSchemaError("Invalid product")
    name = payload.get("model_name")
    expected = {"aladin": "ALADIN SHMU/", "alaef": "ALADIN ENS ", "ecmwf": "ECMWF ENS "}[model]
    if not isinstance(name, str) or not name.startswith(expected):
        raise ShmuSchemaError("Model identity mismatch")
    if isinstance(payload.get("si_id"), bool) or str(payload.get("si_id")) != station_id:
        raise ShmuSchemaError("Station identity mismatch")
    try:
        run = datetime.fromisoformat(payload["data_date_time"])
        if run.tzinfo is None or run.utcoffset().total_seconds() != 0 or run.timestamp() != _epoch(runtime):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ShmuSchemaError("Run identity mismatch") from None
    values, epochs = _series(payload.get("Air_temperature_at_2m"), ensemble=model != "aladin", unit="°C")
    if not values or len(epochs) < 2:
        raise ShmuSchemaError("Missing required temperature")
    all_values = {"native_temperature": values}
    omitted = []
    fields = {"Total_precipitation": ("native_precipitation", "mm"),
              "Wind_speed_at_10m": ("native_wind_speed", "m/s"),
              "Wind_gust_at_10m": ("native_wind_gust_speed", "m/s"),
              "Mean_sea_level_pressure": ("native_pressure", "hPa"),
              "Total_cloud_cover": ("cloud_coverage", "%")}
    if model == "aladin":
        fields["Wind_direction_at_10m"] = ("wind_bearing", "°")
    for source, (target, unit) in fields.items():
        if source not in payload:
            continue
        try:
            series, _ = _series(payload[source], ensemble=model != "aladin", unit=unit)
            # Respect physical domain, never turn missing/invalid into zero.
            if target in ("native_precipitation", "native_wind_speed", "native_wind_gust_speed", "native_pressure"):
                series = {t: v for t,v in series.items() if v >= 0}
            if target == "cloud_coverage":
                series = {t: v for t,v in series.items() if 0 <= v <= 100}
            if target == "wind_bearing":
                series = {t: v for t,v in series.items() if 0 <= v <= 360}
            all_values[target] = series
        except ShmuSchemaError as err:
            reason = str(err) if str(err) in ("unit", "columns", "rows") else "schema"
            omitted.append(f"{source}:{reason}")
    records = tuple(MappingProxyType({"datetime": datetime.fromtimestamp(t, UTC).isoformat(),
                                     **{k: v[t] for k,v in all_values.items() if t in v}}) for t in sorted(values))
    return ForecastSnapshot(model, name, run, source_url,
                            MappingProxyType({"station_id": station_id, "station_name": str(payload.get("location_name", "Forecast"))}), records,
                            tuple(sorted({(b-a)/3600 for a,b in zip(epochs, epochs[1:])})), 6 if model == "ecmwf" else 1,
                            tuple(omitted))


class ShmuClient:
    """Bounded direct client. Catalog state belongs to each calling flow."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def _get(self, url, *, params=None):
        try:
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False) as response:
                if response.status == 404:
                    raise ShmuPublicationError("Publication missing")
                if response.status != 200:
                    raise ShmuTransportError("HTTP request failed")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError):
            raise ShmuTransportError("Request or decoding failed") from None

    async def async_stations(self) -> list[dict]:
        data = await self._get(API_ROOT + "getjsonaladinstations")
        if not isinstance(data, list) or not data:
            raise ShmuSchemaError("Invalid station catalog")
        stations = []
        seen = set()
        for item in data:
            if not isinstance(item, dict):
                raise ShmuSchemaError("Invalid station catalog")
            station = str(item.get("station_id", ""))
            name = item.get("station_name")
            if not station.isdigit() or station in seen or not isinstance(name, str) or not name:
                raise ShmuSchemaError("Invalid station catalog")
            seen.add(station)
            try:
                lat, lon = float(item["lat"]), float(item["lon"])
            except (KeyError, TypeError, ValueError):
                raise ShmuSchemaError("Invalid station catalog") from None
            if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ShmuSchemaError("Invalid station catalog")
            stations.append({"station_id": station, "station_name": name, "lat": lat, "lon": lon})
        return stations

    async def async_forecast(self, station_id: str, model: str) -> ForecastSnapshot:
        if model not in MODELS or not isinstance(station_id, str) or not station_id.isdigit():
            raise ShmuSelectionError("Invalid selection")
        catalog = await self._get(API_ROOT + "getstationproducts", params={"station": station_id})
        if not isinstance(catalog, dict) or not isinstance(catalog.get("station"), dict) or str(catalog["station"].get("station_id")) != station_id:
            raise ShmuSchemaError("Catalog station mismatch")
        rows = catalog.get("data")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ShmuSchemaError("Invalid product catalog")
        selected = [row for row in rows if row.get("type") == model]
        for row in selected:
            _epoch(row.get("runtime"))
        selected.sort(key=lambda row: row["runtime"], reverse=True)
        if not selected:
            raise ShmuSelectionError("Selected product not advertised")
        for row in selected[:2]:
            run = datetime.fromtimestamp(row["runtime"], UTC)
            # Compare the advertised relative link to the selected identity;
            # never generate requests for guessed/unadvertised runs.
            expected = f"{MODEL_PATHS[model]}/{run:%Y-%m-%d}/{station_id}_{run:%Y-%m-%d_%H}.json"
            link = row.get("file_link")
            if not isinstance(link, str) or link != expected:
                raise ShmuSchemaError("Unsafe product path")
            url = DATA_ROOT + link
            try:
                payload = await self._get(url)
                if payload == {} or payload is None:
                    raise ShmuPublicationError("Publication missing")
            except ShmuPublicationError:
                continue
            return parse_forecast(payload, model=model, station_id=station_id, runtime=row["runtime"], source_url=url)
        raise ShmuPublicationError("Advertised publications missing")
