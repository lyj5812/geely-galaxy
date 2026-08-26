"""Config flow for Geely Galaxy integration."""
from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import GeelyGalaxyApi, GeelyApiError, GeelyAuthError
from .captcha_views import ensure_captcha_views
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

    @property
    def _is_reauth(self) -> bool:
        """Check if this flow is a reauth flow."""
        return self.context.get("source") == config_entries.SOURCE_REAUTH

    def _captcha_url(self, flow_id: str) -> str:
        """构造验证码页面的绝对 URL（external step 要求绝对地址）。

        优先使用用户显式配置的 external/internal URL；未配置时动态
        获取 HA 主机的局域网 IP 兜底。全程容错，避免探测异常导致流程卡死。
        """
        base: str | None = None
        try:
            base = (
                self.hass.config.get("external_url")
                or self.hass.config.get("internal_url")
            )
        except Exception:  # noqa: BLE001 - 兜底，任何异常都回退到动态探测
            base = None

        if not base:
            ip = "127.0.0.1"
            try:
                ip = self._local_ip()
            except Exception:  # noqa: BLE001
                pass
            port = getattr(self.hass.config, "api_port", None) or 8123
            base = f"http://{ip}:{port}"

        return f"{base}/api/geely_galaxy/captcha?flow_id={flow_id}"

    @staticmethod
    def _local_ip() -> str:
        """获取 HA 主机的局域网 IP（用于未配置 URL 时兜底）。"""
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # UDP connect 不实际发包，仅让内核选择默认路由对应的本机地址
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def _finish_login(self, title: str, data: dict[str, Any]) -> FlowResult:
        """Finish login: update existing entry (reauth) or create new entry."""
        if self._is_reauth:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=data,
            )
        return self.async_create_entry(title=title, data=data)

    def _get_captcha_data(self) -> dict | None:
        """读取并消费本次 flow 的验证码结果。"""
        results = self.hass.data.get(DOMAIN, {}).get("captcha_results", {})
        return results.pop(self.flow_id, None)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: choose login method."""
        # 提前注册验证码视图，避免首次点击登录时才注册导致流程报错
        ensure_captcha_views(self.hass)

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

    # ==================== 重新认证 ====================

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauth when token expires (e.g. APP login invalidated HA token)."""
        return await self.async_step_user()

    # ==================== 短信验证码登录流程 ====================

    async def async_step_sms(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dispatch SMS menu option to sms_phone step."""
        return await self.async_step_sms_phone(user_input)

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
        ensure_captcha_views(self.hass)

        captcha_data = self._get_captcha_data()
        if captcha_data:
            self._captcha_data = captcha_data
            return self.async_external_step_done(next_step_id="sms_code")

        # 短暂延迟确保视图注册完成，避免首次点击失败
        import asyncio
        await asyncio.sleep(0.1)

        return self.async_external_step(
            step_id="sms_captcha",
            url=self._captcha_url(self.flow_id),
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

                if info.get("vin") and not self._is_reauth:
                    await self.async_set_unique_id(info["vin"])
                    self._abort_if_unique_id_configured()

                entry_data = {
                    CONF_REFRESH_TOKEN: tokens["refresh_token"],
                    CONF_DEVICE_SN: self._device_sn,
                }
                return self._finish_login(
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
                if info.get("vin") and not self._is_reauth:
                    await self.async_set_unique_id(info["vin"])
                    self._abort_if_unique_id_configured()
                return self._finish_login(title=info["title"], data=user_input)

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
        ensure_captcha_views(self.hass)

        captcha_data = self._get_captcha_data()
        if captcha_data:
            self._captcha_data = captcha_data
            return self.async_external_step_done(next_step_id="pwd_login")

        return self.async_external_step(
            step_id="captcha",
            url=self._captcha_url(self.flow_id),
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

            if info.get("vin") and not self._is_reauth:
                await self.async_set_unique_id(info["vin"])
                self._abort_if_unique_id_configured()

            entry_data = {
                CONF_REFRESH_TOKEN: tokens["refresh_token"],
                CONF_DEVICE_SN: self._device_sn,
            }
            return self._finish_login(
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
