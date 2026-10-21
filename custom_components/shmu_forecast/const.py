"""Integration constants."""
DOMAIN = "shmu_forecast"
VERSION = "0.1.1"
MODELS = {"aladin": "ALADIN", "alaef": "A-LAEF (ALADIN ENS)", "ecmwf": "ECMWF ENS"}
MODEL_PATHS = {"aladin": "aladin", "alaef": "a-laef", "ecmwf": "ecmwf"}
ORIGIN = "https://www.shmu.sk"
API_ROOT = f"{ORIGIN}/api/v1/nwp/"
DATA_ROOT = f"{ORIGIN}/data/datanwp/json/"
DEFAULT_MAX_RUN_AGE_HOURS = 36
