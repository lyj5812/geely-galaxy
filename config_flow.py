"""Config flow for Geely Galaxy integration."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.http import HomeAssistantView
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

STEP_SMS_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("sms_code"): str,
    }
)


# ==================== 验证码回调 HTTP 视图 ====================

class GeelyCaptchaCallbackView(HomeAssistantView):
    """接收 GeeTest 验证码页面的回调结果。

    验证码页面完成滑块验证后，自动 POST 结果到此端点，
    无需用户手动复制粘贴验证码。
    """

    url = "/api/geely_galaxy/captcha_callback"
    name = "api:geely_galaxy:captcha_callback"
    requires_auth = False  # flow_id 本身作为一次性凭证

    async def post(self, request):
        """Handle POST with captcha result."""
        hass = request.app["hass"]
        try:
            data = await request.json()
        except Exception:
            return self.json_message("Invalid JSON", status_code=400)

        flow_id = data.get("flow_id")
        captcha_data = data.get("captcha_data")

        if not flow_id or not captcha_data:
            return self.json_message("Missing data", status_code=400)

        required_keys = ["lot_number", "captcha_output", "pass_token", "gen_time"]
        if not all(k in captcha_data for k in required_keys):
            return self.json_message("Invalid captcha data", status_code=400)

        # 存储验证码结果，供 config flow 步骤读取
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN].setdefault("captcha_results", {})
        hass.data[DOMAIN]["captcha_results"][flow_id] = captcha_data

        # 推进 config flow 到下一步
        try:
            await hass.config_entries.flow.async_configure(flow_id=flow_id)
        except Exception as err:
            _LOGGER.error("推进配置流程失败 %s: %s", flow_id, err)
            return self.json_message(str(err), status_code=500)

        return self.json({"success": True})


def _ensure_captcha_view(hass: HomeAssistant) -> None:
    """注册验证码回调视图（仅首次调用时注册）。"""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("captcha_view_registered"):
        domain_data["captcha_results"] = {}
        domain_data["captcha_view_registered"] = True
        hass.http.register_view(GeelyCaptchaCallbackView())


# ==================== 辅助函数 ====================

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


# ==================== Config Flow ====================

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
        self._captcha_data: dict | None = None

    def _get_captcha_data(self) -> dict | None:
        """读取并消费本次 flow 的验证码结果。"""
        results = self.hass.data.get(DOMAIN, {}).get("captcha_results", {})
        return results.pop(self.flow_id, None)

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
        """Step 2: 外部步骤 - GeeTest 滑块验证。

        验证码页面完成后会自动回调，推进到下一步。
        """
        _ensure_captcha_view(self.hass)

        captcha_data = self._get_captcha_data()
        if captcha_data:
            self._captcha_data = captcha_data
            return self.async_external_step_done(next_step_id="sms_code")

        return self.async_external_step(
            step_id="sms_captcha",
            url=f"/local/geely_captcha.html?flow_id={self.flow_id}",
        )

    async def async_step_sms_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: 验证 GeeTest → 发送短信 → 输入验证码。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            # 用户提交了短信验证码 → 验证并登录
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

        elif self._captcha_data:
            # 首次进入（从验证码步骤跳转来）→ 校验 GeeTest 并发送短信
            api = GeelyGalaxyApi(refresh_token="", device_sn=self._device_sn)
            try:
                self._certify_id = await api.validate_geetest(
                    lot_number=self._captcha_data["lot_number"],
                    captcha_output=self._captcha_data["captcha_output"],
                    pass_token=self._captcha_data["pass_token"],
                    gen_time=self._captcha_data["gen_time"],
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
            finally:
                await api.close()
            self._captcha_data = None

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

    # ==================== 密码登录 ====================

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
        """外部步骤 - GeeTest 滑块验证（密码登录流程）。"""
        _ensure_captcha_view(self.hass)

        captcha_data = self._get_captcha_data()
        if captcha_data:
            self._captcha_data = captcha_data
            return self.async_external_step_done(next_step_id="pwd_login")

        return self.async_external_step(
            step_id="captcha",
            url=f"/local/geely_captcha.html?flow_id={self.flow_id}",
        )

    async def async_step_pwd_login(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """校验验证码并执行密码登录。"""
        errors: dict[str, str] = {}

        api = GeelyGalaxyApi(refresh_token="", device_sn=self._device_sn)
        try:
            certify_id = await api.validate_geetest(
                lot_number=self._captcha_data["lot_number"],
                captcha_output=self._captcha_data["captcha_output"],
                pass_token=self._captcha_data["pass_token"],
                gen_time=self._captcha_data["gen_time"],
            )
            self._captcha_data = None

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

        # 登录失败 → 返回密码输入页面并显示错误
        return self.async_show_form(
            step_id="password",
            data_schema=STEP_PASSWORD_SCHEMA,
            errors=errors,
        )
