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
from typing import Any
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
}

# API 域名配置
API_HOSTS = {
    "user": "galaxy-user-api.geely.com",
    "app": "galaxy-app.geely.com",
    "vc": "galaxy-vc.geely.com",
}

# AppKey 配置
APP_KEYS = {
    "user": "204179735",  # 用户API
    "app": "204167276",   # H5端应用API
    "vc": "204373120",    # 车辆控制API
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
        self._own_session = False
        self._vehicle_info: dict = {}

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
        string_to_sign += path

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
            "appVersion": "1.39.0",
            "platform": "Android",
            "Cache-Control": "no-cache",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "token": self._token or "",
        }

        # 根据 AppKey 设置不同的参数（与 JS getGetHeader 一致）
        if app_key == APP_KEYS["user"]:
            headers["usetoken"] = "true"
            headers["host"] = API_HOSTS["user"]
            headers["taenantid"] = "569001701001"
            headers["svcsid"] = ""
            del headers["x-refresh-token"]

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
            "appVersion": "1.39.0",
            "platform": "Android",
            "Cache-Control": "no-cache",
            "sweet_security_info": '{"appVersion":"1.27.0","platform":"android"}',
            "methodtype": "6",
            "contenttype": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "token": self._token or "",
        }

        # 根据 AppKey 设置不同的参数（与 JS getPostHeader 一致）
        if app_key == APP_KEYS["user"]:
            headers["usetoken"] = "true"
            headers["host"] = API_HOSTS["user"]
            headers["taenantid"] = "569001701001"
            headers["svcsid"] = ""
            del headers["x-refresh-token"]
        elif app_key == APP_KEYS["vc"]:
            headers["host"] = API_HOSTS["vc"]
        else:
            headers["host"] = API_HOSTS["app"]

        return headers

    async def refresh_access_token(self) -> str:
        """Refresh the access token."""
        session = await self._ensure_session()

        path = f"/api/v1/login/refresh?refreshToken={self._refresh_token}"
        url = f"https://{API_HOSTS['user']}{path}"
        headers = self._build_get_headers(APP_KEYS["user"], path)

        _LOGGER.debug("Calling refresh_access_token: %s", url)
        try:
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Refresh token response: %s", response_text)

                if response.status != 200:
                    _LOGGER.error("Refresh token HTTP %s: %s", response.status, response_text[:500])
                    raise GeelyAuthError(
                        f"Failed to refresh token: HTTP {response.status}"
                    )

                data = json.loads(response_text)
                code = data.get("code")

                if code not in ("success", 0, "0"):
                    _LOGGER.error("Refresh token failed: %s", response_text[:500])
                    raise GeelyAuthError(
                        f"Token refresh failed (code={code}): {data.get('msg', data.get('message', 'Unknown error'))}"
                    )

                token_data = data.get("data", {}).get("centerTokenDto", {})
                self._token = token_data.get("token")

                # 更新 refresh token（如果返回了新的）
                new_refresh_token = token_data.get("refreshToken")
                if new_refresh_token:
                    self._refresh_token = new_refresh_token

                if not self._token:
                    raise GeelyAuthError("No token in response")

                _LOGGER.info("Token refreshed successfully")
                return self._token

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def get_vehicle_list(self) -> list[dict[str, Any]]:
        """Get list of vehicles."""
        if not self._token:
            await self.refresh_access_token()

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
                    _LOGGER.error("Vehicle list failed: %s", response_text[:500])
                    raise GeelyApiError(
                        f"API error (code={code}): {data.get('msg', data.get('message', response_text[:200]))}"
                    )

                vehicles = data.get("data", [])
                if vehicles:
                    self._vehicle_info = vehicles[0]
                return vehicles

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

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
        """Get vehicle status with retry on failure."""
        import asyncio

        if not self._token:
            await self.refresh_access_token()

        if not vin and self._vehicle_info:
            vin = self._vehicle_info.get("vin")

        if not vin:
            vehicles = await self.get_vehicle_list()
            if vehicles:
                vin = vehicles[0].get("vin")

        if not vin:
            raise GeelyApiError("No VIN available")

        try:
            return await self._call_vehicle_status(vin)
        except GeelyApiError:
            # B00000 可能是 token 失效或服务端限流，刷新 token 后延迟重试
            _LOGGER.info("车辆状态首次请求失败，刷新 token 后重试...")
            await self.refresh_access_token()
            await asyncio.sleep(2)
            try:
                return await self._call_vehicle_status(vin)
            except aiohttp.ClientError as err:
                raise GeelyApiError(f"Connection error: {err}") from err

    async def get_switch_status(self, vin: str | None = None) -> dict[str, Any]:
        """Get vehicle switch status (sentry mode, etc)."""
        if not self._token:
            await self.refresh_access_token()

        if not vin and self._vehicle_info:
            vin = self._vehicle_info.get("vin")

        if not vin:
            vehicles = await self.get_vehicle_list()
            if vehicles:
                vin = vehicles[0].get("vin")

        if not vin:
            raise GeelyApiError("No VIN available")

        session = await self._ensure_session()
        path = "/vc/app/v1/vehicle/switch/status"
        url = f"https://{API_HOSTS['vc']}{path}"
        body = json.dumps({
            "clientType": 2,
            "udid": None,
            "vin": vin,
        }, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["vc"], path, body)

        _LOGGER.debug("Calling get_switch_status: %s, vin=%s", url, vin)
        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Switch status response: %s", response_text)

                if response.status != 200:
                    _LOGGER.error("Switch status HTTP %s: %s", response.status, response_text[:500])
                    raise GeelyApiError(f"API request failed: HTTP {response.status}")

                data = json.loads(response_text)
                code = data.get("code")
                if code not in (0, "0", "success"):
                    raise GeelyApiError(
                        f"API error (code={code}): {data.get('msg', data.get('message', response_text[:200]))}"
                    )

                return data.get("data") or {}

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def get_user_points(self) -> dict[str, Any]:
        """Get user points (for sign-in tracking)."""
        if not self._token:
            await self.refresh_access_token()

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
