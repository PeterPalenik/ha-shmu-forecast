"""Config/options acceptance through actual HA flow managers."""
import asyncio
from datetime import UTC, datetime
from unittest.mock import patch
import pytest
from homeassistant.data_entry_flow import InvalidData

from tests.ha_helpers import OfficialNetwork, isolated_hass, add_via_flow

NOW = datetime(2030, 1, 1, 0, 30, tzinfo=UTC)


def test_user_flow_catalog_pair_validation_and_duplicates(tmp_path):
    async def check():
        network = OfficialNetwork()
        async with isolated_hass(tmp_path, network) as hass:
            with patch("homeassistant.util.dt.utcnow", return_value=NOW):
                result = await hass.config_entries.flow.async_init("shmu_forecast", context={"source": "user"})
                assert result["type"] == "form"
                schema = result["data_schema"]
                station_key = next(k for k in schema.schema if k.schema == "station_id")
                assert station_key.default() == "99901"
                selector = schema.schema[station_key]
                assert selector.config["mode"] == "dropdown"
                assert selector.config["custom_value"] is False
                first = result["flow_id"]
                with pytest.raises(InvalidData):
                    await hass.config_entries.flow.async_configure(first, {"station_id": "99901", "model": "alaef", "max_run_age_hours": 0})
                result = await hass.config_entries.flow.async_configure(first, {"station_id": "99901", "model": "alaef", "max_run_age_hours": 36})
                assert result["type"] == "create_entry", result
                await hass.async_block_till_done()
                duplicate = await add_via_flow(hass)
                assert duplicate["type"] == "abort" and duplicate["reason"] == "already_configured"
                comparison = await add_via_flow(hass, "aladin")
                assert comparison["type"] == "create_entry", comparison
                assert len(hass.config_entries.async_entries("shmu_forecast")) == 2
                catalog_calls = [u for u in network.calls if "getjsonaladinstations" in u]
                assert len(catalog_calls) == 3  # one per flow; repeated first form didn't refetch
    asyncio.run(check())


def test_simultaneous_same_pair_cannot_create_duplicates(tmp_path):
    async def check():
        async with isolated_hass(tmp_path, OfficialNetwork()) as hass:
            with patch("homeassistant.util.dt.utcnow", return_value=NOW):
                results = await asyncio.gather(add_via_flow(hass), add_via_flow(hass))
                assert sorted(r["type"] for r in results) == ["abort", "create_entry"]
                assert len(hass.config_entries.async_entries("shmu_forecast")) == 1
    asyncio.run(check())


def test_options_validation_failure_collision_and_new_source_retry(tmp_path):
    from homeassistant.helpers import entity_registry as er
    from tests.test_weather import forecasts, assert_no_forecast
    async def check():
        network = OfficialNetwork()
        async with isolated_hass(tmp_path, network) as hass:
            with patch("homeassistant.util.dt.utcnow", return_value=NOW):
                result = await add_via_flow(hass)
                entry = result["result"]
                entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[0].entity_id
                old_coordinator = entry.runtime_data
                original = dict(entry.options)
                flow = await hass.config_entries.options.async_init(entry.entry_id)
                network.error = TimeoutError()
                failed = await hass.config_entries.options.async_configure(flow["flow_id"], {"station_id": "99901", "model": "ecmwf", "max_run_age_hours": 36})
                assert failed["errors"] == {"base": "cannot_connect"}
                assert dict(entry.options) == original
                assert hass.states.get(entity_id).attributes["selected_model"] == "alaef"
                network.error = None
                comparison = await add_via_flow(hass, "aladin")
                assert comparison["type"] == "create_entry"
                collision = await hass.config_entries.options.async_configure(flow["flow_id"], {"station_id": "99901", "model": "aladin", "max_run_age_hours": 36})
                assert collision["errors"] == {"base": "already_configured"}
                assert dict(entry.options) == original
                network.fail_after = network.data_requests + 1  # validate succeeds; new setup fails
                saved = await hass.config_entries.options.async_configure(flow["flow_id"], {"station_id": "99902", "model": "ecmwf", "max_run_age_hours": 36})
                assert saved["type"] == "create_entry"
                await hass.async_block_till_done()
                assert entry.options["model"] == "ecmwf" and entry.options["station_id"] == "99902"
                assert entry.state.value == "setup_retry"
                assert hass.states.get(entity_id).state == "unavailable"
                await assert_no_forecast(hass, entity_id)
                assert not old_coordinator._listeners
                assert not entry.update_listeners
                network.fail_after = None
                call_start = len(network.calls)
                # HA registers setup-retry on its native started event before startup.
                await hass.async_start()
                await hass.async_block_till_done(wait_background_tasks=True)
                assert entry.state.value == "loaded"
                states = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                assert len(states) == 1 and states[0].entity_id == entity_id
                assert states[0].unique_id == entry.entry_id
                assert hass.states.get(entity_id).attributes["selected_model"] == "ecmwf"
                assert "/ecmwf/" in hass.states.get(entity_id).attributes["source_url"]
                assert (await forecasts(hass, entity_id))[entity_id]["forecast"][0]["temperature"] == 31
                assert all("a-laef/" not in url for url in network.calls[call_start:])
                assert not entry.update_listeners
                reused_old_pair = await add_via_flow(hass)
                assert reused_old_pair["type"] == "create_entry"  # effective options, not stale entry.data
                blocked_new_pair = await add_via_flow(hass, "ecmwf", "99902")
                assert blocked_new_pair["type"] == "abort" and blocked_new_pair["reason"] == "already_configured"
                assert await hass.config_entries.async_unload(entry.entry_id)
    asyncio.run(check())
