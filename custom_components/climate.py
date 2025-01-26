from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import ATTR_TEMPERATURE
from datetime import timedelta
import logging
from .const import DOMAIN
from .switch import LiebherrSwitch

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up Liebherr appliances as devices and entities from a config entry."""
    api = hass.data[DOMAIN][config_entry.entry_id]["api"]
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    appliances = await api.get_appliances()
    entities = []

    for appliance in appliances:
        controls = await api.get_controls(appliance["deviceId"])
        if appliance["applianceType"] in [
            "FRIDGE",
            "FREEZER",
            "COMBI",
            "WINE",
        ]:
            entities.append(LiebherrClimate(coordinator, api, appliance, controls))

    async_add_entities(entities)


class LiebherrClimate(ClimateEntity):
    """Representation of a Liebherr climate entity."""

    def __init__(self, coordinator, api, appliance, controls):
        """Initialize the climate entity."""
        self.coordinator = coordinator
        self.api = api
        self._controls = controls
        self._appliance = appliance
        self._attr_name = appliance.get("nickname", appliance["deviceId"])
        self._attr_unique_id = appliance["deviceId"]
        self._attr_temperature_unit = "°C"
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        self._attr_hvac_modes = [HVACMode.COOL, HVACMode.OFF]
        self._attr_hvac_mode = HVACMode.COOL

    @property
    def device_info(self):
        """Return device information for the appliance."""
        return {
            "identifiers": {(DOMAIN, self._appliance["deviceId"])},
            "name": self._appliance.get("nickname", "Liebherr Appliance"),
            "manufacturer": "Liebherr",
            "model": self._appliance.get("model", "Unknown Model"),
            "sw_version": self._appliance.get("softwareVersion", ""),
            "configuration_url": self._appliance.get("image", ""),
        }

    async def async_set_temperature(self, **kwargs):
        """Set the target temperature."""
        if ATTR_TEMPERATURE in kwargs:
            temperature = kwargs[ATTR_TEMPERATURE]
            endpoint = f"{self._appliance['deviceId']}/zones/0/temperature"
            await self.api.set_temperature(endpoint, temperature)
            await self.coordinator.async_request_refresh()

    @property
    def target_temperature(self):
        """Return the target temperature."""
        controls = self._controls
        for control in controls:
            if control.get("controlType") == "temperature":
                return control.get("target")
        return None

    @property
    def min_temp(self):
        """Return the minimum temperature that can be set."""
        controls = self._controls
        for control in controls:
            if control.get("controlType") == "temperature":
                return control.get("min", -24)
        return -24

    @property
    def max_temp(self):
        """Return the maximum temperature that can be set."""
        controls = self._controls
        for control in controls:
            if control.get("controlType") == "temperature":
                return control.get("max", 15)
        return 15

    @property
    def current_temperature(self):
        """Return the current temperature."""
        for device in self.coordinator.data:
            if device.get("deviceId") == self._appliance["deviceId"]:
                controls = device.get("controls", [])
                for control in controls:
                    if control.get("controlType") == "temperature":
                        return control.get("current")
        return None

    @property
    def hvac_mode(self):
        """Return the current HVAC mode."""
        return self._attr_hvac_mode

    async def async_set_hvac_mode(self, hvac_mode):
        """Set the HVAC mode."""
        if hvac_mode in self._attr_hvac_modes:
            self._attr_hvac_mode = hvac_mode
            await self.coordinator.async_request_refresh()

    async def async_update(self):
        """Fetch the latest data from the API."""
        await self.coordinator.async_request_refresh()
