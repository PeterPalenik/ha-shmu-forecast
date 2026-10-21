"""Real HA instances with isolated storage and network-only test doubles."""
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import asyncio
from unittest.mock import patch
import aiohttp

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_setup as loader_setup
from homeassistant.util.unit_system import METRIC_SYSTEM
from homeassistant.bootstrap import async_load_base_functionality

from tests.fixtures import STATIONS, products, payload

ROOT = Path(__file__).resolve().parents[1]


class Response:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        await asyncio.sleep(0)  # external transport can interleave simultaneous flows
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class OfficialNetwork:
    """Only transport is replaced; URL routing distinguishes product and run."""
    def __init__(self):
        self.calls = []
        self.error = None
        self.data_requests = 0
        self.fail_after = None

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "getjsonaladinstations" in url:
            return Response(STATIONS)
        if self.error:
            raise self.error
        station = (kwargs.get("params") or {}).get("station", "99901")
        if "getstationproducts" in url:
            data = products("aladin", station=station)
            data["data"] += products("alaef", station=station)["data"] + products("ecmwf", station=station)["data"]
            return Response(data)
        self.data_requests += 1
        if self.fail_after is not None and self.data_requests > self.fail_after:
            raise TimeoutError()
        model = "alaef" if "/a-laef/" in url else "ecmwf" if "/ecmwf/" in url else "aladin"
        station = url.rsplit("/", 1)[1].split("_", 1)[0]
        return Response(payload(model, station=station))


@asynccontextmanager
async def isolated_hass(tmp_path, network):
    shutil.copytree(ROOT / "custom_components", tmp_path / "custom_components")
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    hass.config.units = METRIC_SYSTEM
    hass.config.latitude = 0.1
    hass.config.longitude = 0.1
    loader_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    assert await async_load_base_functionality(hass)
    # Network is the external seam. Real aiohttp sessions/HA interfaces remain.
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    with patch("homeassistant.helpers.aiohttp_client._async_get_connector", return_value=connector), patch("aiohttp.ClientSession.get", side_effect=network.get):
        try:
            yield hass
        finally:
            await hass.async_stop()
            await hass.async_block_till_done()
            await connector.close()


async def add_via_flow(hass, model="alaef", station="99901", age=36):
    result = await hass.config_entries.flow.async_init("shmu_forecast", context={"source": "user"})
    assert result["type"] == "form", result
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station_id": station, "model": model, "max_run_age_hours": age})
    await hass.async_block_till_done()
    return result
