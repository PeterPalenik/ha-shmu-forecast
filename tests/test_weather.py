"""Runtime acceptance at HA entry, state and weather service boundaries."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er

from tests.fixtures import RUN, payload, NAMES, PATHS
from tests.ha_helpers import OfficialNetwork, isolated_hass, add_via_flow

NOW = datetime(2030, 1, 1, 0, 30, tzinfo=UTC)


async def forecasts(hass, entity_id):
    return await hass.services.async_call("weather", "get_forecasts", {"entity_id": entity_id, "type": "hourly"}, blocking=True, return_response=True)


@pytest.mark.parametrize("model,temperature,next_time,steps", [("aladin", 11.0, "2030-01-01T01:00:00+00:00", [1.0]), ("alaef", 21.0, "2030-01-01T01:00:00+00:00", [1.0]), ("ecmwf", 31.0, "2030-01-01T03:00:00+00:00", [3.0,6.0])])
def test_add_integration_to_native_forecast_service(tmp_path, caplog, model, temperature, next_time, steps):
    async def check():
        async with isolated_hass(tmp_path, OfficialNetwork()) as hass:
            with patch("homeassistant.util.dt.utcnow", return_value=NOW):
                result = await add_via_flow(hass, model)
                assert result["type"] == "create_entry", result
                entry = result["result"]
                assert entry.state.value == "loaded"
                entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                assert len(entities) == 1
                entity_id = entities[0].entity_id
                assert entities[0].unique_id == entry.entry_id
                state = hass.states.get(entity_id)
                assert state.state != "unavailable"
                assert state.attributes["selected_model"] == model
                assert state.attributes["model_name"] == NAMES[model]
                assert state.attributes["model_run"] == "2030-01-01T00:00:00+00:00"
                assert state.attributes["source_url"] == f"https://www.shmu.sk/data/datanwp/json/{PATHS[model]}/2030-01-01/99901_2030-01-01_00.json"
                assert state.attributes["precipitation_interval_hours"] == (6 if model == "ecmwf" else 1)
                assert state.attributes["forecast_steps_hours"] == steps
                assert state.attributes["condition_source"] == "forecast_model"
                assert state.attributes["temperature"] == temperature-1
                response = await forecasts(hass, entity_id)
                points = response[entity_id]["forecast"]
                assert points[0]["temperature"] == temperature
                assert points[0]["datetime"] == next_time
                assert "humidity" not in points[0]
                if model == "ecmwf":
                    assert "precipitation" not in points[0]
                    assert points[1]["precipitation"] == 2.5
                coordinator = entry.runtime_data
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert hass.states.get(entity_id).state == "unavailable"
                assert not coordinator._listeners
                assert not entry.update_listeners
                assert not [r for r in caplog.records if r.levelno >= 40]
    asyncio.run(check())


@pytest.mark.parametrize("failure", ["transport", "identity", "schema"])
def test_refresh_failure_hides_last_good_data_then_recovers(tmp_path, failure):
    async def check():
        network = OfficialNetwork()
        async with isolated_hass(tmp_path, network) as hass:
            with patch("homeassistant.util.dt.utcnow", return_value=NOW):
                result = await add_via_flow(hass)
                entry = result["result"]
                coordinator = entry.runtime_data
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
                assert (await forecasts(hass, entity_id))[entity_id]["forecast"][0]["temperature"] == 21
                if failure == "transport":
                    network.error = TimeoutError()
                    await coordinator.async_refresh()
                else:
                    def corrupt(url, **kwargs):
                        from tests.ha_helpers import Response
                        response = network.get(url, **kwargs)
                        if "/data/" in url:
                            if failure == "identity": response.body["model_name"] = "ECMWF ENS 9km 137L"
                            else: response.body["Air_temperature_at_2m"]["unit"] = "K"
                        return response
                    with patch("aiohttp.ClientSession.get", side_effect=corrupt):
                        await coordinator.async_refresh()
                assert coordinator.last_update_success is False
                state = hass.states.get(entity_id)
                assert state.state == "unavailable"
                assert "source_url" not in state.attributes
                assert "temperature" not in state.attributes
                await assert_no_forecast(hass, entity_id)
                network.error = None
                await coordinator.async_refresh()
                assert coordinator.last_update_success is True
                assert hass.states.get(entity_id).attributes["selected_model"] == "alaef"
                assert (await forecasts(hass, entity_id))[entity_id]["forecast"][0]["temperature"] == 21
    asyncio.run(check())


async def assert_no_forecast(hass, entity_id):
    from homeassistant.exceptions import HomeAssistantError
    try:
        result = await forecasts(hass, entity_id)
    except HomeAssistantError:
        return
    assert not result[entity_id]["forecast"]


def test_clock_expiration_without_poll_clears_state_and_service(tmp_path):
    async def check():
        network = OfficialNetwork()
        clock = [datetime(2030, 1, 1, 0, 59, 59, 980000, tzinfo=UTC)]
        async with isolated_hass(tmp_path, network) as hass:
            with patch("homeassistant.util.dt.utcnow", side_effect=lambda: clock[0]), patch("homeassistant.helpers.event.time.time", side_effect=lambda: clock[0].timestamp()), patch("homeassistant.helpers.event.time_tracker_timestamp", side_effect=lambda: clock[0].timestamp()):
                result = await add_via_flow(hass, age=1)
                entry = result["result"]
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
                assert hass.states.get(entity_id).state != "unavailable"
                calls = len(network.calls)
                clock[0] = datetime(2030, 1, 1, 1, 0, 0, 1, tzinfo=UTC)
                await asyncio.sleep(0.05)  # actual native HA timer, accelerated wall clock boundary
                await hass.async_block_till_done()
                assert len(network.calls) == calls
                assert hass.states.get(entity_id).state == "unavailable"
                await assert_no_forecast(hass, entity_id)
    asyncio.run(check())


def test_current_values_never_use_future_or_overage_model_point(tmp_path):
    from tests.ha_helpers import Response
    async def check():
        network = OfficialNetwork()
        async with isolated_hass(tmp_path, network) as hass:
            def missing_temperature(url, **kwargs):
                response = network.get(url, **kwargs)
                if "/data/" in url:
                    response.body["Air_temperature_at_2m"]["data"][1][2] = None
                return response
            with patch("aiohttp.ClientSession.get", side_effect=missing_temperature), patch("homeassistant.util.dt.utcnow", return_value=datetime(2030,1,1,1,30,tzinfo=UTC)):
                result = await add_via_flow(hass)
                entry = result["result"]
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
                assert "temperature" not in hass.states.get(entity_id).attributes
                assert "pressure" not in hass.states.get(entity_id).attributes
                assert (await forecasts(hass, entity_id))[entity_id]["forecast"][0]["temperature"] == 22
    asyncio.run(check())


def test_diagnostics_only_structural_counts_no_location_or_identifiers(tmp_path):
    import importlib
    async def check():
        async with isolated_hass(tmp_path, OfficialNetwork()) as hass:
            with patch("homeassistant.util.dt.utcnow", return_value=NOW):
                result = await add_via_flow(hass)
                entry = result["result"]
                assert importlib.util.find_spec("custom_components.shmu_forecast.diagnostics") is not None
                module = importlib.import_module("custom_components.shmu_forecast.diagnostics")
                data = await module.async_get_config_entry_diagnostics(hass, entry)
                assert data == {"version": "0.1.1", "selected_model": "alaef", "records": 5, "omitted_fields": [], "last_update_success": True, "error_class": None}
                text = str(data)
                assert "99901" not in text and "Synthetic" not in text and entry.entry_id not in text
    asyncio.run(check())


@pytest.mark.parametrize("hours,age,available", [(0.999,1,True), (1,1,True), (1.001,1,False), (2,3,True), (4,36,False)])
def test_freshness_deadline_custom_policy_and_no_future(tmp_path, hours, age, available):
    async def check():
        async with isolated_hass(tmp_path, OfficialNetwork()) as hass:
            with patch("homeassistant.util.dt.utcnow", return_value=NOW):
                result = await add_via_flow(hass, age=age)
                entry = result["result"]
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
            with patch("homeassistant.util.dt.utcnow", return_value=datetime.fromtimestamp(RUN, UTC)+timedelta(hours=hours)):
                if available:
                    response = await forecasts(hass, entity_id)
                    assert response[entity_id]["forecast"]
                else:
                    await assert_no_forecast(hass, entity_id)
    asyncio.run(check())
