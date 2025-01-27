from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
import logging
from .const import DOMAIN
import asyncio

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities
):
    """Set up Liebherr switches from a config entry."""
    api = hass.data[DOMAIN][config_entry.entry_id]["api"]
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    appliances = await api.get_appliances()

    entities = []
    for appliance in appliances:
        controls = await api.get_controls(appliance["deviceId"])
        if not controls:
            _LOGGER.warning("No controls found for appliance %s",
                            appliance["deviceId"])
            continue

        for control in controls:
            if control["controlType"] in ("toggle", "icemaker", "bottletimer"):
                entities.append(LiebherrSwitch(
                    api, coordinator, appliance, control))

    if not entities:
        _LOGGER.error("No switch entities created")

    async_add_entities(entities)


class LiebherrSwitch(SwitchEntity):
    """Representation of a Liebherr switch entity."""

    def __init__(self, api, coordinator, appliance, control) -> None:
        """Initialize the switch entity."""
        self._api = api
        self._coordinator = coordinator
        self._appliance = appliance
        self._control = control
        self._identifier = control.get("identifier", control["controlType"])
        self._attr_name = f"{appliance['nickname']} {self._identifier}"
        self._attr_unique_id = f"{appliance['deviceId']}_{self._identifier}"

    @property
    def device_info(self):
        """Return device information for the switch."""
        return {
            "identifiers": {(DOMAIN, self._appliance["deviceId"])},
            "name": self._appliance.get(
                "nickname", f"Liebherr Device {self._appliance['deviceId']}"
            ),
            "manufacturer": "Liebherr",
            "model": self._appliance.get("model", self._appliance["model"]),
            "sw_version": self._appliance.get("softwareVersion", ""),
            "configuration_url": self._appliance.get("image", ""),
        }

    @property
    def is_on(self):
        """Return true if the switch is on."""
        if not self._coordinator.data:
            _LOGGER.error("Coordinator data is empty")
            return False

        controls = []
        for device in self._coordinator.data:
            if device.get("deviceId") == self._appliance["deviceId"]:
                controls = device.get("controls", [])
                for control in controls:
                    if (
                        control.get("identifier", control["controlType"])
                        == self._identifier
                    ):
                        return control.get("active", False)
        return False

    @property
    def available(self):
        """Return True if the switch is available."""
        return self._appliance["available"]

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        if self._control["controlType"] == "icemaker":
            await self._api.set_value(
                self._appliance["deviceId"] + "/" + self._control["endpoint"],
                {"iceMakerMode": "ON"},
            )
        if self._control["controlType"] == "bottletimer":
            await self._api.set_value(
                self._appliance["deviceId"] + "/" + self._control["endpoint"],
                {"bottleTimer": "ON"},
            )
        if self._control["controlType"] == "toggle":
            await self._api.set_active(
                self._appliance["deviceId"] + "/" +
                self._control["endpoint"], True
            )
        # TODO: autodoor, presentationlight, biofresh, hydrobreeze, biofreshplus
        await asyncio.sleep(5)
        await self._coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        if self._control["controlType"] == "icemaker":
            await self._api.set_value(
                self._appliance["deviceId"] + "/" + self._control["endpoint"],
                {"iceMakerMode": "OFF"},
            )
        if self._control["controlType"] == "bottletimer":
            await self._api.set_value(
                self._appliance["deviceId"] + "/" + self._control["endpoint"],
                {"bottleTimer": "OFF"},
            )
        if self._control["controlType"] == "toggle":
            await self._api.set_active(
                self._appliance["deviceId"] + "/" +
                self._control["endpoint"], False
            )
        await asyncio.sleep(5)
        await self._coordinator.async_request_refresh()
