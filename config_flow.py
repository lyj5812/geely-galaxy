"""Config flow for Geely Galaxy integration."""
from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import GeelyGalaxyApi, GeelyApiError, GeelyAuthError
from .const import (
    DOMAIN,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_SN,
    CONF_PHONE,
    CONF_PASSWORD,
    CONF_LOGIN_METHOD,
    LOGIN_METHOD_SMS,
    LOGIN_METHOD_PASSWORD,
    LOGIN_METHOD_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

STEP_TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REFRESH_TOKEN): str,
        vol.Required(CONF_DEVICE_SN): str,
    }
)

STEP_PHONE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PHONE): str,
    }
)

STEP_PASSWORD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PHONE): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_CAPTCHA_SCHEMA = vol.Schema(
    {
        vol.Required("captcha_code"): str,
    }
)

STEP_SMS_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("sms_code"): str,
    }
)


async def validate_token_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate token login input."""
    api = GeelyGalaxyApi(
        refresh_token=data[CONF_REFRESH_TOKEN],
        device_sn=data[CONF_DEVICE_SN],
    )

    try:
        if not await api.test_connection():
            raise GeelyAuthError("Invalid credentials")

        try:
            vehicles = await api.get_vehicle_list()
            if vehicles:
                model = vehicles[0].get("seriesNameVs", "吉利银河")
                vin = vehicles[0].get("vin", "")
                return {"title": f"{model}", "vin": vin}
        except GeelyApiError:
            pass

        return {"title": "吉利银河", "vin": ""}
    finally:
        await api.close()


async def _do_login_and_get_info(
    api: GeelyGalaxyApi,
) -> dict[str, Any]:
    """登录成功后获取车辆信息。"""
    try:
        vehicles = await api.get_vehicle_list()
        if vehicles:
            model = vehicles[0].get("seriesNameVs", "吉利银河")
            vin = vehicles[0].get("vin", "")
            return {"title": f"{model}", "vin": vin}
    except GeelyApiError:
        pass
    return {"title": "吉利银河", "vin": ""}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Geely Galaxy."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._phone: str = ""
        self._password: str = ""
        self._device_sn: str = ""
        self._certify_id: str = ""
        self._login_method: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: choose login method."""
        if user_input is not None:
            method = user_input.get(CONF_LOGIN_METHOD)
            if method == LOGIN_METHOD_SMS:
                return await self.async_step_sms_phone()
            if method == LOGIN_METHOD_PASSWORD:
                return await self.async_step_password()
            return await self.async_step_token()

        return self.async_show_menu(
            step_id="user",
            menu_options=[LOGIN_METHOD_SMS, LOGIN_METHOD_PASSWORD, LOGIN_METHOD_TOKEN],
        )

    # ==================== 短信验证码登录流程 ====================

    async def async_step_sms_phone(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Enter phone number."""
        if user_input is not None:
            self._phone = user_input[CONF_PHONE]
            self._device_sn = uuid.uuid4().hex
            self._login_method = LOGIN_METHOD_SMS
            return await self.async_step_sms_captcha()

        return self.async_show_form(
            step_id="sms_phone",
            data_schema=STEP_PHONE_SCHEMA,
        )

    async def async_step_sms_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Solve GeeTest captcha, then send SMS code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            captcha_code = user_input.get("captcha_code", "").strip()

            try:
                decoded = json.loads(
                    base64.b64decode(captcha_code).decode("utf-8")
                )
            except Exception:
                errors["base"] = "invalid_captcha"
                return self.async_show_form(
                    step_id="sms_captcha",
                    data_schema=STEP_CAPTCHA_SCHEMA,
                    errors=errors,
                )

            required_keys = ["lot_number", "captcha_output", "pass_token", "gen_time"]
            if not all(k in decoded for k in required_keys):
                errors["base"] = "invalid_captcha"
                return self.async_show_form(
                    step_id="sms_captcha",
                    data_schema=STEP_CAPTCHA_SCHEMA,
                    errors=errors,
                )

            api = GeelyGalaxyApi(refresh_token="", device_sn=self._device_sn)
            try:
                self._certify_id = await api.validate_geetest(
                    lot_number=decoded["lot_number"],
                    captcha_output=decoded["captcha_output"],
                    pass_token=decoded["pass_token"],
                    gen_time=decoded["gen_time"],
                )

                await api.send_sms_code(
                    phone=self._phone,
                    certify_id=self._certify_id,
                )
            except GeelyApiError as err:
                _LOGGER.error("发送验证码失败: %s", err)
                errors["base"] = "sms_send_failed"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return await self.async_step_sms_code()
            finally:
                await api.close()

        return self.async_show_form(
            step_id="sms_captcha",
            data_schema=STEP_CAPTCHA_SCHEMA,
            errors=errors,
        )

    async def async_step_sms_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Enter SMS verification code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            sms_code = user_input.get("sms_code", "").strip()

            api = GeelyGalaxyApi(refresh_token="", device_sn=self._device_sn)
            try:
                tokens = await api.sms_login(
                    phone=self._phone,
                    sms_code=sms_code,
                    certify_id=self._certify_id,
                )

                info = await _do_login_and_get_info(api)

                if info.get("vin"):
                    await self.async_set_unique_id(info["vin"])
                    self._abort_if_unique_id_configured()

                entry_data = {
                    CONF_REFRESH_TOKEN: tokens["refresh_token"],
                    CONF_DEVICE_SN: self._device_sn,
                }
                return self.async_create_entry(
                    title=info["title"], data=entry_data
                )
            except GeelyAuthError as err:
                _LOGGER.error("SMS login failed: %s", err)
                errors["base"] = "invalid_sms_code"
            except GeelyApiError as err:
                _LOGGER.error("API error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            finally:
                await api.close()

        return self.async_show_form(
            step_id="sms_code",
            data_schema=STEP_SMS_CODE_SCHEMA,
            errors=errors,
        )

    # ==================== Token 登录 ====================

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle token login step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_token_input(self.hass, user_input)
            except GeelyAuthError:
                errors["base"] = "invalid_auth"
            except GeelyApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if info.get("vin"):
                    await self.async_set_unique_id(info["vin"])
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="token",
            data_schema=STEP_TOKEN_SCHEMA,
            errors=errors,
        )

    # ==================== 密码登录（需要预加密密码）====================

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle password login step: enter phone/password."""
        if user_input is not None:
            self._phone = user_input[CONF_PHONE]
            self._password = user_input[CONF_PASSWORD]
            self._device_sn = uuid.uuid4().hex
            self._login_method = LOGIN_METHOD_PASSWORD
            return await self.async_step_captcha()

        return self.async_show_form(
            step_id="password",
            data_schema=STEP_PASSWORD_SCHEMA,
        )

    async def async_step_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle captcha step for password login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            captcha_code = user_input.get("captcha_code", "").strip()

            try:
                decoded = json.loads(
                    base64.b64decode(captcha_code).decode("utf-8")
                )
            except Exception:
                errors["base"] = "invalid_captcha"
                return self.async_show_form(
                    step_id="captcha",
                    data_schema=STEP_CAPTCHA_SCHEMA,
                    errors=errors,
                )

            required_keys = ["lot_number", "captcha_output", "pass_token", "gen_time"]
            if not all(k in decoded for k in required_keys):
                errors["base"] = "invalid_captcha"
                return self.async_show_form(
                    step_id="captcha",
                    data_schema=STEP_CAPTCHA_SCHEMA,
                    errors=errors,
                )

            api = GeelyGalaxyApi(refresh_token="", device_sn=self._device_sn)
            try:
                certify_id = await api.validate_geetest(
                    lot_number=decoded["lot_number"],
                    captcha_output=decoded["captcha_output"],
                    pass_token=decoded["pass_token"],
                    gen_time=decoded["gen_time"],
                )

                tokens = await api.password_login(
                    phone=self._phone,
                    password=self._password,
                    certify_id=certify_id,
                )

                info = await _do_login_and_get_info(api)

                if info.get("vin"):
                    await self.async_set_unique_id(info["vin"])
                    self._abort_if_unique_id_configured()

                entry_data = {
                    CONF_REFRESH_TOKEN: tokens["refresh_token"],
                    CONF_DEVICE_SN: self._device_sn,
                }
                return self.async_create_entry(
                    title=info["title"], data=entry_data
                )
            except GeelyAuthError as err:
                _LOGGER.error("Password login failed: %s", err)
                errors["base"] = "invalid_auth"
            except GeelyApiError as err:
                _LOGGER.error("API error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            finally:
                await api.close()

        return self.async_show_form(
            step_id="captcha",
            data_schema=STEP_CAPTCHA_SCHEMA,
            errors=errors,
        )
