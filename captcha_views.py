"""GeeTest 验证码相关 HTTP 视图。

包含：
- 验证码页面视图（内嵌 HTML，无需部署额外文件）
- 验证码结果回调视图
- GeeTest API 代理视图（解决 CORS，参考 captcha_proxy.py）
"""
from __future__ import annotations

import gzip
import logging
import ssl

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

GEETEST_HOST = "captcha4.geely.com"
CAPTCHA_ID = "2baef8ee692c27f1c8a0632e560242d7"


# ==================== 验证码页面视图 ====================

class GeelyCaptchaPageView(HomeAssistantView):
    """提供 GeeTest 验证码页面。"""

    url = "/api/geely_galaxy/captcha"
    name = "api:geely_galaxy:captcha"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """返回验证码 HTML 页面。"""
        flow_id = request.query.get("flow_id", "")
        html = CAPTCHA_HTML.replace("__FLOW_ID__", flow_id)
        html = html.replace("__CAPTCHA_ID__", CAPTCHA_ID)
        return web.Response(text=html, content_type="text/html")


# ==================== 验证码结果回调视图 ====================

class GeelyCaptchaCallbackView(HomeAssistantView):
    """接收 GeeTest 验证码页面的回调结果。"""

    url = "/api/geely_galaxy/captcha_callback"
    name = "api:geely_galaxy:captcha_callback"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """处理验证码结果并推进配置流程。"""
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


# ==================== GeeTest API 代理视图 ====================

class GeeTestProxyView(HomeAssistantView):
    """代理 GeeTest 请求到 captcha4.geely.com。

    GeeTest SDK 在浏览器中运行，受 CORS 限制无法直接访问 captcha4.geely.com。
    此视图将请求透明转发，使 SDK 认为是同源请求。
    """

    url = "/api/geely_galaxy/gt/{path:.*}"
    name = "api:geely_galaxy:gt"
    requires_auth = False

    async def get(self, request: web.Request, path: str) -> web.Response:
        """代理 GET 请求。"""
        return await self._proxy(request, path, "GET")

    async def post(self, request: web.Request, path: str) -> web.Response:
        """代理 POST 请求。"""
        return await self._proxy(request, path, "POST")

    async def options(self, request: web.Request, path: str) -> web.Response:
        """处理 CORS 预检请求。"""
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            },
        )

    async def _proxy(
        self, request: web.Request, path: str, method: str
    ) -> web.Response:
        """将请求转发到 captcha4.geely.com。"""
        # SDK 可能拼出 /undefinedXXX 路径，去掉 undefined 前缀
        if path.startswith("undefined"):
            path = path[len("undefined"):]

        query_string = request.query_string
        target_url = f"https://{GEETEST_HOST}/{path}"
        if query_string:
            target_url += f"?{query_string}"

        proxy_base = f"{request.scheme}://{request.host}/api/geely_galaxy/gt"

        headers = {
            "Host": GEETEST_HOST,
            "Accept-Encoding": "gzip",
        }
        body = await request.read() if method == "POST" else None

        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.request(
                    method,
                    target_url,
                    data=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    resp_body = await resp.read()
                    content_type = resp.headers.get(
                        "Content-Type", "application/octet-stream"
                    )
                    content_encoding = resp.headers.get("Content-Encoding", "")

                    # 解压 gzip
                    if content_encoding == "gzip":
                        resp_body = gzip.decompress(resp_body)

                    # 改写 load JSONP 响应中的域名引用
                    if "callback=" in query_string and "load" in path:
                        resp_body = _rewrite_geetest_body(
                            resp_body, proxy_base, f"/{path}?{query_string}"
                        )

                    # 修正静态资源 Content-Type
                    clean_path = path.split("?")[0]
                    if clean_path.endswith(".js"):
                        content_type = "application/javascript; charset=utf-8"
                    elif clean_path.endswith(".css"):
                        content_type = "text/css; charset=utf-8"

                    return web.Response(
                        body=resp_body,
                        status=resp.status,
                        content_type=content_type,
                        headers={
                            "Access-Control-Allow-Origin": "*",
                            "Cache-Control": "no-cache",
                        },
                    )
        except Exception as err:
            _LOGGER.error("GeeTest 代理错误: %s", err)
            return web.Response(
                text=f"Proxy error: {err}",
                status=502,
                content_type="text/plain",
            )


# ==================== 辅助函数 ====================

def _rewrite_geetest_body(
    body: bytes, proxy_base: str, request_path: str
) -> bytes:
    """改写 GeeTest JSONP 响应中的域名引用。

    将 captcha4.geely.com 替换为本地代理路径，
    使所有子资源也通过代理加载。
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body

    # proxy_base 格式: "http://host:port/api/geely_galaxy/gt"
    proxy_host = proxy_base.split("//", 1)[1]  # "host:port/api/geely_galaxy/gt"

    text = text.replace(f"https://{GEETEST_HOST}", proxy_base)
    text = text.replace(f"http://{GEETEST_HOST}", proxy_base)
    text = text.replace(f"//{GEETEST_HOST}", f"//{proxy_host}")
    text = text.replace(GEETEST_HOST, proxy_host)

    # 注入 static_path，SDK 用它拼接静态资源 URL
    if "/load" in request_path and '"static_servers"' in text:
        text = text.replace(
            '"static_servers"', '"static_path": "/", "static_servers"'
        )

    return text.encode("utf-8")


def ensure_captcha_views(hass: HomeAssistant) -> None:
    """注册验证码相关 HTTP 视图（仅首次调用时注册）。"""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("captcha_view_registered"):
        domain_data["captcha_results"] = {}
        domain_data["captcha_view_registered"] = True
        hass.http.register_view(GeelyCaptchaPageView())
        hass.http.register_view(GeelyCaptchaCallbackView())
        hass.http.register_view(GeeTestProxyView())


# ==================== 内嵌 HTML 页面 ====================

CAPTCHA_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>吉利银河 - 安全验证</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }
        .container {
            background: #fff;
            border-radius: 12px;
            padding: 32px;
            max-width: 420px;
            width: 90%;
            box-shadow: 0 2px 12px rgba(0,0,0,0.1);
        }
        h2 { text-align: center; color: #333; margin-bottom: 8px; font-size: 20px; }
        .subtitle { text-align: center; color: #999; font-size: 13px; margin-bottom: 24px; }
        #captcha { display: flex; justify-content: center; margin: 20px 0; }
        .status {
            text-align: center; padding: 12px; border-radius: 8px;
            margin-top: 16px; font-size: 14px;
        }
        .status.success { background: #e6f7e6; color: #2e7d32; }
        .status.error { background: #fde8e8; color: #c62828; }
        .status.info { background: #e3f2fd; color: #1565c0; }
        .result-box { display: none; margin-top: 20px; }
        .result-box.show { display: block; }
        .btn {
            display: block; width: 100%; padding: 12px; border: none;
            border-radius: 8px; font-size: 16px; cursor: pointer; margin-top: 12px;
            background: #1a73e8; color: #fff;
        }
        .btn:hover { background: #1557b0; }
        #debug-log {
            margin-top: 16px; padding: 8px; background: #fafafa;
            border: 1px solid #eee; border-radius: 4px; font-family: monospace;
            font-size: 11px; color: #666; max-height: 150px; overflow-y: auto;
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h2>吉利银河 安全验证</h2>
        <p class="subtitle">请完成滑块验证，用于 Home Assistant 登录</p>

        <div id="captcha"></div>

        <div id="status-loading" class="status info">正在加载验证码...</div>

        <div id="auto-result-box" class="result-box">
            <div id="auto-status" class="status info">正在提交验证结果...</div>
        </div>

        <div id="error-box" class="result-box">
            <div class="status error" id="error-msg">验证失败</div>
            <button class="btn" onclick="location.reload()">重试</button>
        </div>

        <div id="debug-log"></div>
    </div>

    <script src="/api/geely_galaxy/gt/www/gt4.js"></script>
    <script>
        var CAPTCHA_ID = "__CAPTCHA_ID__";
        var flowId = "__FLOW_ID__";
        var debugEl = document.getElementById("debug-log");

        function debugLog(msg) {
            console.log("[GeeTest]", msg);
            debugEl.style.display = "block";
            var line = document.createElement("div");
            line.textContent = new Date().toLocaleTimeString() + " " + msg;
            debugEl.appendChild(line);
            debugEl.scrollTop = debugEl.scrollHeight;
        }

        function showError(msg) {
            document.getElementById("status-loading").style.display = "none";
            document.getElementById("error-box").className = "result-box show";
            document.getElementById("error-msg").textContent = msg;
        }

        function onCaptchaSuccess(data) {
            var autoBox = document.getElementById("auto-result-box");
            var autoStatus = document.getElementById("auto-status");
            document.getElementById("status-loading").style.display = "none";
            autoBox.className = "result-box show";
            autoStatus.textContent = "正在提交验证结果...";

            fetch("/api/geely_galaxy/captcha_callback", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ flow_id: flowId, captcha_data: data })
            })
            .then(function(resp) {
                if (resp.ok) {
                    autoStatus.className = "status success";
                    autoStatus.textContent = "验证成功！请返回 Home Assistant 继续操作。";
                    debugLog("验证结果已提交");
                    setTimeout(function() { window.close(); }, 2000);
                } else {
                    return resp.text().then(function(t) { throw new Error(t); });
                }
            })
            .catch(function(err) {
                autoStatus.className = "status error";
                autoStatus.textContent = "提交失败: " + err.message;
                debugLog("提交失败: " + err.message);
            });
        }

        // GeeTest SDK 通过内置代理加载，所有请求经 /api/geely_galaxy/gt/ 转发到 captcha4.geely.com
        var proxyServer = window.location.host + "/api/geely_galaxy/gt";
        debugLog("SDK loaded: " + (typeof initGeetest));
        debugLog("代理路径: " + proxyServer);
        debugLog("flow_id: " + flowId);

        if (typeof initGeetest === "function") {
            var opts = {
                captchaId: CAPTCHA_ID,
                language: "zho",
                protocol: window.location.protocol + "//",
                apiServers: [proxyServer],
                onError: function(e) {
                    debugLog("onError: " + JSON.stringify(e));
                    showError("验证码加载失败: " + (e.msg || e.desc || JSON.stringify(e)));
                }
            };
            debugLog("初始化参数: " + JSON.stringify({captchaId: opts.captchaId, apiServers: opts.apiServers}));

            try {
                initGeetest(opts, function(captchaObj) {
                    debugLog("initGeetest 回调已触发");
                    document.getElementById("status-loading").style.display = "none";
                    captchaObj.appendTo("#captcha");
                    captchaObj.onReady(function() { debugLog("验证码就绪"); });
                    captchaObj.onSuccess(function() {
                        var result = captchaObj.getValidate();
                        debugLog("验证成功: lot_number=" + result.lot_number);
                        onCaptchaSuccess(result);
                    });
                    captchaObj.onError(function(e) { debugLog("onError: " + JSON.stringify(e)); });
                    captchaObj.onFail(function(e) { debugLog("onFail: " + JSON.stringify(e)); });
                    captchaObj.onClose(function() { debugLog("验证码弹窗已关闭"); });
                });
            } catch(e) {
                debugLog("异常: " + e.message);
                showError("验证码初始化异常: " + e.message);
            }
        } else {
            showError("GeeTest SDK 加载失败");
            debugLog("initGeetest 未定义，gt4.js 可能加载失败。请检查 /api/geely_galaxy/gt/www/gt4.js 是否可访问。");
        }
    </script>
</body>
</html>"""
