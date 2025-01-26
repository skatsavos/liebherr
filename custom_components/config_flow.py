from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol

from .const import DOMAIN


@callback
def configured_instances(hass):
    return {
        entry.data["username"] for entry in hass.config_entries.async_entries(DOMAIN)
    }


class LiebherrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            # Test credentials (optional)
            # api = LiebherrAPI(...)
            # success = await api.test_auth()
            success = True
            if success:
                return self.async_create_entry(
                    title="Liebherr Account", data=user_input
                )
            else:
                errors["base"] = "auth_failed"

        data_schema = vol.Schema(
            {
                vol.Required("username"): str,
                vol.Required("password"): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
