"""Native subdaily forecasts; current fields are model-derived, not observed."""
from datetime import datetime, timedelta
import math

from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET, UnitOfLength, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.helpers.sun import get_astral_event_next, is_up
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import MODELS


def cloud_condition(percent, daytime):
    """Local cloud-only WMO okta analogy, HALF-UP at half-eighth ties."""
    if (not isinstance(percent, (int, float)) or isinstance(percent, bool)
            or not math.isfinite(percent) or not 0 <= percent <= 100):
        return None
    oktas = math.floor(percent * 8 / 100 + 0.5)
    if oktas <= 2:
        return "sunny" if daytime else "clear-night"
    return "partlycloudy" if oktas <= 5 else "cloudy"


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ShmuWeather(entry.runtime_data, entry)])


class ShmuWeather(CoordinatorEntity, WeatherEntity):
    """One entity with immutable identity across selected-source changes."""
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfLength.MILLIMETERS
    _attr_attribution = "Forecast data provided by SHMÚ"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = entry.entry_id
        self._unsub_clock = None
        self._attr_name = f"{coordinator.data.station['station_name']} Forecast · {MODELS[coordinator.data.selected_model]}"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_clock)
        self._schedule_clock()

    @callback
    def _cancel_clock(self):
        if self._unsub_clock:
            self._unsub_clock()
            self._unsub_clock = None

    @callback
    def _schedule_clock(self):
        self._cancel_clock()
        if not self.available:
            return
        data = self.coordinator.data
        now = dt_util.utcnow()
        times = [datetime.fromisoformat(r["datetime"]) for r in data.records]
        times.append(data.model_run + timedelta(hours=self.coordinator.selection["max_run_age_hours"], microseconds=1))
        # Current-model fields also expire if a required-temperature point is missing.
        times.extend(datetime.fromisoformat(r["datetime"]) + timedelta(hours=max(data.forecast_steps_hours), microseconds=1) for r in data.records)
        # Native sun helpers use HA home coordinates, not the selected grid point.
        for event in (SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET):
            try:
                times.append(get_astral_event_next(self.hass, event, now))
            except (ValueError, TypeError):
                # Missing polar events/invalid home coordinates cannot invent daylight.
                continue
        future = [t for t in times if t > now]
        if future:
            self._unsub_clock = async_track_point_in_utc_time(self.hass, self._clock_changed, min(future))

    @callback
    def _clock_changed(self, now):
        self._unsub_clock = None
        self.async_write_ha_state()
        self._schedule_clock()

    @callback
    def _handle_coordinator_update(self):
        super()._handle_coordinator_update()
        self._schedule_clock()

    @property
    def available(self):
        return self.coordinator.usable

    @property
    def condition(self):
        cloud = self._current("cloud_coverage")
        summary = cloud_condition(cloud, True)
        if summary != "sunny":
            return summary
        try:
            return cloud_condition(cloud, is_up(self.hass, dt_util.utcnow()))
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self):
        if not self.available:
            return {"condition_source": "forecast_model", "condition_method": "cloud_cover_oktas"}
        data = self.coordinator.data
        return {"condition_source": "forecast_model", "condition_method": "cloud_cover_oktas", "selected_model": data.selected_model,
                "model_name": data.model_name, "model_run": data.model_run.isoformat(),
                "source_url": data.source_url, "forecast_steps_hours": list(data.forecast_steps_hours),
                "precipitation_interval_hours": data.precipitation_interval_hours}

    def _current(self, field):
        if not self.available:
            return None
        now = dt_util.utcnow()
        records = [r for r in self.coordinator.data.records if datetime.fromisoformat(r["datetime"]) <= now]
        if not records:
            return None
        record = records[-1]
        largest_step = max(self.coordinator.data.forecast_steps_hours)
        if now - datetime.fromisoformat(record["datetime"]) > timedelta(hours=largest_step):
            return None
        return record.get(field)

    @property
    def native_temperature(self):
        return self._current("native_temperature")

    @property
    def native_pressure(self):
        return self._current("native_pressure")

    @property
    def native_wind_speed(self):
        return self._current("native_wind_speed")

    @property
    def native_wind_gust_speed(self):
        return self._current("native_wind_gust_speed")

    @property
    def wind_bearing(self):
        return self._current("wind_bearing")

    @property
    def cloud_coverage(self):
        return self._current("cloud_coverage")

    async def async_forecast_hourly(self):
        if not self.available:
            return None
        now = dt_util.utcnow()
        return [dict(r) for r in self.coordinator.data.records if datetime.fromisoformat(r["datetime"]) > now]
