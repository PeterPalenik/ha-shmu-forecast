# SHMU Forecast

[![Validate](https://github.com/PeterPalenik/ha-shmu-forecast/actions/workflows/validate.yml/badge.svg)](https://github.com/PeterPalenik/ha-shmu-forecast/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An unofficial [Home Assistant](https://www.home-assistant.io/) integration that exposes Slovak Hydrometeorological Institute (SHMÚ) model forecasts as a native `weather` entity. Choose a location and one of three forecast products. Data is fetched directly from SHMÚ; no API key or third-party relay is used.

> This project is independent of SHMÚ and is not endorsed or operated by SHMÚ.

## Features

- UI-based setup and options; no YAML configuration
- Three selectable products: ALADIN, A-LAEF (ALADIN ENS) and ECMWF ENS
- Native subdaily forecast points, without daily aggregation or interpolation
- One forecast entity per location/model selection
- Direct requests to SHMÚ with bounded timeouts and strict response validation

## Installation

### HACS custom repository

1. In HACS, open **Integrations → Custom repositories**.
2. Add `https://github.com/PeterPalenik/ha-shmu-forecast` with category **Integration**.
3. Download **SHMU Forecast** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for **SHMU Forecast**, and follow the setup flow.

### Manual

1. Copy `custom_components/shmu_forecast/` into `<config>/custom_components/shmu_forecast/`.
2. Restart Home Assistant.
3. Add **SHMU Forecast** under **Settings → Devices & services → Add integration**.

## Configuration

Choose a location and model in the setup flow. The nearest catalog location to the configured Home Assistant home coordinates is an editable default; if coordinates are unavailable, select a location explicitly. Setup requires a valid future forecast for the selected location/model.

The **Maximum model run age** option defaults to 36 hours and accepts 1–168 hours. This is your freshness policy, not a promise about SHMÚ publication frequency. Use **Configure** on an entry to change its location, model or age limit. A location/model pair can be configured only once; add a different model for the same location to compare products.

## Entity and forecasts

Each entry creates one native `weather` entity named **Forecast**. Current fields are taken from the latest model point at or before now, not from station observations. The current `condition` is a local cloud-cover summary only; it does not infer rain, snow, fog, storms or severe weather. Day/night uses the Home Assistant home location.

Use Home Assistant's `weather.get_forecasts` action with `type: hourly` to retrieve future **native subdaily points**. Despite the action name, ECMWF retains its native 3-hour and 6-hour temperature steps; it does not interpolate hourly values. There is no daily forecast aggregation. Forecast fields can include temperature, precipitation, wind, gust, pressure and cloud cover when valid in the source data. ALADIN and A-LAEF precipitation values cover one-hour intervals; ECMWF precipitation values are six-hour accumulations reported at interval-end timestamps and omitted at intervening temperature points.

The entity exposes provenance attributes including selected model, model run, source URL, native forecast steps and precipitation interval. The source URL identifies the selected location; avoid sharing it as anonymous diagnostics.

## Availability and limitations

The integration refreshes every 30 minutes. It uses the selected product only and does not fall back to another model. Missing or expired data, a failed refresh, or invalid required data makes the entity unavailable; previous values are not presented as a fallback. The maximum run age is checked between refreshes as well as during refresh.

Forecast products and feed formats may change without notice. Strict validation can make the entity unavailable if SHMÚ changes its data. These are model forecasts, not observations, and must not be used as a safety-critical control source.

## Development

Use Python 3.14 in a disposable environment:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m pytest -q
```

The test extra pins Home Assistant 2026.10.2 and pytest 8.4.2. Tests use synthetic data and isolated temporary Home Assistant configuration; they do not call live household services. GitHub Actions runs the tests and Home Assistant's `hassfest` validator. HACS publication validation is a separate manual workflow and is not implied by those checks.

## Data source and license

Forecasts are fetched from the structured products advertised by [SHMÚ](https://www.shmu.sk/). This integration uses those feeds, not a guaranteed stable public API. SHMÚ owns its data and related third-party material; this project's MIT license covers only its authored code and synthetic test fixtures. No redistribution rights for SHMÚ material are asserted.

## Removal

Remove the integration entry under **Settings → Devices & services**. After removing all entries, delete `custom_components/shmu_forecast/` (or remove the integration through HACS) and restart Home Assistant. The integration does not modify other integrations, scripts or automations.
