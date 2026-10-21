"""Parser contract: genuine selected identity, native times and explicit medians."""
from datetime import UTC, datetime
import importlib
import asyncio
import pytest
from tests.ha_helpers import Response
from tests.fixtures import products, STATIONS
from tests.fixtures import RUN, payload


def api():
    # Missing implementation is an assertion failure, not a collection failure.
    assert importlib.util.find_spec("custom_components.shmu_forecast.api") is not None
    return importlib.import_module("custom_components.shmu_forecast.api")


@pytest.mark.parametrize("model,expected,steps", [("aladin", 10, (1.0,)), ("alaef", 20, (1.0,)), ("ecmwf", 30, (3.0, 6.0))])
def test_selected_model_native_values_and_intervals(model, expected, steps):
    snapshot = api().parse_forecast(payload(model), model=model, station_id="99901", runtime=RUN, source_url="https://www.shmu.sk/data/datanwp/json/synthetic.json")
    assert snapshot.selected_model == model
    assert snapshot.records[0]["native_temperature"] == expected
    assert snapshot.records[1]["datetime"] == ("2030-01-01T03:00:00+00:00" if model == "ecmwf" else "2030-01-01T01:00:00+00:00")
    assert snapshot.model_run == datetime(2030, 1, 1, tzinfo=UTC)
    assert snapshot.forecast_steps_hours == steps
    assert snapshot.precipitation_interval_hours == (6 if model == "ecmwf" else 1)
    assert "cloud_coverage" not in snapshot.records[0]
    if model == "ecmwf":
        assert "native_precipitation" not in snapshot.records[1]
        assert snapshot.records[2]["native_precipitation"] == 2.5
    assert ("wind_bearing" in snapshot.records[0]) == (model == "aladin")


@pytest.mark.parametrize("mutation", ["station", "model", "run", "unit", "columns", "row", "empty", "timestamp", "unsupported"])
def test_rejects_invalid_selected_identity_and_required_schema(mutation):
    module = api()
    data = payload()
    model = "alaef"
    if mutation == "station": data["si_id"] = 99902
    if mutation == "model": data["model_name"] = "ECMWF ENS 9km 137L"
    if mutation == "run": data["data_date_time"] = "2029-12-31T12:00Z"
    if mutation == "unit": data["Air_temperature_at_2m"]["unit"] = "K"
    if mutation == "columns": data["Air_temperature_at_2m"]["columns"] = ["Time", "Median", "Median", "Minimum"]
    if mutation == "row": data["Air_temperature_at_2m"]["data"][0] = [RUN]
    if mutation == "empty": data.pop("Air_temperature_at_2m")
    if mutation == "timestamp": data["Air_temperature_at_2m"]["data"][0][0] = True
    if mutation == "unsupported": model = "fictional"
    with pytest.raises(module.ShmuError):
        module.parse_forecast(data, model=model, station_id="99901", runtime=RUN, source_url="synthetic")


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), "12"])
def test_invalid_numbers_never_zero_filled(bad):
    module = api()
    data = payload()
    data["Air_temperature_at_2m"]["data"][1][2] = bad
    data["Wind_speed_at_10m"]["unit"] = "knots"
    snapshot = module.parse_forecast(data, model="alaef", station_id="99901", runtime=RUN, source_url="synthetic")
    assert len(snapshot.records) == 4
    assert all(record["datetime"] != "2030-01-01T01:00:00+00:00" for record in snapshot.records)
    assert all("native_wind_speed" not in record for record in snapshot.records)
    assert "Wind_speed_at_10m:unit" in snapshot.omitted_fields


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return response


def test_client_catalog_and_newest_typed_run_with_bounded_publication_fallback():
    module = api()
    assert hasattr(module, "ShmuClient")
    catalog = products("alaef", (RUN - 43200, RUN, RUN - 86400))
    catalog["data"].insert(0, products("ecmwf", (RUN + 43200,))["data"][0])
    session = Session([Response(STATIONS), Response(catalog), Response({}, 404), Response(payload(runtime=RUN-43200))])
    async def check():
        client = module.ShmuClient(session)
        assert (await client.async_stations())[0]["station_id"] == "99901"
        snapshot = await client.async_forecast("99901", "alaef")
        assert snapshot.model_run == datetime(2029, 12, 31, 12, tzinfo=UTC)
        assert snapshot.source_url.endswith("a-laef/2029-12-31/99901_2029-12-31_12.json")
    asyncio.run(check())
    assert len(session.calls) == 4
    assert session.calls[0][0] == "https://www.shmu.sk/api/v1/nwp/getjsonaladinstations"
    assert session.calls[1][1]["params"] == {"station": "99901"}
    assert session.calls[2][0].endswith("a-laef/2030-01-01/99901_2030-01-01_00.json")
    assert all(call[1]["timeout"].total <= 20 and call[1]["allow_redirects"] is False for call in session.calls)


@pytest.mark.parametrize("path", ["https://evil.example/a.json", "../a.json", "a-laef/%2e%2e/a.json", "a-laef/2030-01-01/../../a.json", "a-laef\\a.json", "a-laef/a.json?q=1", "a-laef/a.json#x", "ecmwf/2030-01-01/99901_2030-01-01_00.json"])
def test_client_rejects_unsafe_advertised_paths_before_download(path):
    module = api()
    assert hasattr(module, "ShmuClient")
    catalog = products()
    catalog["data"][0]["file_link"] = path
    session = Session([Response(catalog)])
    with pytest.raises(module.ShmuSchemaError):
        asyncio.run(module.ShmuClient(session).async_forecast("99901", "alaef"))
    assert len(session.calls) == 1


@pytest.mark.parametrize("failure", ["wrong_model", "wrong_run", "wrong_station", "schema", "429", "500", "timeout", "decode"])
def test_client_never_falls_back_after_nonpublication_failure(failure):
    module = api()
    assert hasattr(module, "ShmuClient")
    data = payload()
    if failure == "wrong_model": data = payload("ecmwf")
    if failure == "wrong_run": data = payload(runtime=RUN-43200)
    if failure == "wrong_station": data["si_id"] = 99902
    if failure == "schema": data["Air_temperature_at_2m"]["unit"] = "K"
    response = Response(data)
    if failure in ("429", "500"): response = Response({}, int(failure))
    if failure == "timeout": response = TimeoutError()
    if failure == "decode": response = Response(ValueError())
    session = Session([Response(products(runtimes=(RUN, RUN-43200))), response])
    with pytest.raises(module.ShmuError):
        asyncio.run(module.ShmuClient(session).async_forecast("99901", "alaef"))
    assert len(session.calls) == 2


def test_client_request_cap_unsupported_model_and_station_error():
    module = api()
    assert hasattr(module, "ShmuClient")
    session = Session([])
    with pytest.raises(module.ShmuSelectionError):
        asyncio.run(module.ShmuClient(session).async_forecast("99901", "not-real"))
    assert not session.calls
    session = Session([Response(products(runtimes=(RUN, RUN-43200, RUN-86400))), Response({}, 404), Response({})])
    with pytest.raises(module.ShmuPublicationError):
        asyncio.run(module.ShmuClient(session).async_forecast("99901", "alaef"))
    assert len(session.calls) == 3
    session = Session([Response({"error": "synthetic"})])
    with pytest.raises(module.ShmuSchemaError):
        asyncio.run(module.ShmuClient(session).async_stations())

