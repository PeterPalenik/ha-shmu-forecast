"""Native UI flows for an explicit station/product selection."""
import math

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .api import ShmuClient, ShmuError, ShmuTransportError
from .const import DEFAULT_MAX_RUN_AGE_HOURS, DOMAIN, MODELS


def selection(entry):
    """Effective saved selection; options are authoritative once saved."""
    return {**entry.data, **entry.options}


class _SelectionFlow:
    _stations = None

    async def _form(self, user_input, *, step_id, entry=None):
        errors = {}
        client = ShmuClient(async_get_clientsession(self.hass))
        if self._stations is None:
            try:
                self._stations = await client.async_stations()
            except ShmuError:
                return self.async_abort(reason="cannot_connect")
        defaults = selection(entry) if entry else {"model": "alaef", "max_run_age_hours": DEFAULT_MAX_RUN_AGE_HOURS}
        if not entry:
            lat, lon = self.hass.config.latitude, self.hass.config.longitude
            if (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                    and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                def distance(station):
                    a, b = math.radians(lat), math.radians(station["lat"])
                    return math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(station["lon"]-lon)/2)**2
                defaults["station_id"] = min(self._stations, key=distance)["station_id"]
        if user_input is not None:
            defaults.update(user_input)
            age = user_input.get("max_run_age_hours")
            station, model = user_input.get("station_id"), user_input.get("model")
            if (type(age) is not int or not 1 <= age <= 168 or model not in MODELS
                    or station not in {s["station_id"] for s in self._stations}):
                errors["base"] = "invalid_selection"
            elif any(e is not entry and selection(e).get("station_id") == station and selection(e).get("model") == model
                     for e in self.hass.config_entries.async_entries(DOMAIN)):
                if entry:
                    errors["base"] = "already_configured"
                else:
                    return self.async_abort(reason="already_configured")
            else:
                try:
                    snapshot = await client.async_forecast(station, model)
                    # Import here keeps the pure parser independent of HA.
                    from .coordinator import validate_freshness
                    validate_freshness(snapshot, age)
                except ShmuTransportError:
                    errors["base"] = "cannot_connect"
                except ShmuError:
                    errors["base"] = "no_forecast"
                else:
                    # Another flow can save while this one awaits SHMU validation.
                    if any(e is not entry and selection(e).get("station_id") == station and selection(e).get("model") == model
                           for e in self.hass.config_entries.async_entries(DOMAIN)):
                        return self.async_abort(reason="already_configured")
                    data = {"station_id": station, "model": model, "max_run_age_hours": age}
                    if entry:
                        return self.async_create_entry(title="", data=data)
                    return self.async_create_entry(title=f"{snapshot.station['station_name']} Forecast · {MODELS[model]}", data=data)
        station_key = vol.Required("station_id", **({"default": defaults["station_id"]} if "station_id" in defaults else {}))
        schema = vol.Schema({station_key: SelectSelector(SelectSelectorConfig(
            options=[{"value": s["station_id"], "label": s["station_name"]} for s in self._stations],
            mode=SelectSelectorMode.DROPDOWN, custom_value=False)),
            vol.Required("model", default=defaults["model"]): SelectSelector(SelectSelectorConfig(
                options=[{"value": k, "label": v} for k,v in MODELS.items()], mode=SelectSelectorMode.DROPDOWN)),
            vol.Required("max_run_age_hours", default=defaults["max_run_age_hours"]): vol.All(int, vol.Range(min=1, max=168))})
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)


class ShmuConfigFlow(_SelectionFlow, config_entries.ConfigFlow, domain=DOMAIN):
    """Create one entry per selected pair, multiple models allowed."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return await self._form(user_input, step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ShmuOptionsFlow()


class ShmuOptionsFlow(_SelectionFlow, config_entries.OptionsFlowWithReload):
    """Validate before saving; native reload never rolls back selection."""

    async def async_step_init(self, user_input=None):
        return await self._form(user_input, step_id="init", entry=self.config_entry)
