"""
API client for Geely Galaxy.

基于 geely-galaxy-assistant 项目实现
https://github.com/suyunkai/geely-galaxy-assistant
"""
import hashlib
import hmac
import base64
import time
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
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    def _format_date(self) -> str:
        """Format date for API request (GMT format)."""
        now = datetime.now(timezone.utc)
        # 格式: Wed, 22 Jan 2025 08:30:00 GMT
        return now.strftime("%a, %d %b %Y %H:%M:%S GMT")

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
        date = self._format_date()
        timestamp = str(int(time.time() * 1000))
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
            "x-ca-timestamp": timestamp,
            "x-ca-signature-headers": "x-ca-nonce,x-ca-timestamp,x-ca-key",
            "content-type": "application/x-www-form-urlencoded; charset=utf-8",
            "user-agent": "ALIYUN-ANDROID-UA",
            "deviceSN": self._device_sn,
            "appId": "galaxy-app",
            "appVersion": "1.39.0",
            "platform": "Android",
        }

        if self._token:
            headers["token"] = self._token

        # 根据 AppKey 设置不同的 host 和参数
        if app_key == APP_KEYS["user"]:
            headers["host"] = API_HOSTS["user"]
            headers["usetoken"] = "true"
            headers["taenantid"] = "569001701001"
        else:
            headers["host"] = API_HOSTS["app"]
            headers["usetoken"] = "1"
            headers["x-refresh-token"] = "true"

        return headers

    def _build_post_headers(
        self, app_key: str, path: str, body: str
    ) -> dict[str, str]:
        """Build POST request headers with signature."""
        date = self._format_date()
        timestamp = str(int(time.time() * 1000))
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
            "content-md5": content_md5,
            "x-ca-timestamp": timestamp,
            "x-ca-signature-headers": signature_headers,
            "x-refresh-token": "true",
            "user-agent": "ALIYUN-ANDROID-UA",
            "deviceSN": self._device_sn,
            "appId": "galaxy-app",
            "appVersion": "1.39.0",
            "platform": "Android",
            "content-type": "application/json; charset=utf-8",
        }

        if self._token:
            headers["token"] = self._token

        # 根据 AppKey 设置不同的 host
        if app_key == APP_KEYS["user"]:
            headers["host"] = API_HOSTS["user"]
            headers["usetoken"] = "true"
            headers["taenantid"] = "569001701001"
            del headers["x-refresh-token"]
        elif app_key == APP_KEYS["vc"]:
            headers["host"] = API_HOSTS["vc"]
            headers["usetoken"] = "1"
        else:
            headers["host"] = API_HOSTS["app"]
            headers["usetoken"] = "1"

        return headers

    async def refresh_access_token(self) -> str:
        """Refresh the access token."""
        session = await self._ensure_session()

        path = f"/api/v1/login/refresh?refreshToken={self._refresh_token}"
        url = f"https://{API_HOSTS['user']}{path}"
        headers = self._build_get_headers(APP_KEYS["user"], path)

        try:
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Refresh token response: %s", response_text)

                if response.status != 200:
                    raise GeelyAuthError(
                        f"Failed to refresh token: {response.status}"
                    )

                data = json.loads(response_text)

                if data.get("code") != "success":
                    raise GeelyAuthError(
                        f"Token refresh failed: {data.get('message', 'Unknown error')}"
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

        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Vehicle list response: %s", response_text)

                if response.status != 200:
                    raise GeelyApiError(f"API request failed: {response.status}")

                data = json.loads(response_text)
                if data.get("code") != 0:
                    raise GeelyApiError(f"API error: {data.get('message')}")

                vehicles = data.get("data", [])
                if vehicles:
                    self._vehicle_info = vehicles[0]
                return vehicles

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def get_vehicle_status(self, vin: str | None = None) -> dict[str, Any]:
        """Get vehicle status."""
        if not self._token:
            await self.refresh_access_token()

        if not vin and self._vehicle_info:
            vin = self._vehicle_info.get("vin")

        if not vin:
            # 先获取车辆列表
            vehicles = await self.get_vehicle_list()
            if vehicles:
                vin = vehicles[0].get("vin")

        if not vin:
            raise GeelyApiError("No VIN available")

        session = await self._ensure_session()
        path = "/vc/app/v1/vehicle/status/query"
        url = f"https://{API_HOSTS['vc']}{path}"
        body = json.dumps({"vin": vin}, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["vc"], path, body)

        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Vehicle status response: %s", response_text)

                if response.status != 200:
                    raise GeelyApiError(f"API request failed: {response.status}")

                data = json.loads(response_text)
                if data.get("code") != 0:
                    raise GeelyApiError(f"API error: {data.get('message')}")

                return data.get("data", {})

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
        body = json.dumps({"vin": vin}, separators=(",", ":"))
        headers = self._build_post_headers(APP_KEYS["vc"], path, body)

        try:
            async with session.post(url, data=body, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("Switch status response: %s", response_text)

                if response.status != 200:
                    raise GeelyApiError(f"API request failed: {response.status}")

                data = json.loads(response_text)
                if data.get("code") != 0:
                    raise GeelyApiError(f"API error: {data.get('message')}")

                return data.get("data", {})

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
