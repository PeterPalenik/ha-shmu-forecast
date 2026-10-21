"""One bounded SHMU snapshot per config entry."""
from datetime import datetime, timedelta
import logging

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ForecastSnapshot, ShmuClient, ShmuError, ShmuStaleError
from .config_flow import selection
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def validate_freshness(snapshot: ForecastSnapshot, max_run_age_hours: int, now: datetime | None = None):
    """Operator policy, not an assertion about SHMU publication cadence."""
    now = now or dt_util.utcnow()
    if not timedelta(0) <= now - snapshot.model_run <= timedelta(hours=max_run_age_hours):
        raise ShmuStaleError("Run outside freshness policy")
    if not any(datetime.fromisoformat(r["datetime"]) > now for r in snapshot.records):
        raise ShmuStaleError("No future forecast")


class ShmuCoordinator(DataUpdateCoordinator[ForecastSnapshot]):
    """Fetch only the effective saved selection; errors fail closed."""

    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=timedelta(minutes=30))
        self.selection = selection(entry)
        self.client = ShmuClient(async_get_clientsession(hass))

    @property
    def usable(self):
        if not self.last_update_success or self.data is None:
            return False
        try:
            validate_freshness(self.data, self.selection["max_run_age_hours"])
        except ShmuError:
            return False
        return True

    async def _async_update_data(self):
        try:
            snapshot = await self.client.async_forecast(self.selection["station_id"], self.selection["model"])
            validate_freshness(snapshot, self.selection["max_run_age_hours"])
            return snapshot
        except ShmuError as err:
            raise UpdateFailed(type(err).__name__) from None
