"""Public packaging boundary tests against the real HA loader."""
import asyncio
from pathlib import Path
import shutil
import subprocess
import json

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration, async_setup

ROOT = Path(__file__).resolve().parents[1]


def test_discovery_and_private_boundary(tmp_path):
    # A missing/incorrect manifest or accidentally published notes must fail.
    assert (ROOT / "custom_components/shmu_forecast/manifest.json").exists()
    shutil.copytree(ROOT / "custom_components", tmp_path / "custom_components")

    async def check():
        hass = HomeAssistant(str(tmp_path))
        hass.config.skip_pip = True
        async_setup(hass)
        try:
            integration = await async_get_integration(hass, "shmu_forecast")
            assert integration.domain == "shmu_forecast"
            assert integration.version == "0.1.1"
        finally:
            await hass.async_stop()
    asyncio.run(check())
    for private in ("STATE.md", "decisions.md", "progress.md", "research/a.json",
                    "skill-notes/a.md", "evidence/a.json", ".env", "afk-cert.json",
                    "deployment/a.json", "credentials.json", "BUILD-REPORT.md",
                    "field-notes.md", "SPEC.md", "PLAN.md", "RESUME.md", "RESULT.md",
                    "build-final-tests.log", "test-runtime-x/lib/a", "test-cache-x/a", "deploy-local.sh"):
        result = subprocess.run(["git", "check-ignore", private], cwd=ROOT, capture_output=True)
        assert result.returncode == 0, private


def test_hacs_metadata_and_complete_flow_translations():
    component = ROOT / "custom_components/shmu_forecast"
    manifest = json.loads((component / "manifest.json").read_text())
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []
    assert manifest["codeowners"] == ["@PeterPalenik"]
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["integration_type"] == "service"
    assert manifest["version"] == "0.1.1"
    assert {p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir() and not p.name.startswith("__")} == {"shmu_forecast"}
    assert (ROOT / "hacs.json").exists()
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"] == "SHMU Forecast" and hacs["render_readme"] is True
    assert hacs["homeassistant"] == "2026.10.2"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    slovak = json.loads((component / "translations/sk.json").read_text())
    def paths(value, prefix=""):
        return {prefix} if not isinstance(value, dict) else {p for k,v in value.items() for p in paths(v, prefix+"/"+k)}
    assert strings == english
    assert paths(english) == paths(slovak)
    for section, step in (("config", "user"), ("options", "init")):
        assert set(english[section]["step"][step]["data"]) == {"station_id", "model", "max_run_age_hours"}
        assert {"cannot_connect", "no_forecast", "invalid_selection", "already_configured"} <= set(english[section]["error"])
        assert {"cannot_connect", "already_configured"} <= set(english[section]["abort"])
    assert (ROOT / "LICENSE").exists()
    assert (ROOT / "README.md").exists()
    assert (ROOT / ".github/workflows/validate.yml").exists()


def test_ha_loads_english_and_slovak_flow_text(tmp_path):
    from homeassistant.helpers.translation import async_get_translations
    from tests.ha_helpers import OfficialNetwork, isolated_hass
    async def check():
        async with isolated_hass(tmp_path, OfficialNetwork()) as hass:
            english = await async_get_translations(hass, "en", "config", {"shmu_forecast"})
            slovak = await async_get_translations(hass, "sk", "config", {"shmu_forecast"})
            key = "component.shmu_forecast.config.step.user.description"
            assert "model forecasts, not observations" in english[key]
            assert "Údaje sú modelová predpoveď, nie meranie." in slovak[key]
            assert "momentálne nie je dostupná platná predpoveď" in slovak["component.shmu_forecast.config.error.no_forecast"]
    asyncio.run(check())
