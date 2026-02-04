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
    "204195485": "CqPwP83wzdjesmLeDuzK6SljsYN5PvRM",  # 充电服务 API
}

# API 域名配置
API_HOSTS = {
    "user": "galaxy-user-api.geely.com",
    "app": "galaxy-app.geely.com",
    "vc": "galaxy-vc.geely.com",
    "recharge": "api-recharge.geely.com",  # 充电桩服务
}

# AppKey 配置
APP_KEYS = {
    "user": "204179735",  # 用户API
    "app": "204167276",   # H5端应用API
    "vc": "204373120",    # 车辆控制API
    "recharge": "204195485",  # 充电桩服务 API
}

# 充电服务配置
RECHARGE_CONFIG = {
    "channel_id": "01701001",
    "oauth_client_id": "30000023",
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
        self._recharge_auth_token: str | None = None  # 充电服务 authToken
        self._own_session = False
        self._vehicle_info: dict = {}
        self._piling_code: str | None = None  # 缓存充电桩编码

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
            "appVersion": "1.45.0",
            "platform": "Android",
            "Cache-Control": "no-cache",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }

        # 根据 AppKey 设置不同的参数（与 JS getGetHeader 一致）
        if app_key == APP_KEYS["user"]:
            headers["usetoken"] = "true"
            headers["host"] = API_HOSTS["user"]
            headers["taenantid"] = "569001701001"
            headers["svcsid"] = ""
            # 用户 API 的签名头顺序必须是字母顺序
            headers["x-ca-signature-headers"] = "x-ca-key,x-ca-nonce,x-ca-timestamp"
            del headers["x-refresh-token"]

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
            "appVersion": "1.45.0",
            "platform": "Android",
            "Cache-Control": "no-cache",
            "sweet_security_info": '{"appVersion":"1.45.0","platform":"android"}',
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
            headers["taenantid"] = "569001701001"
            headers["svcsid"] = ""
            del headers["x-refresh-token"]
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

    async def _ensure_vin(self, vin: str | None) -> str:
        """确保有可用的 VIN。"""
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
        """Get vehicle status with retry on failure.

        对于 geely2 车型（如 L7），服务端可能返回 B00000 错误（请求被拒绝），
        此时返回空字典而不是抛出异常，以便其他功能（开关状态、远程控制）继续正常工作。
        """
        import asyncio

        vin = await self._ensure_vin(vin)

        try:
            return await self._call_vehicle_status(vin)
        except GeelyApiError as err:
            err_msg = str(err)
            # B00000 表示请求被服务端拒绝（常见于 geely2 车型）
            if "B00000" in err_msg:
                _LOGGER.warning(
                    "车辆状态查询被服务端拒绝 (B00000)，可能是 geely2 车型限制。"
                    "开关状态和远程控制不受影响。"
                )
                return {}

            # 其他错误：尝试刷新 token 后重试一次
            _LOGGER.info("车辆状态首次请求失败，刷新 token 后重试...")
            await self.refresh_access_token()
            await asyncio.sleep(2)
            try:
                return await self._call_vehicle_status(vin)
            except GeelyApiError as retry_err:
                if "B00000" in str(retry_err):
                    _LOGGER.warning("车辆状态查询被服务端拒绝 (B00000)")
                    return {}
                raise
            except aiohttp.ClientError as err:
                raise GeelyApiError(f"Connection error: {err}") from err

    async def get_switch_status(self, vin: str | None = None) -> dict[str, Any]:
        """Get vehicle switch status (sentry mode, etc)."""
        vin = await self._ensure_vin(vin)
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

    async def _vc_post(self, path: str, body_dict: dict) -> dict[str, Any]:
        """通用 USP 网关 POST 请求。"""
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
                    raise GeelyApiError(
                        f"API error (code={code}): {data.get('msg', data.get('message', response_text[:200]))}"
                    )

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
        if not self._token:
            await self.refresh_access_token()

        session = await self._ensure_session()
        path = f"/api/v1/oauth2/code?scope=snsapiUserinfo&response_type=code&isDestruction=false&state=1&client_id={RECHARGE_CONFIG['oauth_client_id']}"
        url = f"https://{API_HOSTS['user']}{path}"
        headers = self._build_get_headers(APP_KEYS["user"], path)

        _LOGGER.debug("Calling get_oauth_code: %s", url)
        try:
            async with session.get(url, headers=headers) as response:
                response_text = await response.text()
                _LOGGER.debug("OAuth code response: %s", response_text)

                if response.status != 200:
                    raise GeelyApiError(f"OAuth code request failed: HTTP {response.status}")

                data = json.loads(response_text)
                if data.get("code") != "success":
                    raise GeelyApiError(f"OAuth code failed: {data.get('msg', response_text[:200])}")

                oauth_code = data.get("data", {}).get("code")
                if not oauth_code:
                    raise GeelyApiError("No OAuth code in response")

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
            _LOGGER.info("充电服务 authToken 为空，开始获取...")
            await self.get_recharge_auth_token()
            _LOGGER.info("充电服务 authToken 获取成功")

    async def _recharge_post(self, path: str, body_dict: dict) -> dict[str, Any]:
        """充电服务 POST 请求。"""
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
                    raise GeelyApiError(
                        f"API error (code={code}): {data.get('msg', data.get('message', response_text[:200]))}"
                    )

                return data.get("data") or {}

        except aiohttp.ClientError as err:
            raise GeelyApiError(f"Connection error: {err}") from err

    async def get_home_charger_list(self) -> list[dict[str, Any]]:
        """获取家用充电桩列表。"""
        try:
            _LOGGER.info("开始获取家用充电桩列表...")
            result = await self._recharge_post("/app/hcharger/getMyPilingsNew", {})
            _LOGGER.info("充电桩列表 API 返回: %s (类型: %s)", result, type(result).__name__)
            if isinstance(result, list) and result:
                self._piling_code = result[0].get("pilingsCode")
                _LOGGER.info("检测到充电桩，编码: %s", self._piling_code)
                return result
            _LOGGER.info("未检测到充电桩（列表为空）")
            return []
        except GeelyApiError as err:
            _LOGGER.warning("获取充电桩列表失败: %s", err)
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
