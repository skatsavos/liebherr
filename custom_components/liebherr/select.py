"""Support for Liebherr mode selections."""

import asyncio
import logging
from typing import Callable

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import ModeControlRequest, IceMakerControlRequest

_LOGGER = logging.getLogger(__name__)

# Control configuration
SELECT_CONFIG: dict[str, dict] = {
    "biofreshplus": {
        "icon": "mdi:leaf",
        "options": None,
        "attr": "currentMode",
    },
    "hydrobreeze": {
        "icon": "mdi:water",
        "options": ["OFF", "LOW", "MEDIUM", "HIGH"],
        "attr": "currentMode",
    },
    "IceMakerControl": {
        "icon": "mdi:cube-outline",
        "options": lambda ctrl: ["OFF", "ON", "MAX_ICE"] if ctrl.get("hasMaxIce") else ["OFF", "ON"],
        "attr": "iceMakerMode",
    },
}


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities
):
    """Set up Liebherr selects from a config entry."""
    api = hass.data[DOMAIN][config_entry.entry_id]["api"]
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    appliances = await api.get_appliances()
    entities = []

    for appliance in appliances:
        controls = await api.get_controls(appliance["deviceId"])
        if not controls:
            _LOGGER.warning("No controls found for appliance %s", appliance["deviceId"])
            continue

        for control in controls:
            ctrl_id = control.get("identifier", control["type"])
            if ctrl_id in SELECT_CONFIG:
                entities.append(LiebherrSelect(api, coordinator, appliance, control))

    async_add_entities(entities)


class LiebherrSelect(SelectEntity):
    """Representation of a Liebherr select entity."""

    def __init__(self, api, coordinator, appliance, control) -> None:
        """Initialize the select entity."""
        self._api = api
        self._coordinator = coordinator
        self._appliance = appliance
        self._control = control

        self._identifier = control.get("identifier", control["type"])
        self._device_id = appliance["deviceId"]

        nickname = appliance.get("nickname", "Liebherr")
        self._attr_name = f"{nickname} {self._identifier}"
        self._attr_unique_id = f"{self._device_id}_{self._identifier}"

        config = SELECT_CONFIG.get(self._identifier, {})
        self._attr_icon = config.get("icon")
        self._state_attr_key = config.get("attr", "currentMode")

        options = config.get("options")
        if callable(options):
            self._attr_options = options(control)
        elif options is not None:
            self._attr_options = options
        else:
            self._attr_options = control.get("supportedModes", [])

    @property
    def device_info(self):
        """Return device information for the select."""
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._appliance.get("nickname", f"Liebherr {self._device_id}"),
            "manufacturer": "Liebherr",
            "model": self._appliance.get("model", self._appliance["model"]),
            "sw_version": self._appliance.get("softwareVersion", ""),
        }

    def _get_control_from_coordinator(self):
        """Return the current control from the coordinator data."""
        if not self._coordinator.data:
            _LOGGER.error("Coordinator data is empty")
            return None

        for device in self._coordinator.data.get("appliances", []):
            if device.get("deviceId") != self._device_id:
                continue
            for control in device.get("controls", []):
                if control.get("identifier", control["type"]) == self._identifier:
                    return control
        return None

    @property
    def current_option(self):
        """Return the current selected option."""
        control = self._get_control_from_coordinator()
        if not control:
            return None
        return control.get(self._state_attr_key)

    async def async_select_option(self, option: str):
        """Change the selected option."""
        if option not in self._attr_options:
            _LOGGER.error("Invalid option selected: %s", option)
            return

        try:
            if self._control["type"] == "IceMakerControl":
                data = IceMakerControlRequest(
                    zoneId=self._control.get("zoneId"),
                    iceMakerMode=option,
                )
            else:
                data = ModeControlRequest(mode=option)

            await self._api.set_value(
                self._device_id,
                self._control["name"],
                data,
            )

            # Wait a bit for the fridge to apply changes before refreshing
            await asyncio.sleep(5)

        except Exception as e:
            _LOGGER.error("Failed to set option '%s' for '%s': %s", option, self._identifier, e)
            return

        await self._coordinator.async_request_refresh()
