import asyncio
import microcontroller
import socketpool
import time
import wifi
from adafruit_httpserver import Server, Request, Response, GET, POST

import config

AP_SSID = "CinnaScale-Setup"
# Reset (and try the normal startup again) if nobody saves anything in this long, so a stray boot into setup mode
# doesn't leave the scale offline.
PORTAL_TIMEOUT = 15 * 60  # seconds


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_PAGE = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>CinnaScale Setup</title>
<style>body{font-family:sans-serif;max-width:28em;margin:1em auto;padding:0 1em}
label{display:block;margin-top:1em}input{width:100%;padding:.4em;box-sizing:border-box}
button{margin-top:1.5em;padding:.6em 1.2em}</style></head>
<body><h1>CinnaScale Setup</h1><p>$MESSAGE</p>
<form method="POST" action="/save">
<label>WiFi SSID<input name="ssid" value="$SSID"></label>
<label>WiFi password ($PASSWORD_STATE, leave blank to keep)<input name="password" type="password"></label>
<label>Home Assistant URL<input name="homeassistant_url" value="$URL" placeholder="http://homeassistant.local:8123"></label>
<label>Long-lived access token ($TOKEN_STATE, leave blank to keep)<input name="token" type="password"></label>
<button type="submit">Save and restart</button>
</form></body></html>"""


def _page(message: str = "") -> str:
    return (
        _PAGE.replace("$MESSAGE", message)
        .replace("$SSID", _escape(config.get("ssid")))
        .replace("$URL", _escape(config.get("homeassistant_url")))
        .replace("$PASSWORD_STATE", "set" if config.get("password") else "not set")
        .replace("$TOKEN_STATE", "set" if config.get("token") else "not set")
    )


async def run_portal():
    """
    Start an open access point with a small settings page.  Saving the form writes the settings to NVM and resets the
    board.  Never returns.
    """
    print("Starting setup portal on AP {}...".format(AP_SSID))
    wifi.radio.stop_station()
    wifi.radio.start_ap(AP_SSID)

    while wifi.radio.ipv4_address_ap is None:
        await asyncio.sleep(0.1)

    pool = socketpool.SocketPool(wifi.radio)
    server = Server(pool, debug=True)
    saved = False

    @server.route("/", GET)
    def index(request: Request):
        return Response(request, _page(), content_type="text/html")

    @server.route("/save", POST)
    def save(request: Request):
        nonlocal saved
        values = {key: request.form_data.get(key, "").strip() for key in config.KEYS}
        try:
            config.save(values)
        except Exception as e:
            return Response(request, _page("Save failed: {}".format(_escape(repr(e)))), content_type="text/html")
        saved = True
        return Response(request, _page("Saved. Restarting..."), content_type="text/html")

    host = str(wifi.radio.ipv4_address_ap)
    server.start(host, 80)
    print("Setup portal running at http://{}/".format(host))

    deadline = time.monotonic() + PORTAL_TIMEOUT
    while not saved and time.monotonic() < deadline:
        server.poll()
        await asyncio.sleep(0.05)

    # Give the response a moment to flush before resetting.
    await asyncio.sleep(1)
    print("Leaving setup portal, resetting...")
    microcontroller.reset()
