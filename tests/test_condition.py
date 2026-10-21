"""Cloud-only local interpretation, with native HA state and solar timers."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET

from tests.ha_helpers import OfficialNetwork, isolated_hass, add_via_flow
from tests.test_weather import forecasts, assert_no_forecast


@pytest.mark.parametrize("cloud,day,expected", [(0,True,"sunny"),(0,False,"clear-night"),(31.25-1e-8,True,"sunny"),(31.25,True,"partlycloudy"),(31.25+1e-8,False,"partlycloudy"),(68.75-1e-8,True,"partlycloudy"),(68.75,True,"cloudy"),(68.75+1e-8,False,"cloudy"),(100,False,"cloudy"),(None,True,None),(True,True,None),(float("nan"),True,None),(float("inf"),False,None),(-1,True,None),(101,False,None)])
def test_half_up_okta_cloud_summary(cloud, day, expected):
    from custom_components.shmu_forecast import weather
    assert hasattr(weather, "cloud_condition")
    assert weather.cloud_condition(cloud, day) == expected


@pytest.mark.parametrize("cloud,now,expected", [(0,datetime(2030,1,1,0,30,tzinfo=UTC),"clear-night"),(0,datetime(2030,1,1,12,30,tzinfo=UTC),"sunny"),(50,datetime(2030,1,1,0,30,tzinfo=UTC),"partlycloudy"),(100,datetime(2030,1,1,12,30,tzinfo=UTC),"cloudy"),(None,datetime(2030,1,1,12,30,tzinfo=UTC),"unknown"),(True,datetime(2030,1,1,12,30,tzinfo=UTC),"unknown"),(float("nan"),datetime(2030,1,1,12,30,tzinfo=UTC),"unknown"),("missing",datetime(2030,1,1,12,30,tzinfo=UTC),"unknown")])
def test_actual_ha_state_cloud_only_and_numeric_forecast_unchanged(tmp_path, cloud, now, expected):
    async def check():
        network = OfficialNetwork()
        # ECMWF has genuine numeric points across the synthetic daytime period.
        def response(url, **kwargs):
            result = network.get(url, **kwargs)
            if "/data/" in url:
                if cloud == "missing": result.body.pop("Total_cloud_cover")
                else:
                    for row in result.body["Total_cloud_cover"]["data"]: row[2] = cloud
            return result
        async with isolated_hass(tmp_path, network) as hass:
            with patch("aiohttp.ClientSession.get", side_effect=response), patch("homeassistant.util.dt.utcnow", return_value=now):
                result = await add_via_flow(hass, "ecmwf")
                entry = result["result"]
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
                state = hass.states.get(entity_id)
                assert state.state == expected
                assert state.attributes["condition_source"] == "forecast_model"
                assert state.attributes["condition_method"] == "cloud_cover_oktas"
                points = (await forecasts(hass, entity_id))[entity_id]["forecast"]
                assert all("condition" not in point for point in points)
                assert points[0]["temperature"] == (31 if now.hour == 0 else 34)
                network.error = TimeoutError()
                await entry.runtime_data.async_refresh()
                assert hass.states.get(entity_id).state == "unavailable"
                await assert_no_forecast(hass, entity_id)
                network.error = None
                await entry.runtime_data.async_refresh()
                assert hass.states.get(entity_id).state == expected
                with patch("homeassistant.util.dt.utcnow", return_value=now+timedelta(hours=37)):
                    await entry.runtime_data.async_refresh()
                    assert hass.states.get(entity_id).state == "unavailable"
    asyncio.run(check())


def test_overage_cloud_point_does_not_describe_current_weather(tmp_path):
    async def check():
        network = OfficialNetwork()
        def response(url, **kwargs):
            result = network.get(url, **kwargs)
            if "/data/" in url:
                for row in result.body["Total_cloud_cover"]["data"]: row[2] = 0
                result.body["Air_temperature_at_2m"]["data"][3][2] = None
            return result
        async with isolated_hass(tmp_path, network) as hass:
            with patch("aiohttp.ClientSession.get", side_effect=response), patch("homeassistant.util.dt.utcnow", return_value=datetime(2030,1,1,12,30,tzinfo=UTC)):
                result = await add_via_flow(hass, "ecmwf")
                entry = result["result"]
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
                assert hass.states.get(entity_id).state == "unknown"
                assert (await forecasts(hass, entity_id))[entity_id]["forecast"][0]["temperature"] == 34
    asyncio.run(check())


@pytest.mark.parametrize("event,before,after", [(SUN_EVENT_SUNRISE,"clear-night","sunny"),(SUN_EVENT_SUNSET,"sunny","clear-night")])
def test_native_solar_timer_updates_without_http_and_unloads_cleanly(tmp_path, caplog, event, before, after):
    async def check():
        network = OfficialNetwork()
        async with isolated_hass(tmp_path, network) as hass:
            sunrise = get_astral_event_next(hass, event, datetime(2030,1,1,tzinfo=UTC))
            clock = [sunrise-timedelta(milliseconds=20)]
            def response(url, **kwargs):
                result = network.get(url, **kwargs)
                if "/data/" in url:
                    for row in result.body["Total_cloud_cover"]["data"]: row[2] = 0
                    row = result.body["Air_temperature_at_2m"]["data"][-1].copy()
                    row[0] += 6*3600
                    result.body["Air_temperature_at_2m"]["data"].append(row)
                return result
            with patch("aiohttp.ClientSession.get", side_effect=response), patch("homeassistant.util.dt.utcnow", side_effect=lambda: clock[0]), patch("homeassistant.helpers.event.time.time", side_effect=lambda: clock[0].timestamp()), patch("homeassistant.helpers.event.time_tracker_timestamp", side_effect=lambda: clock[0].timestamp()):
                result = await add_via_flow(hass, "ecmwf")
                entry = result["result"]
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
                assert hass.states.get(entity_id).state == before
                calls = len(network.calls)
                clock[0] = sunrise+timedelta(microseconds=1)
                await asyncio.sleep(0.05)
                await hass.async_block_till_done()
                assert hass.states.get(entity_id).state == after
                assert len(network.calls) == calls
                coordinator = entry.runtime_data
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert not coordinator._listeners
                clock[0] += timedelta(days=1)
                await asyncio.sleep(0.05)
                await hass.async_block_till_done()
                assert hass.states.get(entity_id).state == "unavailable"
                assert len(network.calls) == calls
                assert not [r for r in caplog.records if r.levelno >= 40]
    asyncio.run(check())
