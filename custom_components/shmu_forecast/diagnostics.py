"""Allowlisted diagnostics: no place, coordinates, entry IDs or source URLs."""
from .const import VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    data = coordinator.data
    return {"version": VERSION, "selected_model": coordinator.selection["model"],
            "records": len(data.records) if data else 0,
            "omitted_fields": list(data.omitted_fields) if data else [],
            "last_update_success": coordinator.last_update_success,
            "error_class": type(coordinator.last_exception).__name__ if coordinator.last_exception else None}
