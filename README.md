# SHMU Forecast

Unofficial Home Assistant integration for model forecasts from the Slovak
Hydrometeorological Institute (SHMÚ). This project is not owned, endorsed or
operated by SHMÚ. Forecast data are fetched directly from **www.shmu.sk**; no
third-party relay, API key, geocoding service or GRIB decoder is used.

**Status:** version 0.1.1, tested locally with Home Assistant **2026.10.2** and
Python **3.14**. The intended repository identity is
`PeterPalenik/ha-shmu-forecast`. Remote publication, remote CI, HACS installation
and default-store admission have **not** been verified. Metadata links name
that intended repository and may not resolve until it is published.

## Installation

### Manual installation (available from a local checkout)

1. Copy only `custom_components/shmu_forecast/` into your Home Assistant
   configuration directory's `custom_components/` directory.
2. Restart Home Assistant normally so its integration loader discovers the new
   domain. Follow your installation's restart/safety procedure.
3. In **Settings → Devices & services → Add integration**, search for
   **SHMU Forecast**.
4. Choose a location, model and maximum run age, then submit. A valid future
   forecast for that exact pair must be available before the entry is created.

### HACS custom repository (after authorized public publication)

Once the intended repository actually exists publicly, add its GitHub URL in
HACS **Custom repositories**, category **Integration**, download SHMU Forecast,
restart Home Assistant and follow steps 3–4 above. The repository layout and
metadata are prepared for this path; this is **not a claim that remote HACS
installation has passed**, nor that the integration is in the default store.
Public repository metadata, the applicable HACS checklist and a
`home-assistant/brands` entry still require publication-stage verification.

## Configuration and model comparison

The location dropdown is searchable. Its editable default is the nearest
catalog location to the configured Home Assistant coordinates, calculated
locally. With invalid/missing coordinates, choose a location explicitly.
The catalog is refreshed for each new config/options flow and reused only
inside that flow. No device discovery or physical actions are performed.

Available official product types:

| Selection | Source payload | Summary and native time steps |
|---|---|---|
| ALADIN (`aladin`) | ALADIN SHMU/SK | Deterministic values; observed hourly temperature steps, approximately 72-hour source horizon |
| A-LAEF / ALADIN ENS (`alaef`, default) | ALADIN ENS | Explicit `Median` column; observed hourly temperature steps, approximately 72-hour source horizon |
| ECMWF ENS (`ecmwf`) | ECMWF ENS | Explicit `Median` column; observed 3-hour then 6-hour temperature steps over approximately ten days |

These describe the inspected feed, not a guaranteed SHMÚ contract or universal
location coverage. The selected product's availability is checked each time.
The integration never substitutes another product. Add multiple entries with
different models for the same location to compare them. The same model/location
pair cannot be added twice, including through options.

Use **Configure** on an entry to change its model, location or freshness policy.
A failed validation leaves the previous saved selection intact. After a valid
selection is saved, it is authoritative: HA unloads the old source and reloads
the new one. A subsequent failure leaves the entry unavailable/in native
setup-retry; it does **not** restore the previous model. Recovery uses only the
new saved selection and the same entity registry identity. No `.storage` edits
are needed. An unload failure fails closed and must be investigated.

## Forecast semantics

Each entry exposes one native `weather` entity, named **Forecast**. Its current
fields are the nearest model point **at or before now**, not station
observations. A point older than the largest native temperature step supplies
no current values. A future point is never presented as a current observation.
`condition_source: forecast_model` makes this distinction explicit. The current
condition summarizes **cloudiness only**, using valid `Total_cloud_cover` (the
explicit Median for ensembles). Percent cloud is rounded to the nearest eighth
(okta), with HALF-UP ties: `floor(percent * 8 / 100 + 0.5)`. The
[WMO observed-sky bands](https://worldweather.wmo.int/oktas.htm) are used as an
analogy: 0–2 oktas becomes `sunny` by day or `clear-night` at night, 3–5 becomes
`partlycloudy`, and 6–8 becomes `cloudy`. Exact transitions are 31.25% and 68.75%.
This is a local interpretation, **not an official SHMÚ symbol, a comprehensive
weather condition or a validated forecast-accuracy threshold**.

Day/night uses Home Assistant's native sun helper at the **configured HA home
location**, not at the selected forecast grid point. State updates at native
sunrise/sunset without an HTTP poll; the existing state timer is cancelled on
unload. Missing, invalid or overage cloud stays `unknown`; a failed refresh or
expired run stays `unavailable`. `condition_method: cloud_cover_oktas` discloses
the interpretation. No rain, snow, fog, storm or severe-weather condition is
inferred from precipitation/snowfall interval totals. Numeric forecast service
records and their native timing are unchanged and contain no derived condition.

Use the native `weather.get_forecasts` action with `type: hourly` to obtain
**subdaily native steps**. For ECMWF this does not mean hourly interpolation:
3-hour and 6-hour temperature points are retained exactly. ECMWF precipitation
is a **six-hour accumulation at the actual interval-end timestamp**; it is
omitted at intervening temperature points, never divided, duplicated or
zero-filled. ALADIN and A-LAEF precipitation uses one-hour source intervals.
There is no daily forecast aggregation.

Temperature is °C, wind/gust m/s, pressure hPa, cloud coverage %, precipitation
mm. HA converts native units to its configured units. Missing, null, boolean or
nonfinite values are not zero. Optional fields with unknown units/schema are
omitted with a bounded diagnostic reason. Unknown required temperature units,
malformed required rows, or station/model/run identity mismatches fail the
entire selected snapshot. Ensemble wind-direction frequency bins are omitted;
they are not degree bearings. Humidity is not invented.

Public provenance attributes (not forecast-array state attributes):

- `selected_model`, `model_name`
- `model_run` (UTC RFC3339), `source_url` (exact validated official source)
- `forecast_steps_hours`, `precipitation_interval_hours`

One snapshot is one model, station and run. Forecast service rows contain only
standard HA forecast fields and UTC validity times. State provenance contains
the selected location's identifying source URL; do not share it as anonymous
diagnostics. Downloadable integration diagnostics instead allowlist only
version, model, structural counts and error class; no location, coordinates,
entry IDs or raw payloads.

## Refresh and unavailable behavior

The coordinator polls every **30 minutes**. Each refresh fetches fresh product
metadata and at most two catalog-advertised data files. Only a missing
publication/HTTP 404 permits trying the next advertised run of the **same**
product. There are no guessed dates, persistent catalog caches or model
fallbacks. HTTP requests time out after at most 20 seconds each. HTTP 429,
other HTTP errors, decode failures and schema/identity errors are surfaced
without request loops; HA retries on its normal coordinator/setup schedule.

**Maximum model run age** is an operator policy: default **36 hours** for all
products, configurable as an integer **1–168 hours**. It is not a claim about
SHMÚ publication frequency. A run is usable at the exact age deadline and
expires immediately afterwards; a run dated in the future is invalid. Having
no remaining future temperatures independently makes the entity unavailable.
Availability and forecast filtering use current UTC even between HTTP polls;
a local HA timer updates state at relevant clock boundaries.

Any refresh failure immediately makes the entity unavailable and exposes no
last-good current values, usable provenance or forecasts, even within the age
limit. The last snapshot may remain internally for structural diagnostics,
never as public fallback data. Successful selected-source refresh restores
availability. Do not use this integration as a safety-critical control source.

## Development and validation

Use a disposable environment with Python 3.14:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m pytest -q
```

The test extra pins `homeassistant==2026.10.2` and `pytest==8.4.2`. Tests use
synthetic schema-faithful locations/data, isolated temporary HA configuration
and storage, actual HA config/options managers, entity registry, setup/unload
and `weather.get_forecasts`. Only external HTTP/DNS and clock boundaries are
controlled. No fake Home Assistant classes or live household services are
used. Upstream HA/backoff deprecation warnings may be emitted under Python
3.14; they are not suppressed to hide failures.

GitHub CI is prepared for pinned-runtime pytest and hassfest, but has not run
remotely. The manual workflow-dispatch HACS publication check deliberately
requires a real public repository and brands entry; it is not replaced with
an ignored/masked success. No local Ruff validation is claimed.

## Source contract and attribution

Official source: [SHMÚ](https://www.shmu.sk/), its meteogram station catalog,
`/api/v1/nwp/getstationproducts` and advertised relative files under
`/data/datanwp/json/`. These are the structured feeds used by the official
meteogram client, not a promised stable public API. SHMÚ can change schema,
product labels, units or availability without notice; strict validation may
then make this integration unavailable until updated.

The MIT license covers this project's authored code and synthetic test
fixtures only. No license or redistribution rights are asserted for SHMÚ
forecast data, names or other third-party material. Actual downloaded forecast
evidence is not bundled.

## Removal

Remove each SHMU Forecast entry in **Settings → Devices & services**. After
removing all entries, delete only `custom_components/shmu_forecast/` (or remove
it through HACS after an actual HACS install) and restart HA normally. Existing
SHMÚ scripts, other integrations and automations are independent and are not
modified by this integration.
