"""Liebherr SmartDevice for HomeAssistant."""

import base64
from datetime import timedelta
import hashlib
import json
import logging
from pathlib import Path
import re
import secrets
import ssl
from urllib.parse import parse_qs

import aiofiles
from aiohttp import ClientSession, TCPConnector
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.dt import as_local, parse_datetime

# from homeassistant.core import __all__
from .const import (
    AIR_FILTER,
    BASE_API_URL,
    BASE_URL,
    CLIENT_ID,
    DOMAIN,
    DOOR_ALARM,
    DOOR_OVERHEAT_ALARM,
    OBSTACLE_ALARM,
    POWER_FAILURE_ALARM,
    REDIRECT_URI,
    TEMPERATURE_ALARM,
    TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)
_DEBUG = False


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Set up Liebherr devices from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    api = LiebherrAPI(hass, config_entry.data)
    api.translations = await api.load_translations()

    ssl_context = await hass.async_add_executor_job(ssl.create_default_context)
    # DEBUG
    # ssl_context.check_hostname = False
    # ssl_context.verify_mode = ssl.CERT_NONE
    # api.connector = TCPConnector(ssl=ssl_context)
    ###
    api.connector = TCPConnector(ssl=ssl_context)

    api.session = ClientSession(connector=api.connector)

    try:
        await api.authenticate()
    except LiebherrAuthException as e:
        _LOGGER.error("Failed to authenticate: %s", e)
        return False

    async def async_update_method():
        """Fetch both appliances and notifications."""
        try:
            # Geräte abrufen
            appliances = await api.get_appliances()

            # Benachrichtigungen abrufen
            filtered_notifications = await api.fetch_notifications(config_entry)

            # Kombinierte Daten zurückgeben
            combined_data = {
                "appliances": appliances,
                "notifications": filtered_notifications,
            }
        except LiebherrUpdateException as e:
            raise LiebherrUpdateException(f"Error updating Liebherr data: {e}") from e
        else:
            return combined_data

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Liebherr devices",
        update_method=async_update_method,
        update_interval=timedelta(
            seconds=config_entry.options.get("update_interval", 30)
        ),
    )

    hass.data[DOMAIN][config_entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await coordinator.async_refresh()

    if not coordinator.data:
        _LOGGER.warning("No initial data retrieved from Liebherr API")

    await hass.config_entries.async_forward_entry_setups(
        config_entry, ["climate", "switch", "select", "sensor", "cover"]
    )
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload a config entry."""
    await hass.config_entries.async_forward_entry_unload(config_entry, "climate")
    await hass.config_entries.async_forward_entry_unload(config_entry, "switch")
    await hass.config_entries.async_forward_entry_unload(config_entry, "select")
    await hass.config_entries.async_forward_entry_unload(config_entry, "sensor")
    await hass.config_entries.async_forward_entry_unload(config_entry, "cover")
    hass.data[DOMAIN].pop(config_entry.entry_id)
    return True


class LiebherrAuthException(Exception):
    """Exception raised for authentication errors in the Liebherr API."""


class LiebherrUpdateException(Exception):
    """Exception raised for update methodes in the Liebherr API."""


class LiebherrFetchException(Exception):
    """Exception raised for fetching data in the Liebherr API."""


class LiebherrException(Exception):
    """Exception raised for errors in the Liebherr API."""

    def __init__(self, message) -> None:
        """Initialize the exception."""
        self.message = message

    def __str__(self):
        """Return the exception message."""
        return self.message


class LiebherrAPI:
    """Liebherr API Class."""

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        """Initialize the Liebherr API."""
        self._hass = hass
        self.connector = {}
        self.session = {}
        self._username = config.get("username")
        self._password = config.get("password")
        self._token = None
        self._code_verifier = self._generate_code_verifier()
        self._code_challenge = self._generate_code_challenge(self._code_verifier)
        self.translations = {}

    def _generate_code_verifier(self):
        """Generate a secure code verifier."""
        return secrets.token_urlsafe(64)

    def _generate_code_challenge(self, code_verifier):
        """Generate a code challenge from the code verifier."""
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    async def authenticate(self) -> None:
        """Authenticate with the Liebherr API."""
        # Generate code verifier and challenge
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)

        # Step 1: Get the RequestVerificationToken and ncforminfo
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        auth_url = (
            f"https://login.liebherr.com/connect/authorize?client_id={CLIENT_ID}&"
            f"redirect_uri={REDIRECT_URI}&response_type=code&scope=openid%20email%20profile%20hau:sdb:smartdevice:2.0%20offline_access&"
            f"state=session_state&code_challenge_method=S256&code_challenge={code_challenge}"
        )

        login_url = (
            f"https://login.liebherr.com/Account/Login?ReturnUrl=/connect/authorize/callback?client_id={CLIENT_ID}&"
            f"redirect_uri={REDIRECT_URI}&response_type=code&scope=openid%20email%20profile%20hau:sdb:smartdevice:2.0%20offline_access&"
            f"state=session_state&code_challenge_method=S256&code_challenge={code_challenge}"
        )

        callback_url = (
            f"https://login.liebherr.com/connect/authorize/callback?client_id={CLIENT_ID}&"
            f"redirect_uri={REDIRECT_URI}&response_type=code&scope=openid%20email%20profile%20hau:sdb:smartdevice:2.0%20offline_access&"
            f"state=session_state&code_challenge_method=S256&code_challenge={code_challenge}"
        )

        async with self.session.get(auth_url) as response:
            if response.status != 200:
                _LOGGER.error(
                    "Failed to retrieve the initial authentication page: %s",
                    response.status,
                )
                raise LiebherrAuthException(
                    "Failed to retrieve initial authentication page."
                )

            html = await response.text()
            _LOGGER.debug("STEP1: auth_url content: %s", html)
            verification_token = self._extract_verification_token(html)
            ncforminfo = self._extract_ncforminfo(html)

        # Step 2: Perform the login
        login_data = {
            "Username": self._username,
            "Password": self._password,
            "__RequestVerificationToken": verification_token,
            "__ncforminfo": ncforminfo,
        }

        async with self.session.post(
            login_url, headers=headers, data=login_data, allow_redirects=False
        ) as response:
            _LOGGER.debug("STEP2: login_url content: %s", login_url)
            if response.status not in (302, 200):
                _LOGGER.error("Login failed with status code: %s", response.headers)
                raise LiebherrAuthException("Failed to log in to Liebherr API")

            redirect_location = response.headers.get("Location")
            if not redirect_location:
                _LOGGER.error("Failed to extract redirect URL from login response")
                raise LiebherrAuthException("Missing redirect URL after login")

        # Step 3: Retrieve authorization code
        async with self.session.get(callback_url, allow_redirects=False) as response:
            _LOGGER.debug("STEP3: callback_url content: %s", response.headers)
            if response.status not in (302, 0):
                _LOGGER.error(
                    "Failed to retrieve authorization code: %s", response.status
                )
                raise LiebherrAuthException("Failed to retrieve authorization code")

            location_header = response.headers.get("Location")
            if not location_header:
                _LOGGER.error("Missing Location header in response")
                raise LiebherrAuthException(
                    "Missing Location header for authorization code"
                )

            query_params = self._parse_query_params(location_header.split("?")[-1])
            authorization_code = query_params.get("code", [None])[0]

            if not authorization_code:
                _LOGGER.error("Authorization code not found in redirect response")
                raise LiebherrAuthException("Missing authorization code")

        # Step 4: Exchange authorization code for access token
        token_data = {
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": authorization_code,
            "code_verifier": code_verifier,
        }

        async with self.session.post(
            TOKEN_URL, headers=headers, data=token_data
        ) as response:
            _LOGGER.debug("STEP4: TOKEN_URL content: %s", TOKEN_URL)
            if response.status != 200:
                _LOGGER.error(
                    "Token exchange failed with status code: %s", response.status
                )
                raise LiebherrAuthException(
                    "Failed to exchange authorization code for token"
                )

            token_response = await response.json()
            self._token = token_response.get("access_token")

            if not self._token:
                _LOGGER.error("Failed to retrieve access token: %s", token_response)
                raise LiebherrAuthException("Missing access token in token response")

    def _extract_verification_token(self, html):
        """Extract the RequestVerificationToken from the HTML page."""
        match = re.search(
            r'<input[^>]*name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']+)["\']',
            html,
        )
        if not match:
            _LOGGER.error("HTML content: %s", html)
            raise LiebherrAuthException("Verification token not found in HTML")
        return match.group(1)

    def _extract_ncforminfo(self, html):
        """Extract the ncforminfo from the HTML page."""
        match = re.search(
            r'<input[^>]*name=["\']__ncforminfo["\'][^>]*value=["\']([^"\']+)["\']',
            html,
        )
        if not match:
            _LOGGER.error("HTML content: %s", html)
            raise LiebherrAuthException("ncforminfo not found in HTML")
        return match.group(1)

    def _parse_query_params(self, query):
        """Parse query parameters from a URL."""
        return parse_qs(query)

    async def get_appliances(self):
        """Retrieve the list of appliances."""
        if self._token is None:
            auth = await self.authenticate()
            if auth is not None:
                _LOGGER.error("Failed to authenticate: %s", auth)
                return []

        headers = {
            "Authorization": f"Bearer {self._token}",
        }
        if _DEBUG:
            data = [
                {
                    "deviceId": "38.258.275.0",
                    "nickname": "SUPERCOOLER-DEVICE",
                    "applianceType": "FREEZER",
                    "imageUrl": "https://stdprodsmartdevicepub.blob.core.windows.net/public/productimages/128b2adc-ae3e-45a3-b0a6-bb45b7a0599d.png",
                    "applianceName": "SIFNei 5188-20 001",
                    "onboardingState": "ONBOARDED",
                    "applianceInformation": {
                        "modelType": "EIGER",
                        "displayPosition": "BEHIND_DOOR",
                        "connectivitySolutionType": "INTEGRATED",
                        "guaranteeState": "NOT_ELIGIBLE",
                        "capabilities": [
                            "PARTY_MODE",
                            "BOTTLE_TIMER",
                            "ICE_MAKER",
                            "SUPER_FROST",
                            "NIGHT_MODE",
                            "HOLIDAY_MODE",
                            "PARTY_MODE",
                            "AIR_FILTER",
                            "BIO_FRESH",
                            "BIO_FRESH_PLUS",
                            "HYDRO_BREEZE",
                            "SUPER_COOL",
                            "SUPER_FROST",
                            "DOOR_ALARM",
                            "POWER_FAILURE_ALARM",
                            "TEMPERATURE_ALARM",
                        ],
                        "connected": True,
                        "displayDesignId": "ADVANCED",
                    },
                }
            ]
            return [
                {
                    "deviceId": appliance["deviceId"],
                    "model": appliance["applianceName"],
                    "image": appliance["imageUrl"],
                    "nickname": appliance.get("nickname", appliance["applianceName"]),
                    "applianceType": appliance["applianceType"],
                    "capabilities": appliance["applianceInformation"]["capabilities"],
                    "available": appliance["applianceInformation"].get(
                        "connected", True
                    ),
                    "controls": await self.get_controls(appliance["deviceId"]),
                }
                for appliance in data
            ]

        async with self.session.get(BASE_API_URL, headers=headers) as response:
            if response.status != 200:
                _LOGGER.error("Failed to fetch appliances: %s", response.status)
                if response.status == 401:
                    await self.authenticate()
                return []

            data = await response.json()
            _LOGGER.debug("Fetched appliances: %s", data)
            return [
                {
                    "deviceId": appliance["deviceId"],
                    "model": appliance["applianceName"],
                    "image": appliance["imageUrl"],
                    "nickname": appliance.get("nickname", appliance["applianceName"]),
                    "applianceType": appliance["applianceType"],
                    "capabilities": appliance["applianceInformation"]["capabilities"],
                    "available": appliance["applianceInformation"].get(
                        "connected", True
                    ),
                    "controls": await self.get_controls(appliance["deviceId"]),
                }
                for appliance in data
            ]

    async def get_controls(self, device_id):
        """Retrieve controls for a specific appliance."""
        url = f"{BASE_API_URL}/{device_id}/controls"
        headers = {
            "Authorization": f"Bearer {self._token}",
        }

        if _DEBUG:
            return [
                {
                    "controlType": "temperature",
                    "endpoint": "zones/0/temperature",
                    "affectedBy": [],
                    "current": 5,
                    "target": 5,
                    "min": 3,
                    "max": 9,
                    "temperatureUnit": "CELSIUS",
                    "identifier": "COOLING",
                },
                {
                    "controlType": "temperature",
                    "endpoint": "zones/1/temperature",
                    "affectedBy": [],
                    "current": 5,
                    "target": 5,
                    "min": 3,
                    "max": 9,
                    "temperatureUnit": "CELSIUS",
                    "identifier": "FREEZING",
                },
                {
                    "controlType": "temperature",
                    "endpoint": "zones/2/temperature",
                    "affectedBy": [],
                    "current": 55,
                    "target": 55,
                    "min": 53,
                    "max": 59,
                    "temperatureUnit": "CELSIUS",
                    "identifier": "HEATING",
                },
                {
                    "controlType": "biofresh",
                    "affectedBy": [],
                    "current": 0,
                    "temperatureUnit": "CELSIUS",
                },
                {
                    "controlType": "icemaker",
                    "endpoint": "zones/0/icemaker",
                    "affectedBy": [],
                    "currentMode": "ON",
                    "tileMode": "ON",
                    "hasMaxIce": True,
                },
                {
                    "controlType": "biofreshplus",
                    "endpoint": "zones/0/biofreshplus",
                    "affectedBy": [],
                    "current": 0,
                    "temperatureUnit": "CELSIUS",
                    "currentMode": "ZERO_ZERO",
                    "supportedModes": [
                        "MINUS_TWO_MINUS_TWO",
                        "ZERO_ZERO",
                        "MINUS_TWO_ZERO",
                    ],
                },
                {
                    "controlType": "toggle",
                    "endpoint": "zones/0/supercool",
                    "affectedBy": [],
                    "identifier": "SUPERCOOL",
                    "active": False,
                },
                {
                    "controlType": "toggle",
                    "endpoint": "zones/1/superfrost",
                    "affectedBy": [],
                    "identifier": "SUPERFROST",
                    "active": False,
                },
                {
                    "controlType": "hydrobreeze",
                    "endpoint": "zones/0/hydrobreeze",
                    "affectedBy": [],
                    "currentMode": "MEDIUM",
                },
                {
                    "controlType": "autodoor",
                    "endpoint": "zones/0/autodoor/trigger",
                    "affectedBy": [],
                    "doorState": "CLOSED",
                    "enabled": True,
                    "calibrated": True,
                },
                {
                    "controlType": "toggle",
                    "endpoint": "partymode",
                    "affectedBy": [],
                    "identifier": "PARTYMODE",
                    "active": False,
                },
                {
                    "controlType": "toggle",
                    "endpoint": "nightmode",
                    "affectedBy": [],
                    "identifier": "NIGHTMODE",
                    "active": False,
                },
                {
                    "controlType": "toggle",
                    "endpoint": "holidaymode",
                    "affectedBy": [],
                    "identifier": "HOLIDAYMODE",
                    "temperatureUnit": "CELSIUS",
                    "active": False,
                },
            ]

        async with self.session.get(url, headers=headers) as response:
            if response.status != 200:
                _LOGGER.error(
                    "Failed to fetch controls for device %s: %s",
                    device_id,
                    response.status,
                )
                return []
            if response.status == 401:
                await self.authenticate()
                return []
            data = await response.json()
            _LOGGER.debug("Fetched controls for device %s: %s", device_id, data)
            return data

    async def set_temperature(self, endpoint, temperature):
        """Set the temperature for a specific endpoint."""
        url = f"{BASE_API_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = {"value": temperature}

        async with self.session.put(url, headers=headers, json=payload) as response:
            if response.status != 204:
                _LOGGER.error("Failed to set temperature: %s", response.status)

    async def set_control(self, endpoint, value):
        """Activate or deactivate a control."""
        url = f"{BASE_API_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = {"active": value}

        async with self.session.put(url, headers=headers, json=payload) as response:
            if response.status != 200:
                _LOGGER.error("Failed to set control: %s", response.status)

    async def set_value(self, endpoint, value):
        """Activate or deactivate a control."""
        url = f"{BASE_API_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = value

        async with self.session.put(url, headers=headers, json=payload) as response:
            if response.status != 204:
                _LOGGER.error("Failed to set control: %s", response.status)
        await self.get_appliances()

    async def set_active(self, endpoint, active):
        """Activate or deactivate a control."""
        url = f"{BASE_API_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = {"active": active}

        async with self.session.put(url, headers=headers, json=payload) as response:
            if response.status != 204:
                _LOGGER.error("Failed to set control: %s", response.status)
        await self.get_appliances()

    async def get_notifications(self):
        """Retrieve notifications from the Liebherr API."""
        url = f"{BASE_URL}/notifications"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        try:
            async with self.session.get(url, headers=headers) as response:
                _LOGGER.debug("Fetching notifications: %s", response.status)
                if response.status == 200:
                    # Parse JSON response
                    _LOGGER.debug("Fetching notifications: %s", await response.text())
                    return await response.json()
                _LOGGER.error(
                    "Failed to fetch notifications: %s - %s",
                    response.status,
                    await response.text(),
                )
                if response.status == 401:
                    await self.authenticate()

                return []
        except LiebherrFetchException as e:
            _LOGGER.error("Error fetching notifications: %s", e)
            return []

    async def fetch_notifications(self, config_entry):
        """Fetch notifications from the Liebherr API."""
        try:
            # Fetch all notifications from the API
            notifications = await self.get_notifications()

            # Get selected devices from options
            selected_devices = config_entry.options.get("devices_to_notify", [])

            # Filter notifications for selected devices
            filtered_notifications = [
                notification
                for notification in notifications
                if notification["deviceId"] in selected_devices
                and not notification.get("isAcknowledged", False)
            ]

            # Process only filtered notifications
            await self.process_notifications(filtered_notifications)
        except LiebherrFetchException as e:
            _LOGGER.error("Error fetching notifications: %s", e)
        else:
            return filtered_notifications

    async def load_translations(self):
        """Load translations from the translations folder."""
        lang = self._hass.config.language  # Aktuelle Sprache des Benutzers
        translation_file = Path(__file__).parent / f"translations/{lang}.json"

        # Fallback zu Englisch, wenn die Sprache nicht verfügbar ist
        if not translation_file.is_file():
            translation_file = Path(__file__).parent / "translations/en.json"

        # Übersetzungen laden
        if translation_file.is_file():
            async with aiofiles.open(translation_file, encoding="utf-8") as file:
                content = await file.read()
                return json.loads(content)
        return {}

    def _translate(self, category, key):
        """Retrieve a translation for the given category and key."""
        return self.translations.get(category, {}).get(key, key)

    def _get_device_name(self, device_registry, device_id):
        """Get the device name based on the deviceId."""
        for device in device_registry.devices.values():
            # Prüfen, ob die Device-Id zu den Identifiers gehört
            if (DOMAIN, device_id) in device.identifiers:
                return device.name  # Name des Geräts zurückgeben
        return None  # Kein Name gefunden

    async def process_notifications(self, notifications):
        """Process notifications and create Home Assistant notifications."""

        device_registry = dr.async_get(self._hass)

        for notification in notifications:
            device_id = notification["deviceId"]
            device_name = self._get_device_name(device_registry, device_id)

            raw_created_at = notification.get("createdAt")
            created_at = None
            if raw_created_at:
                dt_obj = parse_datetime(raw_created_at)
                if dt_obj:
                    dt_local = as_local(dt_obj)
                    created_at = dt_local.strftime(
                        "%x %X"
                    )  # Lokale Formatierung (Datum und Zeit)

            notification_type = notification.get("notificationType", "unknown")
            notification_id = f"liebherr_{notification['notificationId']}"
            translated_notification_type = self._translate(
                "notificationType", notification_type
            )

            svg_icon = ""
            match notification["notificationType"]:
                case "door_alarm":
                    svg_icon = DOOR_ALARM
                case "air_filter_reminder":
                    svg_icon = AIR_FILTER
                case "upper_temperature_alarm", "lower_temperature_alarm":
                    svg_icon = TEMPERATURE_ALARM
                case "auto_door_overheat_alarm":
                    svg_icon = DOOR_OVERHEAT_ALARM
                case "auto_door_obstacle_alarm":
                    svg_icon = OBSTACLE_ALARM
                case "upper_power_failure_alarm", "lower_power_failure_alarm":
                    svg_icon = POWER_FAILURE_ALARM

            message = f"### {translated_notification_type} ({created_at if created_at else raw_created_at})\n"
            if svg_icon:
                message += f"![icon]({svg_icon})\n"
            self._hass.components.persistent_notification.create(
                message,
                title=f"{device_name or device_id}",
                notification_id=notification_id,
            )
            self._add_dismiss_listener(notification_id, notification)

    def _add_dismiss_listener(self, notification_id, notification):
        """Track when the notification is dismissed."""

        async def dismiss_handler(event):
            """Handle the dismiss event."""
            if event.data.get("notification_id") == notification_id:
                # Sende den Acknowledgment-Request
                await self._acknowledge_notification(self, notification)
                # Entferne den Listener
                self._hass.bus.async_listen(
                    "persistent_notification.dismiss", dismiss_handler
                ).remove()

        # Event-Listener hinzufügen
        self._hass.bus.async_listen("persistent_notification.dismiss", dismiss_handler)

    async def _acknowledge_notification(self, notification):
        """Send acknowledgment to the API."""
        try:
            url = f"https://mobile-api.smartdevice.liebherr.com/v1/household/notifications/{notification['deviceId']}/{notification['notificationId']}"
            await self.acknowledge_notification(url, notification["notificationId"])
        except LiebherrException as e:
            self._hass.components.persistent_notification.create(
                message=f"Failed to acknowledge notification(2): {e}",
                title="Liebherr Notification Error",
            )

    async def acknowledge_notification(self, device_id, notification_id):
        """Acknowledge a notification."""
        url = f"{BASE_API_URL}/notifications/{device_id}/{notification_id}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload = {"isAcknowledged": True}

        async with self.session.patch(url, headers=headers, json=payload) as response:
            if response.status == 204:  # Erfolg: Kein Inhalt
                _LOGGER.info(
                    "Successfully acknowledged notification %s for device %s",
                    notification_id,
                    device_id,
                )
                return True
            _LOGGER.error(
                "Failed to acknowledge notification(1) %s: %s",
                notification_id,
                response.status,
            )
            return False


class LiebherrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Liebherr Integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Try to authenticate with the provided credentials
            api = LiebherrAPI(self.hass, user_input)
            try:
                await api.authenticate()
                return self.async_create_entry(
                    title="Liebherr SmartDevice", data=user_input
                )
            except LiebherrAuthException as e:
                _LOGGER.error("Authentication failed: %s", e)
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
