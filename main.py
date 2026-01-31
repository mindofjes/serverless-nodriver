import asyncio
import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import nodriver as nd
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

DEFAULT_TIMEOUT_SECONDS = 10
CHROMIUM_PATH = os.environ.get("BROWSER_EXECUTABLE", "/usr/bin/chromium")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info")
logging.basicConfig(level=LOG_LEVEL.upper(), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("serverless-nodriver")
logging.getLogger("nodriver").setLevel(logging.INFO)

app = FastAPI()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_response(status_code: int, payload: dict):
    return JSONResponse(status_code=status_code, content=payload)


def sanitize_header_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", name)


def get_cookie_attr(cookie, key: str, default=None):
    if isinstance(cookie, dict):
        return cookie.get(key, default)
    return getattr(cookie, key, default)


def format_cookie(cookie) -> str:
    parts = [f"{get_cookie_attr(cookie, 'name')}={get_cookie_attr(cookie, 'value')}"]
    domain = get_cookie_attr(cookie, "domain")
    path = get_cookie_attr(cookie, "path")
    expires = get_cookie_attr(cookie, "expires")

    if domain:
        parts.append(f"Domain={domain}")
    if path:
        parts.append(f"Path={path}")
    if expires and expires > 0:
        dt = datetime.fromtimestamp(expires, timezone.utc)
        parts.append(f"Expires={dt.strftime('%a, %d %b %Y %H:%M:%S GMT')}")
    if get_cookie_attr(cookie, "secure"):
        parts.append("Secure")
    if get_cookie_attr(cookie, "http_only"):
        parts.append("HttpOnly")
    same_site = get_cookie_attr(cookie, "same_site")
    if same_site:
        parts.append(f"SameSite={same_site}")

    return "; ".join(parts)


def is_debug_enabled():
    return logger.isEnabledFor(logging.DEBUG)


def preview_content(content: bytes, limit: int = 128) -> str:
    text = content.decode("utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit]
    return text


async def run_browser(url: str, timeout_seconds: int, proxy: str | None, sleep_ms: int):
    start_ts = time.monotonic()

    browser_args = []
    if proxy:
        browser_args.append(f"--proxy-server={proxy}")

    browser = await nd.start(
        headless=True,
        browser_executable_path=CHROMIUM_PATH,
        browser_args=browser_args,
    )

    last_response = {
        "status": 200,
        "url": url,
        "headers": {},
        "request_id": None,
    }
    main_frame_id = {"id": None}

    try:
        tab = await browser.get(url)
        await tab.send(nd.cdp.network.enable())

        def on_frame_navigated(event, *_):
            frame = event.frame
            if getattr(frame, "parent_id", None) is None:
                main_frame_id["id"] = getattr(frame, "id_", None) or getattr(frame, "id", None)
                if is_debug_enabled():
                    logger.debug("frame navigated %s", frame.url)

        def on_request(event, *_):
            if event.type_ == "Document" and event.frame_id == main_frame_id["id"]:
                last_response["request_id"] = event.request_id
                last_response["url"] = event.request.url
                if is_debug_enabled():
                    logger.debug("request %s %s", event.request.method, event.request.url)

        def on_response(event, *_):
            response = event.response
            if response:
                if is_debug_enabled():
                    logger.debug("response %s %s", response.status, response.url)
                if event.request_id == last_response["request_id"]:
                    last_response["status"] = response.status
                    last_response["headers"] = response.headers or {}
                    last_response["url"] = response.url

        def on_redirect(event, *_):
            if is_debug_enabled():
                logger.debug("redirect %s -> %s", event.request.url, event.redirect_response.url)

        tab.add_handler(nd.cdp.page.FrameNavigated, on_frame_navigated)
        tab.add_handler(nd.cdp.network.RequestWillBeSent, on_request)
        tab.add_handler(nd.cdp.network.ResponseReceived, on_response)
        tab.add_handler(nd.cdp.network.RequestWillBeSent, on_redirect)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                ready_state = await tab.evaluate("document.readyState")
            except Exception:
                ready_state = None

            if ready_state == "complete":
                break

            await tab.sleep(0.2)

        await tab.sleep(0.5)
        if sleep_ms > 0:
            await tab.sleep(sleep_ms / 1000)

        if last_response["request_id"]:
            body_result = await tab.send(
                nd.cdp.network.get_response_body(last_response["request_id"])
            )
            body = body_result.get("body", "")
            if body_result.get("base64_encoded"):
                content = base64.b64decode(body)
                encoding = "base64"
            else:
                content = body.encode("utf-8")
                encoding = "utf8"
        else:
            html = await tab.get_content()
            content = html.encode("utf-8")
            encoding = "utf8"

        if is_debug_enabled():
            logger.debug("body preview %s", preview_content(content))

        final_url = await tab.evaluate("location.href")
        cookies = await tab.send(nd.cdp.network.get_cookies())
    finally:
        browser.stop()
        await asyncio.sleep(0.5)

    elapsed_ms = int((time.monotonic() - start_ts) * 1000)

    return {
        "status": last_response["status"],
        "body": content,
        "encoding": encoding,
        "cookies": cookies,
        "headers": last_response["headers"],
        "url": final_url,
        "time": elapsed_ms,
        "timestamp": now_iso(),
    }


@app.post("/")
async def root(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    url = payload.get("url")
    if not url:
        return json_response(
            400,
            {"statusCode": 400, "error": "Missing url", "timestamp": now_iso()},
        )

    timeout_seconds = int(payload.get("timeout") or DEFAULT_TIMEOUT_SECONDS)
    sleep_ms = int(payload.get("sleep") or 0)
    proxy = payload.get("proxy")

    try:
        result = await run_browser(url, timeout_seconds, proxy, sleep_ms)
    except Exception as exc:
        return json_response(
            500,
            {
                "statusCode": 500,
                "error": str(exc),
                "timestamp": now_iso(),
            },
        )

    response = StreamingResponse(
        iter([result["body"]]),
        status_code=int(result["status"] or 200),
    )

    response.headers["X-Timestamp"] = result["timestamp"]
    response.headers["X-Elapsed-Ms"] = str(result["time"])
    response.headers["X-Final-URL"] = result["url"]

    for name, value in (result["headers"] or {}).items():
        header_name = sanitize_header_name(str(name))
        response.headers[f"X-Original-Header-{header_name}"] = str(value)

    for cookie in result["cookies"] or []:
        response.raw_headers.append(
            (b"x-original-set-cookie", format_cookie(cookie).encode("latin-1", "ignore"))
        )

    content_type = result["headers"].get("content-type") if result["headers"] else None
    if content_type:
        response.headers["Content-Type"] = str(content_type)

    return response


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": now_iso()}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
