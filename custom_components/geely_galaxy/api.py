"""
API client for Geely Galaxy.

基于 geely-galaxy-assistant 项目实现
https://github.com/suyunkai/geely-galaxy-assistant
"""
import hashlib
import hmac
import base64
import uuid
import json
import logging
import random
import string
import time
from typing import Any
from collections.abc import Callable
from datetime import datetime, timezone
import aiohttp

_LOGGER = logging.getLogger(__name__)

# AppKey 和 AppSecret 映射（来自 geely-galaxy-assistant）
APP_SECRETS = {
    "204453306": "uUwSi6m9m8Nx3Grx7dQghyxMpOXJKDGu",
    "204373120": "XfH7OiOe07vorWwvGQdCqh6quYda9yGW",
    "204167276": "5XfsfFBrUEF0fFiAUmAFFQ6lmhje3iMZ",
    "204168364": "NqYVmMgH5HXol8RB8RkOpl8iLCBakdRo",
    "204179735": "UhmsX3xStU4vrGHGYtqEXahtkYuQncMf",
    "204195485": "CqPwP83wzdjesmLeDuzK6SljsYN5PvRM",  # 充电服务 API
}

# API 域名配置
API_HOSTS = {
    "user": "galaxy-user-api.geely.com",
    "app": "galaxy-app.geely.com",
    "vc": "galaxy-vc.geely.com",
    "recharge": "api-recharge.geely.com",  # 充电桩服务
}

# 签到 API AppKey
APP_KEYS_SIGN = "204453306"
SIGN_SECRET = "uUwSi6m9m8Nx3Grx7dQghyxMpOXJKDGu"

# AppKey 配置
APP_KEYS = {
    "user": "204179735",  # 用户API
    "app": "204167276",   # H5端应用API
    "vc": "204373120",    # 车辆控制API
    "recharge": "204195485",  # 充电桩服务 API
    "sign": APP_KEYS_SIGN,    # 签到 API
}

# 充电服务配置
RECHARGE_CONFIG = {
    "channel_id": "01701001",
    "oauth_client_id": "30000023",
}

# XCHANGER 平台配置（用于 geely2/L7 等不支持 VC 的车型）
XCHANGER_CONFIG = {
    "user_host": "user-api.xchanger.cn",
    "device_host": "device-api.xchanger.cn",
    "app_id": "galaxy_SDK_new",
    "operator_code": "geelygalaxy",
    "client_id": "EPLUS0000APP00IN2020263250667542",
    "sign_secret": "8c5d1f3a09e6b427d0c23a8f59b47e21",
    "oauth_client_id": "30000022",
}


class GeelyApiError(Exception):
    """Exception for Geely API errors."""


class GeelyAuthError(GeelyApiError):
    """Exception for authentication errors."""


class GeelyGalaxyApi:
    """API client for Geely Galaxy."""

    def __init__(
        self,
        refresh_token: str,
        device_sn: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._refresh_token = refresh_token
        self._device_sn = device_sn
        self._session = session
        self._token: str | None = None
        self._token_expire_at: float = 0  # access token 过期时间戳（秒）
        self._refresh_token_expire_at: float = 0  # refresh token 过期时间戳（秒）
        self._recharge_auth_token: str | None = None  # 充电服务 authToken
        self._own_session = False
        self._vehicle_info: dict = {}
        self._piling_code: str | None = None  # 缓存充电桩编码
        # refresh token 更新回调（供 __init__.py 注册，立即持久化）
        self._on_refresh_token_updated: Callable[[str], None] | None = None
        self._refresh_token_renew_attempted: float = 0  # 上次主动续期尝试时间
        # XCHANGER 状态（geely2/L7 车型）
        self._xchanger_jwt: str | None = None
        self._xchanger_jwt_expire_at: float = 0
        self._xchanger_user_id: str | None = None
        self._use_xchanger: bool = False  # VC 返回 B00000 后自动切换

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have a valid session."""
        if self._session is None:
            # JS 版本使用 tough-cookie（正常 cookie jar），cookie 在请求间传递
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    def _format_date_and_timestamp(self) -> tuple[str, str]:
        """Format date and timestamp for API request.

        返回 (date_string, timestamp_string)，保证两者来自同一时刻。

        JS 版本的实现逻辑：
        1. 获取当前时间，格式化为 "Wed, 22 Jan 2025 08:30:00 GMT"
        2. 将格式化后的字符串解析回 Date 对象
        3. 用 getTime() 获取毫秒时间戳
        由于格式化丢失了毫秒精度，JS 的时间戳总是以 000 结尾。
        这里复现相同的行为。
        """
        _DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        _MONTHS = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        now = datetime.now(timezone.utc)
        day_name = _DAYS[now.weekday()]
        month_name = _MONTHS[now.month - 1]
        date_str = f"{day_name}, {now.day:02d} {month_name} {now.year} {now.hour:02d}:{now.minute:02d}:{now.second:02d} GMT"
        # 与 JS 一致：截断到秒级精度再乘以 1000
        timestamp = str(int(now.replace(microsecond=0).timestamp()) * 1000)
        return date_str, timestamp

    def _generate_uuid(self) -> str:
        """Generate UUID for request."""
        return str(uuid.uuid4())

    def _calculate_content_md5(self, body: str) -> str:
        """Calculate Content-MD5 header value."""
        md5_hash = hashlib.md5(body.encode("utf-8")).digest()
        return base64.b64encode(md5_hash).decode("utf-8")

    def _calculate_signature(
        self,
        method: str,
        accept: str,
        content_md5: str,
        content_type: str,
        date: str,
        app_key: str,
        nonce: str,
        timestamp: str,
        path: str,
        app_code: str | None = None,
    ) -> str:
        """
        Calculate X-Ca-Signature using Alibaba Cloud API Gateway algorithm.

        签名字符串格式:
        HTTPMethod + "\n" +
        Accept + "\n" +
        Content-MD5 + "\n" +
        Content-Type + "\n" +
        Date + "\n" +
        [x-ca-appcode:xxx\n] (可选)
        x-ca-key:xxx\n +
        x-ca-nonce:xxx\n +
        x-ca-timestamp:xxx\n +
        Path
        """
        # 构建待签名字符串
        string_to_sign = f"{method}\n"
        string_to_sign += f"{accept}\n"
        string_to_sign += f"{content_md5}\n"
        string_to_sign += f"{content_type}\n"
        string_to_sign += f"{date}\n"

        # 如果有 appcode 则添加
        if app_code:
            string_to_sign += f"x-ca-appcode:{app_code}\n"

        string_to_sign += f"x-ca-key:{app_key}\n"
        string_to_sign += f"x-ca-nonce:{nonce}\n"
        string_to_sign += f"x-ca-timestamp:{timestamp}\n"
        # 对查询参数排序（与测试脚本一致）
        string_to_sign += self._sort_query_params(path)

        _LOGGER.debug("StringToSign: %s", repr(string_to_sign))

        # 获取对应的 Secret
        secret = APP_SECRETS.get(app_key)
        if not secret:
            raise GeelyApiError(f"Unknown AppKey: {app_key}")

        # 使用 HMAC-SHA256 计算签名
        signature = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        return base64.b64encode(signature).decode("utf-8")

    def _build_get_headers(self, app_key: str, path: str) -> dict[str, str]:
        """Build GET request headers with signature."""
        date, timestamp = self._format_date_and_timestamp()
        nonce = self._generate_uuid()

        signature = self._calculate_signature(
            method="GET",
            accept="application/json; charset=utf-8",
            content_md5="",
            content_type="application/x-www-form-urlencoded; charset=utf-8",
            date=date,
            app_key=app_key,
            nonce=nonce,
            timestamp=timestamp,
            path=path,
        )

        headers = {
            "date": date,
            "x-ca-signature": signature,
            "x-ca-nonce": nonce,
            "x-ca-key": app_key,
            "ca_version": "1",
            "accept": "application/json; charset=utf-8",
            "usetoken": "1",
            "x-ca-timestamp": timestamp,
            "x-ca-signature-headers": "x-ca-nonce,x-ca-timestamp,x-ca-key",
            "x-refresh-token": "true",
            "content-type": "application/x-www-form-urlencoded; charset=utf-8",
            "user-agent": "ALIYUN-ANDROID-UA",
            "deviceSN": self._device_sn,
            "txCookie": "",
            "appId": "galaxy-app",
            "appVersion": "1.46.0",
            "platform": "Android",
            "Cache-Control": "no-cache",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

        # 根据 AppKey 设置不同的参数（与 JS getGetHeader 一致）
        if app_key == APP_KEYS["user"]:
            headers["usetoken"] = "true"
            headers["host"] = API_HOSTS["user"]
            headers["tenantid"] = "569001701001"
            headers["svcsid"] = ""
            # 用户 API 的签名头顺序必须是字母顺序
            headers["x-ca-signature-headers"] = "x-ca-key,x-ca-nonce,x-ca-timestamp"
            del headers["x-refresh-token"]
            # 设备信息头（与测试脚本一致）
            headers["gl_dev_id"] = self._device_sn
            headers["gl_dev_model"] = "HomeAssistant"
            headers["gl_dev_brand"] = "HomeAssistant"
            headers["gl_dev_platform"] = "android"
            headers["gl_app_version"] = "1.46.0"
            headers["gl_os_version"] = "33"
            headers["gl_app_build"] = "146000098"

        # 与测试脚本一致：只在有 token 时才设置 token header
        if self._token:
            headers["token"] = self._token

        return headers

    def _build_post_headers(
        self, app_key: str, path: str, body: str
    ) -> dict[str, str]:
        """Build POST request headers with signature."""
        date, timestamp = self._format_date_and_timestamp()
        nonce = self._generate_uuid()
        content_md5 = self._calculate_content_md5(body)

        # 根据不同的 key 设置不同的签名头字段
        if app_key == APP_KEYS["vc"]:
            signature_headers = "x-ca-appcode,x-ca-nonce,x-ca-timestamp,x-ca-key"
            app_code = "usp-gateway-code"
        elif app_key == APP_KEYS["user"]:
            # 用户 API 使用字母顺序（与测试脚本一致）
            signature_headers = "x-ca-key,x-ca-nonce,x-ca-timestamp"
            app_code = None
        else:
            signature_headers = "x-ca-nonce,x-ca-key,x-ca-timestamp"
            app_code = None

        signature = self._calculate_signature(
            method="POST",
            accept="application/json; charset=utf-8",
            content_md5=content_md5,
            content_type="application/json; charset=utf-8",
            date=date,
            app_key=app_key,
            nonce=nonce,
            timestamp=timestamp,
            path=path,
            app_code=app_code,
        )

        headers = {
            "date": date,
            "x-ca-signature": signature,
            "x-ca-appcode": app_code if app_code else "SWGeelyCode",
            "x-ca-nonce": nonce,
            "x-ca-key": app_key,
            "ca_version": "1",
            "accept": "application/json; charset=utf-8",
            "usetoken": "1",
            "content-md5": content_md5,
            "x-ca-timestamp": timestamp,
            "x-ca-signature-headers": signature_headers,
            "x-refresh-token": "true",
            "user-agent": "ALIYUN-ANDROID-UA",
            "deviceSN": self._device_sn,
            "txCookie": "",
            "appId": "galaxy-app",
            "appVersion": "1.46.0",
            "platform": "Android",
            "Cache-Control": "no-cache",
            "sweet_security_info": '{"appVersion":"1.46.0","platform":"android"}',
            "methodtype": "6",
            "contenttype": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

        # 根据 AppKey 设置不同的参数（与 JS getPostHeader 一致）
        if app_key == APP_KEYS["user"]:
            headers["usetoken"] = "true"
            headers["host"] = API_HOSTS["user"]
            headers["tenantid"] = "569001701001"
            headers["svcsid"] = ""
            del headers["x-refresh-token"]
            # 设备信息头（与测试脚本一致）
            headers["gl_dev_id"] = self._device_sn
            headers["gl_dev_model"] = "HomeAssistant"
            headers["gl_dev_brand"] = "HomeAssistant"
            headers["gl_dev_platform"] = "android"
            headers["gl_app_version"] = "1.46.0"
            headers["gl_os_version"] = "33"
            headers["gl_app_build"] = "146000098"
        elif app_key == APP_KEYS["vc"]:
            headers["host"] = API_HOSTS["vc"]
        else:
            headers["host"] = API_HOSTS["app"]

        # 与测试脚本一致：只在有 token 时才设置 token header
        if self._token:
            headers["token"] = self._token

        return headers

    async def refresh_access_token(self) -> str:
        """Refresh the access token."""
        session = await self._ensure_session()

        path = f"/api/v1/login/refresh?refreshToken={self._refresh_token}"
        url = f"https://{API_HOSTS['user']}{path}"
        headers = self._build_get_headers(APP_KEYS["user"], path)

        _LOGGER.info(
            "开始刷新 AccessToken (refreshToken 前8位: %s...)",
            self._refresh_token[:8] if self._refresh_token else "空",
        )
        try:
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Refresh token response: %s", response_text)

                if response.status != 200:
                    _LOGGER.error(
                        "刷新 Token 失败: HTTP %s, 响应: %s",
                        response.status, response_text[:500],
                    )
                    raise GeelyAuthError(
                        f"Failed to refresh token: HTTP {response.status}"
                    )

                data = json.loads(response_text)
                code = data.get("code")

                if code not in ("success", 0, "0"):
                    msg = data.get("msg", data.get("message", "Unknown error"))
                    _LOGGER.error(
                        "刷新 Token 业务错误: code=%s, msg=%s, 完整响应: %s",
                        code, msg, response_text[:500],
                    )
                    raise GeelyAuthError(
                        f"Token refresh failed (code={code}): {msg}"
                    )

                token_data = data.get("data", {}).get("centerTokenDto", {})
                self._token = token_data.get("token")

                # 记录 token 过期时间
                expire_at = token_data.get("expireAt")
                if expire_at:
                    # expireAt 是毫秒时间戳
                    self._token_expire_at = expire_at / 1000
                    _LOGGER.info(
                        "AccessToken 刷新成功，过期时间: %s",
                        datetime.fromtimestamp(self._token_expire_at).isoformat(),
                    )
                else:
                    # 没有过期时间信息，默认 1.5 小时后过期
                    self._token_expire_at = time.time() + 5400
                    _LOGGER.warning("响应中无 expireAt 字段，默认 1.5 小时后过期")

                # 更新 refresh token（如果返回了新的）
                new_refresh_token = token_data.get("refreshToken")
                if new_refresh_token and new_refresh_token != self._refresh_token:
                    old_prefix = self._refresh_token[:8] if self._refresh_token else "空"
                    self._refresh_token = new_refresh_token
                    _LOGGER.info(
                        "RefreshToken 已滚动续期 (%s... → %s...)",
                        old_prefix, new_refresh_token[:8],
                    )
                    if self._on_refresh_token_updated:
                        self._on_refresh_token_updated(new_refresh_token)
                elif new_refresh_token:
                    _LOGGER.debug("服务端返回的 refreshToken 与当前相同，无需更新")
                else:
                    _LOGGER.warning("服务端未返回新的 refreshToken，当前 token 未续期")

                # 记录 refresh token 过期时间
                refresh_expire_at = token_data.get("refreshExpireAt")
                if refresh_expire_at:
                    self._refresh_token_expire_at = refresh_expire_at / 1000
                    remaining_days = (self._refresh_token_expire_at - time.time()) / 86400
                    _LOGGER.info(
                        "RefreshToken 剩余有效期: %.1f 天 (过期时间: %s)",
                        remaining_days,
                        datetime.fromtimestamp(self._refresh_token_expire_at).isoformat(),
                    )
                    if remaining_days < 3:
                        _LOGGER.error(
                            "RefreshToken 即将过期（剩余 %.1f 天）！如续期失败将需要重新登录",
                            remaining_days,
                        )
                    elif remaining_days < 7:
                        _LOGGER.warning(
                            "RefreshToken 将在 %.1f 天后过期，请关注续期状态",
                            remaining_days,
                        )
                    elif remaining_days < 0:
                        _LOGGER.error("RefreshToken 已过期！请重新配置集成以重新登录")
                else:
                    _LOGGER.warning("响应中无 refreshExpireAt 字段，无法判断 refreshToken 有效期")

                if not self._token:
                    _LOGGER.error("刷新响应中无 token 字段，centerTokenDto: %s", token_data)
                    raise GeelyAuthError("No token in response")

                return self._token

        except aiohttp.ClientError as err:
            _LOGGER.error("刷新 Token 网络错误: %s", err)
            raise GeelyApiError(f"Connection error: {err}") from err

    def _is_token_expired(self) -> bool:
        """检查 access token 是否已过期或即将过期（提前 5 分钟）。"""
        if not self._token:
            return True
        # 提前 300 秒（5 分钟）刷新，避免请求过程中过期
        return time.time() >= (self._token_expire_at - 300)

    async def _ensure_token(self) -> None:
        """确保有有效的 access token，过期则自动刷新。"""
        if self._is_token_expired():
            _LOGGER.info("Token 已过期或即将过期，自动刷新...")
            await self.refresh_access_token()
        # refresh token 剩余不足 3 天时主动再刷新一次，促使服务端返回新 refresh token
        # 每 6 小时最多尝试一次，避免频繁请求
        if (
            self._refresh_token_expire_at > 0
            and time.time() >= (self._refresh_token_expire_at - 259200)
            and time.time() - self._refresh_token_renew_attempted > 21600
        ):
            self._refresh_token_renew_attempted = time.time()
            remaining_hours = (self._refresh_token_expire_at - time.time()) / 3600
            _LOGGER.warning(
                "RefreshToken 即将过期（剩余 %.1f 小时），尝试主动续期...",
                remaining_hours,
            )
            await self.refresh_access_token()

    @staticmethod
    def _is_auth_error(code: Any, msg: str) -> bool:
        """判断 API 错误是否为认证/token 相关错误。"""
        msg_lower = str(msg).lower()
        # 常见 token 无效错误标识
        if any(kw in msg_lower for kw in ("token", "unauthorized", "登录", "认证", "expired", "过期")):
            return True
        # 特定错误码
        if str(code) in ("401", "403", "A00004", "A00005"):
            return True
        return False

    async def get_vehicle_list(self, *, _retried: bool = False) -> list[dict[str, Any]]:
        """Get list of vehicles."""
        await self._ensure_token()

        session = await self._ensure_session()
        path = "/vc/app/v1/vehicle/control/myList"
        url = f"https://{API_HOSTS['vc']}{path}"
        body = json.dumps({}, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["vc"], path, body)

        _LOGGER.debug("Calling get_vehicle_list: %s, body=%s", url, body)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Vehicle list response: %s", response_text)

                if response.status != 200:
                    _LOGGER.error("Vehicle list HTTP %s: %s", response.status, response_text[:500])
                    raise GeelyApiError(f"API request failed: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")
                if code not in (0, "0", "success"):
                    msg = data.get("msg", data.get("message", response_text[:200]))
                    if not _retried and self._is_auth_error(code, msg):
                        _LOGGER.info("车辆列表 token 无效，刷新后重试...")
                        await self.refresh_access_token()
                        return await self.get_vehicle_list(_retried=True)
                    _LOGGER.error("Vehicle list failed: %s", response_text[:500])
                    raise GeelyApiError(f"API error (code={code}): {msg}")

                vehicles = data.get("data", [])
                if vehicles:
                    self._vehicle_info = vehicles[0]
                return vehicles

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    # ========== XCHANGER 平台方法 (geely2/L7) ==========

    @staticmethod
    def _generate_xchanger_nonce(timestamp_ms: str) -> str:
        """生成 XCHANGER 请求的 nonce。"""
        hex1 = "".join(random.choices("0123456789abcdef", k=3))
        hex2 = "".join(random.choices("0123456789abcdef", k=12))
        alpha = "".join(random.choices(
            string.ascii_uppercase + string.digits, k=7))
        return f"{hex1}-{hex2}{alpha}{timestamp_ms}"

    @staticmethod
    def _xchanger_sign(
        method: str, path: str, timestamp: str, nonce: str,
        body: str | None = None,
    ) -> str:
        """XCHANGER HMAC-SHA1 签名（非标准格式，method 在末尾）。"""
        accept = "application/json;responseformat=3"
        body_bytes = (body or "").encode()
        content_md5 = base64.b64encode(
            hashlib.md5(body_bytes).digest()
        ).decode()

        if "?" in path:
            base_path, query_string = path.split("?", 1)
            params = sorted(query_string.split("&"))
            sorted_query = "&".join(params)
        else:
            base_path = path
            sorted_query = ""

        sign_headers = (
            f"x-api-signature-nonce:{nonce}\n"
            f"x-api-signature-version:1.0"
        )
        s = (
            f"{accept}\n{sign_headers}\n\n{sorted_query}\n"
            f"{content_md5}\n{timestamp}\n{method}\n{base_path}"
        )
        return base64.b64encode(
            hmac.new(
                XCHANGER_CONFIG["sign_secret"].encode(),
                s.encode(),
                hashlib.sha1,
            ).digest()
        ).decode()

    def _build_xchanger_headers(
        self, host: str, ts: str, nonce: str, signature: str,
    ) -> dict[str, str]:
        """构建 XCHANGER 请求头。"""
        return {
            "host": host,
            "x-app-id": XCHANGER_CONFIG["app_id"],
            "accept": "application/json;responseformat=3",
            "x-agent-type": "android",
            "x-device-type": "mobile",
            "x-operator-code": XCHANGER_CONFIG["operator_code"],
            "x-device-identifier": self._device_sn,
            "x-env-type": "production",
            "accept-encoding": "identity",
            "x-version": "geelygalaxyNew",
            "x-timezone": "Asia/Shanghai",
            "accept-language": "zh_CN",
            "x-api-signature-version": "1.0",
            "x-api-signature-nonce": nonce,
            "content-type": "application/json; charset=utf-8",
            "user-agent": "okhttp/4.9.3",
            "x-signature": signature,
            "x-timestamp": ts,
        }

    async def _get_xchanger_oauth_code(self) -> str:
        """用 USP token 获取 XCHANGER 的 OAuth authCode (client_id=30000022)。"""
        await self._ensure_token()
        session = await self._ensure_session()

        path = (
            "/api/v1/oauth2/code?scope=snsapiMobile,snsapiUserinfo"
            "&response_type=code&isDestruction=false&state=1&client_id="
            + XCHANGER_CONFIG["oauth_client_id"]
        )
        url = f"https://{API_HOSTS['user']}{path}"
        headers = self._build_get_headers(APP_KEYS["user"], path)

        async with session.get(url, headers=headers) as response:
            text = await response.text()
            if response.status != 200:
                raise GeelyApiError(
                    f"XCHANGER OAuth code request failed: HTTP {response.status}"
                )
            data = json.loads(text)
            if data.get("code") not in (0, "0", "success"):
                raise GeelyApiError(f"XCHANGER OAuth code failed: {text[:300]}")

            code = data.get("data", {}).get("code")
            if not code:
                raise GeelyApiError("No authCode in XCHANGER OAuth response")
            return code

    async def _get_xchanger_jwt(self) -> None:
        """获取 XCHANGER JWT: OAuth code → session → accessToken。"""
        auth_code = await self._get_xchanger_oauth_code()

        session = await self._ensure_session()
        path = "/auth/account/session/secure?identity_type=geelygalaxy"
        ts = str(int(time.time() * 1000))
        nonce = self._generate_xchanger_nonce(ts)
        body_str = json.dumps({"authCode": auth_code}, separators=(",", ":"))
        signature = self._xchanger_sign("POST", path, ts, nonce, body=body_str)
        headers = self._build_xchanger_headers(
            XCHANGER_CONFIG["user_host"], ts, nonce, signature,
        )
        url = f"https://{XCHANGER_CONFIG['user_host']}{path}"

        async with session.post(url, data=body_str, headers=headers) as response:
            text = await response.text()
            if response.status != 200:
                raise GeelyApiError(
                    f"XCHANGER session failed: HTTP {response.status}"
                )
            data = json.loads(text)
            if str(data.get("code")) != "1000":
                raise GeelyApiError(
                    f"XCHANGER session error: {text[:300]}"
                )

            xd = data.get("data", {})
            jwt = xd.get("accessToken")
            if not jwt:
                raise GeelyApiError("No accessToken in XCHANGER response")

            self._xchanger_jwt = jwt
            self._xchanger_user_id = str(xd.get("userId", ""))

            # 解析 JWT 过期时间
            try:
                payload_b64 = jwt.split(".")[1]
                padding = 4 - len(payload_b64) % 4
                if padding != 4:
                    payload_b64 += "=" * padding
                payload = json.loads(base64.b64decode(payload_b64))
                self._xchanger_jwt_expire_at = payload.get("exp", 0)
            except Exception:
                self._xchanger_jwt_expire_at = time.time() + 90 * 86400

            _LOGGER.info(
                "XCHANGER JWT 获取成功 (user_id=%s, 过期: %s)",
                self._xchanger_user_id,
                datetime.fromtimestamp(self._xchanger_jwt_expire_at).isoformat(),
            )

    async def _ensure_xchanger_token(self) -> None:
        """确保有有效的 XCHANGER JWT，过期前 1 天自动刷新。"""
        if (
            not self._xchanger_jwt
            or time.time() >= (self._xchanger_jwt_expire_at - 86400)
        ):
            await self._get_xchanger_jwt()

    async def _call_xchanger_vehicle_status(self, vin: str) -> dict[str, Any]:
        """调用 XCHANGER 车辆状态接口。"""
        await self._ensure_xchanger_token()
        session = await self._ensure_session()

        user_id = self._xchanger_user_id or ""
        path = (
            f"/remote-control/vehicle/status/{vin}"
            f"?userId={user_id}&latest=&target="
        )
        ts = str(int(time.time() * 1000))
        nonce = self._generate_xchanger_nonce(ts)
        signature = self._xchanger_sign("GET", path, ts, nonce)
        headers = self._build_xchanger_headers(
            XCHANGER_CONFIG["device_host"], ts, nonce, signature,
        )
        headers.update({
            "authorization": self._xchanger_jwt,
            "x-client-id": XCHANGER_CONFIG["client_id"],
            "x-vehicle-identifier": vin,
        })
        url = f"https://{XCHANGER_CONFIG['device_host']}{path}"

        async with session.get(url, headers=headers) as response:
            text = await response.text()
            if response.status != 200:
                raise GeelyApiError(
                    f"XCHANGER status failed: HTTP {response.status}"
                )

            data = json.loads(text)
            code = str(data.get("code", ""))

            if code == "1402":
                raise GeelyAuthError("XCHANGER JWT 已失效 (1402)")

            if code != "1000":
                raise GeelyApiError(
                    f"XCHANGER status error (code={code}): "
                    f"{data.get('message', '')}"
                )

            return data.get("data", {})

    @staticmethod
    def _transform_xchanger_to_vc_format(
        xchanger_data: dict[str, Any],
    ) -> dict[str, Any]:
        """将 XCHANGER 车辆状态转换为 VC API 兼容格式，使现有传感器无需修改。"""
        vs = xchanger_data.get("vehicleStatus", {})
        basic = vs.get("basicVehicleStatus", {})
        additional = vs.get("additionalVehicleStatus", {})
        ev = additional.get("electricVehicleStatus", {})
        climate = additional.get("climateStatus", {})
        maintenance = additional.get("maintenanceStatus", {})
        running = additional.get("runningStatus", {})
        safety = additional.get("drivingSafetyStatus", {})
        pollution = additional.get("pollutionStatus", {})

        return {
            "basicVehicleStatus": {
                "distanceToEmptyOnBatteryOnly": ev.get(
                    "distanceToEmptyOnBatteryOnly"
                ),
                "odometer": maintenance.get("odometer"),
                "distanceToEmpty": basic.get("distanceToEmpty"),
                "position": basic.get("position"),
                "engineStatus": basic.get("engineStatus"),
            },
            "vehicleBatteryStatus": {
                "chargeLevel": ev.get("chargeLevel"),
                "timeToFullyCharged": ev.get("timeToFullyCharged"),
            },
            "vehicleEnvironmentStatus": {
                "interiorTemp": climate.get("interiorTemp"),
                "exteriorTemp": climate.get("exteriorTemp"),
                "interiorPM25Level": pollution.get("interiorPM25Level"),
            },
            "vehicleRunningStatus": {
                "averPowerConsumption": ev.get("averPowerConsumption"),
            },
            "vehicleDoorCoverStatus": {
                "doorLockStatusDriver": safety.get("doorLockStatusDriver"),
            },
            "vehicleClimateStatus": {
                "preClimateActive": climate.get("preClimateActive"),
            },
            # XCHANGER 独有数据（L7 PHEV 特有）
            "_xchanger_extra": {
                "updateTime": vs.get("updateTime"),
                "fuelLevel": running.get("fuelLevel"),
                "fuelLevelPct": running.get("fuelLevelPct"),
                "chargeSts": ev.get("chargeSts"),
                "chargeIAct": ev.get("chargeIAct"),
                "chargeUAct": ev.get("chargeUAct"),
                "centralLockingStatus": safety.get("centralLockingStatus"),
                "tyreStatusDriver": maintenance.get("tyreStatusDriver"),
                "tyreStatusPassenger": maintenance.get("tyreStatusPassenger"),
                "tyreStatusDriverRear": maintenance.get(
                    "tyreStatusDriverRear"
                ),
                "tyreStatusPassengerRear": maintenance.get(
                    "tyreStatusPassengerRear"
                ),
                "winStatusDriver": climate.get("winStatusDriver"),
                "winStatusPassenger": climate.get("winStatusPassenger"),
            },
        }

    async def _get_xchanger_vehicle_status(
        self, vin: str,
    ) -> dict[str, Any]:
        """通过 XCHANGER 获取车辆状态（geely2/L7 等不支持 VC 的车型）。"""
        try:
            raw = await self._call_xchanger_vehicle_status(vin)
            return self._transform_xchanger_to_vc_format(raw)
        except GeelyAuthError:
            # JWT 失效（可能 APP 端获取了新 JWT），重新认证
            _LOGGER.info("XCHANGER JWT 失效，重新获取...")
            self._xchanger_jwt = None
            raw = await self._call_xchanger_vehicle_status(vin)
            return self._transform_xchanger_to_vc_format(raw)

    async def _ensure_vin(self, vin: str | None) -> str:
        """确保有可用的 VIN。"""
        await self._ensure_token()

        if not vin and self._vehicle_info:
            vin = self._vehicle_info.get("vin")

        if not vin:
            vehicles = await self.get_vehicle_list()
            if vehicles:
                vin = vehicles[0].get("vin")

        if not vin:
            raise GeelyApiError("No VIN available")

        return vin

    async def _call_vehicle_status(self, vin: str) -> dict[str, Any]:
        """Internal: make vehicle status API call."""
        session = await self._ensure_session()
        path = "/vc/app/v1/vehicle/control/status"
        url = f"https://{API_HOSTS['vc']}{path}"
        body = json.dumps({
            "clientType": 2,
            "statusType": "local",
            "dataTypeList": ["all"],
            "vin": vin,
        }, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["vc"], path, body)

        _LOGGER.debug("Calling get_vehicle_status: %s, vin=%s", url, vin)
        async with session.post(url, data=body, headers=headers) as response:
            response_text = await response.text()
            _LOGGER.debug("Vehicle status response: %s", response_text)

            if response.status != 200:
                _LOGGER.error("Vehicle status HTTP %s: %s", response.status, response_text[:500])
                raise GeelyApiError(f"API request failed: HTTP {response.status}")

            data = json.loads(response_text)
            code = data.get("code")
            if code not in (0, "0", "success"):
                _LOGGER.error("Vehicle status failed: %s", response_text[:500])
                raise GeelyApiError(
                    f"API error (code={code}): {data.get('msg', data.get('message', response_text[:200]))}"
                )

            return data.get("data") or {}

    async def get_vehicle_status(self, vin: str | None = None) -> dict[str, Any]:
        """Get vehicle status, auto-fallback to XCHANGER for geely2/L7.

        对于 geely2 车型（如 L7），VC API 返回 B00000 错误，
        自动切换到 XCHANGER 平台查询，并缓存此决策避免后续重复尝试 VC。
        """
        import asyncio

        vin = await self._ensure_vin(vin)

        # 已知需要使用 XCHANGER 的车型，直接走 XCHANGER
        if self._use_xchanger:
            try:
                return await self._get_xchanger_vehicle_status(vin)
            except GeelyApiError as err:
                _LOGGER.warning("XCHANGER 车辆状态查询失败: %s", err)
                return {}

        try:
            return await self._call_vehicle_status(vin)
        except GeelyApiError as err:
            err_msg = str(err)
            # B00000 表示 VC 不支持此车型，切换到 XCHANGER
            if "B00000" in err_msg:
                _LOGGER.info(
                    "VC API 返回 B00000 (geely2/L7)，切换到 XCHANGER 平台"
                )
                self._use_xchanger = True
                try:
                    return await self._get_xchanger_vehicle_status(vin)
                except GeelyApiError as xc_err:
                    _LOGGER.warning("XCHANGER 回退也失败: %s", xc_err)
                    return {}

            # 其他错误：尝试刷新 token 后重试一次
            _LOGGER.info("车辆状态首次请求失败，刷新 token 后重试...")
            await self.refresh_access_token()
            await asyncio.sleep(2)
            try:
                return await self._call_vehicle_status(vin)
            except GeelyApiError as retry_err:
                if "B00000" in str(retry_err):
                    _LOGGER.info(
                        "重试后仍 B00000，切换到 XCHANGER 平台"
                    )
                    self._use_xchanger = True
                    try:
                        return await self._get_xchanger_vehicle_status(vin)
                    except GeelyApiError as xc_err:
                        _LOGGER.warning("XCHANGER 回退也失败: %s", xc_err)
                        return {}
                raise
            except aiohttp.ClientError as err:
                raise GeelyApiError(f"Connection error: {err}") from err

    async def get_switch_status(self, vin: str | None = None) -> dict[str, Any]:
        """Get vehicle switch status (sentry mode, etc)."""
        vin = await self._ensure_vin(vin)
        return await self._vc_post("/vc/app/v1/vehicle/switch/status", {
            "clientType": 2,
            "udid": None,
            "vin": vin,
        })

    async def get_user_points(self) -> dict[str, Any]:
        """Get user points (for sign-in tracking)."""
        await self._ensure_token()

        session = await self._ensure_session()
        path = "/api/v1/point/info"
        url = f"https://{API_HOSTS['user']}{path}"
        headers = self._build_get_headers(APP_KEYS["user"], path)

        try:
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("User points response: %s", response_text)

                if response.status != 200:
                    raise GeelyApiError(f"API request failed: {response.status}")

                data = json.loads(response_text)
                return data.get("data", {})

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def _vc_post(
        self, path: str, body_dict: dict, *, _retried: bool = False
    ) -> dict[str, Any]:
        """通用 USP 网关 POST 请求，token 过期自动重试。"""
        await self._ensure_token()

        session = await self._ensure_session()
        url = f"https://{API_HOSTS['vc']}{path}"
        body = json.dumps(body_dict, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["vc"], path, body)

        _LOGGER.debug("vc POST %s body=%s", path, body)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("vc POST %s response: %s", path, response_text[:500])

                if response.status != 200:
                    raise GeelyApiError(f"API request failed: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")
                if code not in (0, "0", "success"):
                    msg = data.get("msg", data.get("message", response_text[:200]))
                    # token 无效时刷新后重试一次
                    if not _retried and self._is_auth_error(code, msg):
                        _LOGGER.info("vc POST %s token 无效，刷新后重试...", path)
                        await self.refresh_access_token()
                        return await self._vc_post(path, body_dict, _retried=True)
                    raise GeelyApiError(f"API error (code={code}): {msg}")

                return data.get("data") or {}
        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def control_switch(
        self, vin: str | None, switch_type: str, status: bool
    ) -> dict[str, Any]:
        """控制车辆开关（哨兵模式等）。

        switch_type: "vstdMode" (哨兵), "strangerMode" (陌生人预警) 等
        status: True=开启, False=关闭
        """
        vin = await self._ensure_vin(vin)
        return await self._vc_post("/vc/app/v1/vehicle/control/switch", {
            "clientType": 2,
            "switchType": switch_type,
            "switchStatus": "1" if status else "0",
            "vin": vin,
            "tspUid": None,
        })

    async def control_door(self, vin: str | None, lock: bool) -> dict[str, Any]:
        """控制车锁。lock=True 锁车，lock=False 解锁。"""
        vin = await self._ensure_vin(vin)
        return await self._vc_post("/vc/app/v1/vehicle/control/door", {
            "clientType": 2,
            "doorCtrlType": 1 if lock else 0,
            "vin": vin,
        })

    async def control_ac(
        self, vin: str | None, on: bool, temperature: float = 24.0
    ) -> dict[str, Any]:
        """控制空调。on=True 开启，on=False 关闭。"""
        vin = await self._ensure_vin(vin)
        return await self._vc_post("/vc/app/v1/vehicle/control/climate", {
            "clientType": 2,
            "climateCtrlType": 1 if on else 0,
            "temperature": temperature,
            "vin": vin,
        })

    async def control_search(self, vin: str | None) -> dict[str, Any]:
        """闪灯鸣笛寻车。"""
        vin = await self._ensure_vin(vin)
        return await self._vc_post("/vc/app/v1/vehicle/control/search", {
            "clientType": 2,
            "searchType": 0,
            "vin": vin,
        })

    async def control_window(
        self, vin: str | None, action: str
    ) -> dict[str, Any]:
        """控制车窗。action: "close", "lightOpen" (微开), "fullOpen" (全开)。"""
        vin = await self._ensure_vin(vin)
        action_map = {"close": 1, "lightOpen": 2, "fullOpen": 3}
        return await self._vc_post("/vc/app/v1/vehicle/control/window", {
            "clientType": 2,
            "windowCtrlType": action_map.get(action, 1),
            "vin": vin,
        })

    async def control_defrost(self, vin: str | None, on: bool) -> dict[str, Any]:
        """控制除霜。"""
        vin = await self._ensure_vin(vin)
        return await self._vc_post("/vc/app/v1/vehicle/control/noEngine", {
            "clientType": 2,
            "noEngineCtrlType": 3 if on else 4,
            "vin": vin,
        })

    async def control_purifier(self, vin: str | None, on: bool) -> dict[str, Any]:
        """控制空气净化。"""
        vin = await self._ensure_vin(vin)
        return await self._vc_post("/vc/app/v1/vehicle/control/noEngine", {
            "clientType": 2,
            "noEngineCtrlType": 1 if on else 2,
            "vin": vin,
        })

    async def get_last_soc(self, vin: str | None = None) -> dict[str, Any]:
        """获取最后 SOC 信息（充电状态）。"""
        vin = await self._ensure_vin(vin)
        try:
            return await self._vc_post("/vc/app/v1/reservation/getLastSoc", {"vin": vin})
        except GeelyApiError as err:
            _LOGGER.debug("获取 SOC 信息失败: %s", err)
            return {}

    async def get_charge_records(
        self, vin: str | None = None, page: int = 1, page_size: int = 10
    ) -> dict[str, Any]:
        """获取充电记录列表。"""
        vin = await self._ensure_vin(vin)
        try:
            return await self._vc_post("/vc/app/v1/charge/record/list", {
                "vin": vin,
                "pageNo": page,
                "pageSize": page_size,
            })
        except GeelyApiError as err:
            _LOGGER.debug("获取充电记录失败: %s", err)
            return {}

    async def get_reservation_info(self, vin: str | None = None) -> dict[str, Any]:
        """获取预约充电信息。"""
        vin = await self._ensure_vin(vin)
        try:
            return await self._vc_post("/vc/app/v1/reservation/info", {"vin": vin})
        except GeelyApiError as err:
            _LOGGER.debug("获取预约充电信息失败: %s", err)
            return {}

    async def get_reservation_setting(self, vin: str | None = None) -> dict[str, Any]:
        """获取充电预约设置。"""
        vin = await self._ensure_vin(vin)
        try:
            return await self._vc_post("/vc/app/v1/reservation/setting", {"vin": vin})
        except GeelyApiError as err:
            _LOGGER.debug("获取充电预约设置失败: %s", err)
            return {}

    async def validate_geetest(
        self, lot_number: str, captcha_output: str, pass_token: str, gen_time: str
    ) -> str:
        """调用吉利后端校验极验验证码结果。

        Args:
            lot_number: 极验返回的 lot_number
            captcha_output: 极验返回的 captcha_output
            pass_token: 极验返回的 pass_token
            gen_time: 极验返回的 gen_time

        Returns:
            certifyId（优先使用服务端返回的值，否则回退到 lot_number）
        """
        session = await self._ensure_session()

        body_dict = {
            "lotNumber": lot_number,
            "captchaOutput": captcha_output,
            "passToken": pass_token,
            "genTime": gen_time,
            "captchaId": "2baef8ee692c27f1c8a0632e560242d7",
        }

        path = "/api/v1/security/geeTestV4/validate"
        url = f"https://{API_HOSTS['user']}{path}"
        body = json.dumps(body_dict, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["user"], path, body)

        _LOGGER.debug("Calling validate_geetest: %s", url)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("GeeTest validate response: %s", response_text)

                if response.status != 200:
                    _LOGGER.error(
                        "GeeTest validate HTTP %s: %s",
                        response.status,
                        response_text[:500],
                    )
                    raise GeelyApiError(f"验证码校验失败: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")

                if code not in ("success", 0, "0"):
                    msg = data.get("msg", data.get("message", "未知错误"))
                    _LOGGER.error("GeeTest validate failed: %s", response_text[:500])
                    raise GeelyApiError(f"验证码校验失败: {msg}")

                # 从响应中提取 certifyId，如果没有则回退到 lot_number
                certify_id = lot_number
                validate_data = data.get("data")
                if validate_data and isinstance(validate_data, dict) and validate_data.get("certifyId"):
                    certify_id = validate_data["certifyId"]

                _LOGGER.info("极验验证码校验成功, certifyId: %s", certify_id)
                return certify_id

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"连接错误: {err}") from err

    async def password_login(
        self, phone: str, password: str, certify_id: str
    ) -> dict[str, str]:
        """使用手机号+密码登录。

        密码加密方式为白盒 SM4 (ECB)，由 APP 内 libwhite-box.so 实现，
        密钥嵌入白盒查找表中无法直接提取。
        因此 password 参数应传入已加密的密码 hex 字符串（通过抓包或 Frida 获取）。

        Args:
            phone: 手机号
            password: 已加密的密码（白盒SM4 hex字符串，32字符）
            certify_id: 极验验证码的 lot_number

        Returns:
            包含 token 和 refresh_token 的字典
        """
        session = await self._ensure_session()

        encrypted_password = password

        # 生成 deviceId
        device_id = hashlib.md5(self._device_sn.encode("utf-8")).hexdigest()

        body_dict = {
            "deviceType": "android",
            "appVersion": "1.46.0",
            "password": encrypted_password,
            "mobile": phone,
            "deviceModel": "HomeAssistant",
            "deviceId": device_id,
            "certifyId": certify_id,
        }

        path = "/api/v1/login/pwdLogin"
        url = f"https://{API_HOSTS['user']}{path}"
        body = json.dumps(body_dict, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["user"], path, body)

        _LOGGER.debug("Calling password_login: %s", url)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Password login response: %s", response_text)

                if response.status != 200:
                    _LOGGER.error(
                        "Password login HTTP %s: %s",
                        response.status,
                        response_text[:500],
                    )
                    raise GeelyAuthError(f"登录失败: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")

                if code not in ("success", 0, "0"):
                    msg = data.get("msg", data.get("message", "未知错误"))
                    _LOGGER.error("Password login failed: %s", response_text[:500])
                    raise GeelyAuthError(f"登录失败: {msg}")

                token_data = data.get("data", {}).get("centerTokenDto", {})
                self._token = token_data.get("token")
                new_refresh_token = token_data.get("refreshToken")

                if not self._token or not new_refresh_token:
                    raise GeelyAuthError("登录响应中缺少 token 信息")

                self._refresh_token = new_refresh_token
                _LOGGER.info("密码登录成功")

                return {
                    "token": self._token,
                    "refresh_token": self._refresh_token,
                }

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"连接错误: {err}") from err

    async def send_sms_code(self, phone: str, certify_id: str) -> bool:
        """发送短信登录验证码。

        Args:
            phone: 手机号
            certify_id: 极验验证码的 lot_number

        Returns:
            是否发送成功
        """
        session = await self._ensure_session()

        body_dict = {
            "mobile": phone,
            "certifyId": certify_id,
        }

        path = "/api/v1/login/sendSms"
        url = f"https://{API_HOSTS['user']}{path}"
        body = json.dumps(body_dict, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["user"], path, body)

        _LOGGER.debug("Calling send_sms_code: %s", url)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Send SMS response: %s", response_text)

                if response.status != 200:
                    raise GeelyApiError(f"发送验证码失败: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")
                msg = data.get("msg", data.get("message", ""))

                if code in ("success", 0, "0"):
                    _LOGGER.info("短信验证码发送成功")
                    return True

                if "频繁" in str(msg) or "frequent" in str(msg).lower():
                    raise GeelyApiError("验证码发送太频繁，请稍后再试")

                raise GeelyApiError(f"发送验证码失败: {msg}")

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"连接错误: {err}") from err

    async def sms_login(
        self, phone: str, sms_code: str, certify_id: str
    ) -> dict[str, str]:
        """使用手机号+短信验证码登录。

        无需密码加密，适合开源项目使用。
        mobileCodeLogin 端点使用 query params（非 JSON body）。

        Args:
            phone: 手机号
            sms_code: 短信验证码（6位数字）
            certify_id: 极验验证码的 lot_number

        Returns:
            包含 token 和 refresh_token 的字典
        """
        session = await self._ensure_session()

        device_id = hashlib.md5(self._device_sn.encode("utf-8")).hexdigest()

        # mobileCodeLogin 使用 query params + POST（空body）
        params = (
            f"mobile={phone}"
            f"&verificationCode={sms_code}"
            f"&certifyId={certify_id}"
            f"&deviceType=android"
            f"&appVersion=1.46.0"
            f"&deviceId={device_id}"
            f"&deviceModel=HomeAssistant"
        )
        path = f"/api/v1/login/mobileCodeLogin?{params}"
        url = f"https://{API_HOSTS['user']}{path}"
        body = json.dumps({}, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["user"], path, body)

        _LOGGER.debug("Calling sms_login: %s", url)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("SMS login response: %s", response_text)

                if response.status != 200:
                    _LOGGER.error(
                        "SMS login HTTP %s: %s",
                        response.status, response_text[:500],
                    )
                    raise GeelyAuthError(f"登录失败: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")

                if code not in ("success", 0, "0"):
                    msg = data.get("msg", data.get("message", "未知错误"))
                    _LOGGER.error("SMS login failed: %s", response_text[:500])
                    raise GeelyAuthError(f"登录失败: {msg}")

                token_data = data.get("data", {}).get("centerTokenDto", {})
                self._token = token_data.get("token")
                new_refresh_token = token_data.get("refreshToken")

                if not self._token or not new_refresh_token:
                    raise GeelyAuthError("登录响应中缺少 token 信息")

                self._refresh_token = new_refresh_token
                _LOGGER.info("短信验证码登录成功")

                return {
                    "token": self._token,
                    "refresh_token": self._refresh_token,
                }

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"连接错误: {err}") from err

    async def test_connection(self) -> bool:
        """Test the API connection."""
        try:
            await self.refresh_access_token()
            return True
        except GeelyApiError as err:
            _LOGGER.error("Connection test failed: %s", err)
            return False

    @property
    def vehicle_info(self) -> dict[str, Any]:
        """Get cached vehicle info."""
        return self._vehicle_info

    # ========== 充电桩服务 API (api-recharge.geely.com) ==========

    def _sort_query_params(self, path: str) -> str:
        """将 URL 参数按字母顺序排序（签名需要）。"""
        if '?' not in path:
            return path
        base, query = path.split('?', 1)
        params = query.split('&')
        params.sort()
        return f"{base}?{'&'.join(params)}"

    def _calculate_recharge_signature(
        self,
        method: str,
        accept: str,
        content_md5: str,
        content_type: str,
        date: str,
        app_key: str,
        nonce: str,
        timestamp: str,
        path: str,
    ) -> str:
        """计算充电服务 API 签名（参数需排序）。"""
        sorted_path = self._sort_query_params(path)
        string_to_sign = f"{method}\n{accept}\n{content_md5}\n{content_type}\n{date}\n"
        string_to_sign += f"x-ca-key:{app_key}\nx-ca-nonce:{nonce}\nx-ca-timestamp:{timestamp}\n{sorted_path}"

        secret = APP_SECRETS.get(app_key)
        if not secret:
            raise GeelyApiError(f"Unknown AppKey: {app_key}")

        signature = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        return base64.b64encode(signature).decode("utf-8")

    def _build_recharge_headers(
        self, method: str, path: str, body: str | None = None
    ) -> dict[str, str]:
        """构建充电服务 API 请求头。"""
        date, timestamp = self._format_date_and_timestamp()
        nonce = self._generate_uuid()
        app_key = APP_KEYS["recharge"]

        content_md5 = ""
        if body:
            content_md5 = self._calculate_content_md5(body)

        ct = "application/json; charset=utf-8" if method == "POST" else "application/x-www-form-urlencoded; charset=utf-8"

        signature = self._calculate_recharge_signature(
            method=method,
            accept="application/json; charset=utf-8",
            content_md5=content_md5,
            content_type=ct,
            date=date,
            app_key=app_key,
            nonce=nonce,
            timestamp=timestamp,
            path=path,
        )

        headers = {
            "channelid": RECHARGE_CONFIG["channel_id"],
            "accept": "application/json; charset=utf-8",
            "date": date,
            "x-ca-timestamp": timestamp,
            "x-ca-nonce": nonce,
            "user-agent": "ALIYUN-ANDROID-UA",
            "x-ca-key": app_key,
            "ca_version": "1",
            "x-ca-signature-headers": "x-ca-key,x-ca-nonce,x-ca-timestamp",
            "x-ca-signature": signature,
            "host": API_HOSTS["recharge"],
            "content-type": ct,
        }

        if content_md5:
            headers["content-md5"] = content_md5

        if self._recharge_auth_token:
            headers["x-auth-token"] = self._recharge_auth_token
            headers["token"] = self._recharge_auth_token
        else:
            headers["token"] = ""

        return headers

    async def get_oauth_code(self) -> str:
        """获取充电服务的 OAuth 授权码。"""
        _LOGGER.warning("[诊断] 开始获取 OAuth 授权码...")
        await self._ensure_token()

        session = await self._ensure_session()
        path = f"/api/v1/oauth2/code?scope=snsapiUserinfo&response_type=code&isDestruction=false&state=1&client_id={RECHARGE_CONFIG['oauth_client_id']}"
        url = f"https://{API_HOSTS['user']}{path}"
        headers = self._build_get_headers(APP_KEYS["user"], path)

        try:
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.warning("[诊断] OAuth code 响应: HTTP %s, body=%s", response.status, response_text[:300])

                if response.status != 200:
                    raise GeelyApiError(f"OAuth code request failed: HTTP {response.status}")

                data = json.loads(response_text)
                if data.get("code") != "success":
                    raise GeelyApiError(f"OAuth code failed: {data.get('msg', response_text[:200])}")

                oauth_code = data.get("data", {}).get("code")
                if not oauth_code:
                    raise GeelyApiError("No OAuth code in response")

                _LOGGER.warning("[诊断] OAuth 授权码获取成功")
                return oauth_code

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def get_recharge_auth_token(self) -> str:
        """使用 OAuth 码换取充电服务 authToken。"""
        oauth_code = await self.get_oauth_code()

        session = await self._ensure_session()
        path = f"/login/auth-token?code={oauth_code}"
        url = f"https://{API_HOSTS['recharge']}{path}"
        headers = self._build_recharge_headers("GET", path)

        _LOGGER.debug("Calling get_recharge_auth_token: %s", url)
        try:
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Recharge auth token response: %s", response_text)

                if response.status != 200:
                    raise GeelyApiError(f"Auth token request failed: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")
                if code not in (0, "0", 1, "1", 200, "200", "success", "SUCCESS"):
                    raise GeelyApiError(f"Auth token failed (code={code}): {data.get('msg', response_text[:200])}")

                # data 可能是数组或对象
                result = data.get("data")
                auth_token = None
                if isinstance(result, list) and len(result) > 0:
                    auth_token = result[0].get("authToken")
                elif isinstance(result, dict):
                    auth_token = result.get("authToken")

                if not auth_token:
                    raise GeelyApiError("No authToken in response")

                self._recharge_auth_token = auth_token
                _LOGGER.info("Recharge auth token obtained successfully")
                return auth_token

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def _ensure_recharge_auth(self) -> None:
        """确保有有效的充电服务 authToken。"""
        if not self._recharge_auth_token:
            _LOGGER.warning("[诊断] 充电服务 authToken 为空，开始获取...")
            await self.get_recharge_auth_token()
            _LOGGER.warning("[诊断] 充电服务 authToken 获取成功")

    async def _recharge_post(
        self, path: str, body_dict: dict, *, _retried: bool = False
    ) -> dict[str, Any]:
        """充电服务 POST 请求，token 过期自动重试。"""
        await self._ensure_recharge_auth()

        session = await self._ensure_session()
        url = f"https://{API_HOSTS['recharge']}{path}"
        body = json.dumps(body_dict, separators=(",", ":"))
        headers = self._build_recharge_headers("POST", path, body)

        _LOGGER.debug("recharge POST %s body=%s", path, body)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("recharge POST %s response: %s", path, response_text[:500])

                if response.status != 200:
                    raise GeelyApiError(f"API request failed: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")
                if code not in (0, "0", 1, "1", 200, "200", "success", "SUCCESS"):
                    msg = data.get("msg", data.get("message", response_text[:200]))
                    # 充电服务 token 过期时重新获取 authToken 后重试
                    if not _retried and self._is_auth_error(code, msg):
                        _LOGGER.info("充电服务 token 无效，重新获取后重试...")
                        self._recharge_auth_token = None
                        return await self._recharge_post(path, body_dict, _retried=True)
                    raise GeelyApiError(f"API error (code={code}): {msg}")

                return data.get("data") or {}

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def get_home_charger_list(self) -> list[dict[str, Any]]:
        """获取家用充电桩列表。"""
        try:
            _LOGGER.warning("[诊断] 开始获取家用充电桩列表...")
            result = await self._recharge_post("/app/hcharger/getMyPilingsNew", {})
            _LOGGER.warning("[诊断] 充电桩列表 API 返回: %s", result)
            if isinstance(result, list) and result:
                self._piling_code = result[0].get("pilingsCode")
                _LOGGER.warning("[诊断] 检测到充电桩，编码: %s", self._piling_code)
                return result
            _LOGGER.warning("[诊断] 未检测到充电桩（列表为空或格式错误）")
            return []
        except GeelyApiError as err:
            _LOGGER.warning("[诊断] 获取充电桩列表失败: %s", err)
            return []

    async def get_home_charger_status(self, piling_code: str | None = None) -> dict[str, Any]:
        """查询家用充电桩状态。"""
        code = piling_code or self._piling_code
        if not code:
            chargers = await self.get_home_charger_list()
            if chargers:
                code = chargers[0].get("pilingsCode")
        if not code:
            _LOGGER.info("无可用充电桩编码")
            return {}

        try:
            _LOGGER.info("查询充电桩状态，编码: %s", code)
            result = await self._recharge_post("/app/hcharger/queryEquipStatus", {"pilingsCode": code})
            _LOGGER.info("充电桩状态 API 返回: %s (类型: %s)", result, type(result).__name__)
            # API 返回数组，取第一个元素
            if isinstance(result, list) and result:
                status_data = result[0]
                _LOGGER.info("充电桩状态数据: %s", status_data)
                return status_data
            return result if isinstance(result, dict) else {}
        except GeelyApiError as err:
            _LOGGER.warning("查询充电桩状态失败: %s", err)
            return {}

    async def get_home_charger_charging_data(self, piling_code: str | None = None) -> dict[str, Any]:
        """获取充电中的实时数据。"""
        code = piling_code or self._piling_code
        if not code:
            return {}

        try:
            return await self._recharge_post("/app/hcharger/getChargingData", {"pilingsCode": code})
        except GeelyApiError as err:
            _LOGGER.debug("获取充电数据失败: %s", err)
            return {}

    async def get_home_charger_records(
        self, piling_code: str | None = None, page: int = 1, page_size: int = 10
    ) -> dict[str, Any]:
        """获取家用充电桩充电记录。"""
        code = piling_code or self._piling_code
        if not code:
            return {}

        try:
            return await self._recharge_post("/app/hcharger/getMyChargeRecordByPage", {
                "pageNumber": page,
                "pageSize": page_size,
                "pilingsCode": code,
            })
        except GeelyApiError as err:
            _LOGGER.debug("获取充电记录失败: %s", err)
            return {}

    async def get_home_charger_detail(self, piling_code: str | None = None) -> dict[str, Any]:
        """获取家用充电桩详情。"""
        code = piling_code or self._piling_code
        if not code:
            return {}

        try:
            result = await self._recharge_post("/app/hcharger/getPlingsDetailNew", {"pilingsCode": code})
            # API 返回数组，取第一个元素
            if isinstance(result, list) and result:
                return result[0]
            return result if isinstance(result, dict) else {}
        except GeelyApiError as err:
            _LOGGER.debug("获取充电桩详情失败: %s", err)
            return {}

    async def get_home_charger_last_record(self, piling_code: str | None = None) -> dict[str, Any]:
        """获取最后一条充电记录。"""
        code = piling_code or self._piling_code
        if not code:
            return {}

        try:
            return await self._recharge_post("/app/hcharger/queryLastRecord", {"pilingsCode": code})
        except GeelyApiError as err:
            _LOGGER.debug("获取最后充电记录失败: %s", err)
            return {}

    async def start_home_charger(self, piling_code: str | None = None) -> dict[str, Any]:
        """启动家用充电桩充电。"""
        code = piling_code or self._piling_code
        if not code:
            raise GeelyApiError("无可用充电桩")

        return await self._recharge_post("/app/hcharger/startCharge", {
            "type": "0",
            "pilingsCode": code,
            "reqId": "",
        })

    async def stop_home_charger(self, piling_code: str | None = None) -> dict[str, Any]:
        """停止家用充电桩充电。"""
        code = piling_code or self._piling_code
        if not code:
            raise GeelyApiError("无可用充电桩")

        return await self._recharge_post("/app/hcharger/stopCharge", {
            "pilingsCode": code,
        })

    @property
    def piling_code(self) -> str | None:
        """Get cached piling code."""
        return self._piling_code

    # ========== 签到相关 ==========

    def _build_sign_headers(self, path: str, body: str = "") -> dict[str, str]:
        """构建签到 API 请求头（与 JS getGetHeader/getPostHeader 一致）。"""
        date, timestamp = self._format_date_and_timestamp()
        nonce = self._generate_uuid()
        content_md5 = self._calculate_content_md5(body) if body else ""

        signature = self._calculate_signature(
            method="GET" if not body else "POST",
            accept="application/json; charset=utf-8",
            content_md5=content_md5,
            content_type="application/json; charset=utf-8",
            date=date,
            app_key=APP_KEYS_SIGN,
            nonce=nonce,
            timestamp=timestamp,
            path=path,
        )

        headers = {
            "date": date,
            "x-ca-signature": signature,
            "x-ca-nonce": nonce,
            "x-ca-key": APP_KEYS_SIGN,
            "ca_version": "1",
            "accept": "application/json; charset=utf-8",
            "usetoken": "1",
            "x-ca-timestamp": timestamp,
            "x-ca-signature-headers": "x-ca-nonce,x-ca-timestamp,x-ca-key",
            "x-refresh-token": "true",
            "content-type": "application/json; charset=utf-8",
            "user-agent": "ALIYUN-ANDROID-UA",
            "deviceSN": self._device_sn,
            "txCookie": "",
            "appId": "galaxy-app",
            "appVersion": "1.46.0",
            "platform": "Android",
            "Cache-Control": "no-cache",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "sweet_security_info": '{"appVersion":"1.46.0","platform":"android"}',
            "methodtype": "6",
            "contenttype": "application/json",
            "gl_dev_id": self._device_sn,
            "gl_dev_model": "HomeAssistant",
            "gl_dev_brand": "HomeAssistant",
            "gl_dev_platform": "android",
            "gl_app_version": "1.46.0",
            "gl_os_version": "33",
            "gl_app_build": "146000098",
        }
        # POST 请求必须包含 content-md5 头
        if body:
            headers["content-md5"] = content_md5
        if self._token:
            headers["token"] = self._token
        return headers

    async def get_sign_state(self) -> bool:
        """查询今日签到状态。返回 True=已签到，False=未签到。"""
        await self._ensure_token()
        session = await self._ensure_session()
        path = "/app/v1/sign/state"
        url = f"https://{API_HOSTS['app']}{path}"
        headers = self._build_sign_headers(path)

        _LOGGER.warning("[签到API] 查询签到状态: %s", url)
        _LOGGER.warning("[签到API] 请求头: %s", json.dumps(headers, indent=2, ensure_ascii=False))
        async with session.get(url, headers=headers) as response:
            response_text = await response.text()
            _LOGGER.warning("[签到API] 响应状态: %d", response.status)
            _LOGGER.warning("[签到API] 响应体: %s", response_text[:500])

            if response.status != 200:
                raise GeelyApiError(f"签到状态查询失败: HTTP {response.status}")

            data = json.loads(response_text)
            code = data.get("code")
            msg = data.get("msg", data.get("message", ""))
            _LOGGER.warning("[签到API] code=%s, msg=%s", code, msg)
            if code not in (0, "0", "success"):
                raise GeelyApiError(f"签到状态查询失败: code={code} msg={msg}")

            # data 为 true=已签到，false=未签到
            signed = data.get("data") is True
            _LOGGER.warning("[签到API] 签到状态: %s", "已签到" if signed else "未签到")
            return signed

    async def do_sign(self) -> dict[str, Any]:
        """执行签到。签到成功返回结果，失败抛出异常。"""
        await self._ensure_token()
        session = await self._ensure_session()
        path = "/app/v1/sign/add"
        url = f"https://{API_HOSTS['app']}{path}"
        body_dict = {"signType": 0}
        body = json.dumps(body_dict, separators=(",", ":"))
        headers = self._build_sign_headers(path, body)

        _LOGGER.warning("[签到API] 执行签到: %s", url)
        _LOGGER.warning("[签到API] 请求体: %s", body)
        _LOGGER.warning("[签到API] 请求头: %s", json.dumps(headers, indent=2, ensure_ascii=False))
        async with session.post(url, data=body, headers=headers) as response:
            response_text = await response.text()
            _LOGGER.warning("[签到API] 响应状态: %d", response.status)
            _LOGGER.warning("[签到API] 响应体: %s", response_text[:500])

            if response.status != 200:
                raise GeelyApiError(f"签到失败: HTTP {response.status}")

            data = json.loads(response_text)
            code = data.get("code")
            msg = data.get("msg", data.get("message", ""))
            _LOGGER.warning("[签到API] code=%s, msg=%s", code, msg)
            if code not in (0, "0", "success"):
                raise GeelyApiError(f"签到失败: code={code} msg={msg}")

            _LOGGER.warning("[签到API] 签到成功! data=%s", data.get("data"))
            return data
