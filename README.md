# Nodriver Container

This is a dumb wrapper around [**ultrafunkamsterdam/nodriver**](https://github.com/ultrafunkamsterdam/nodriver) that does almost nothing besides accepting settings and returning the result.

It's handy when you just want a simple HTTP interface to drive a headless browser.

Originally designed for serverless usage, but works just as well as a regular long-running container.

## 🧱 Usage

Use any container runtime (e.g. Docker, Podman). The service listens on port `8080`.

```bash
docker run --rm -p 8080:8080 stopmakingthatbigface/serverless-nodriver
```

### 🛠️ Build locally (optional)

```bash
docker build -t serverless-nodriver .
```

### 🧪 Run locally from source (no container)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export BROWSER_EXECUTABLE="/path/to/your/chromium-or-chrome"
export LOG_LEVEL=DEBUG
python main.py
```

### Send a request

```bash
curl -i -X POST http://localhost:8080/ \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/","timeout":10,"sleep":500}'
```

### With proxy

```bash
curl -i -X POST http://localhost:8080/ \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/","timeout":10,"sleep":2000,"proxy":"http://user:pass@host:8080"}'
```

### Debug logs

Enable network logs for redirects and responses.

```bash
docker run --rm -p 8080:8080 -e LOG_LEVEL=DEBUG serverless-nodriver:latest
```

## Response

The response body is streamed as-is. Metadata is exposed via headers:

- `X-Timestamp` — request timestamp (UTC).
- `X-Elapsed-Ms` — elapsed time in milliseconds.
- `X-Final-URL` — final URL after redirects.
- `X-Original-Header-{name}` — original response headers.
- `X-Original-Set-Cookie` — cookies from the browser session.

HTTP status code is proxied from the final main-document response.

## Request parameters

- `url` — required.
- `timeout` — seconds to wait for load completion.
- `sleep` — extra milliseconds to wait after load completion.
- `proxy` — proxy URL, `http://` or `socks5://`.

## Environment variables

- `BROWSER_EXECUTABLE` — absolute path to a Chromium/Chrome binary.
- `LOG_LEVEL` — log verbosity (e.g. `INFO`, `DEBUG`).
- `HOST` — bind address (default `0.0.0.0`).
- `PORT` — service port (default `8080`).
