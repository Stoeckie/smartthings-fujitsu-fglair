"""Stoeckie Bridge: brings cloud appliances onto the LAN for a SmartThings Edge driver.

Sources
  * AEG / Electrolux appliances (dryer, washer-dryer) via the official Electrolux Group Developer API
    (developer.electrolux.one): state at start, live updates over SSE, poll as fallback.
  * Fujitsu air conditioners with an FGLair wifi adapter, via the Ayla cloud that the FGLair app uses
    (library ayla-iot-unofficial, the same one Home Assistant uses). Polled every 60 s.

Credentials live only in config.json next to this script (never in the code).

LAN API (default port 8787, no auth, LAN only)
  GET  /stbridge/info                    -> {"service": "stoeckie-bridge", "id": ..., "version": ...}
  GET  /stbridge/devices                 -> {"devices": [ {id, type, name, online, state{...}} ]}
  POST /stbridge/devices/<id>/command    body {"cmd": "...", "value": ...}
      dryer:   run | pause | resume | stop | refresh
      aircon:  on | off | mode (auto cool dry fan heat) | setpoint (C) | fan (quiet low medium high auto)
               | swing (off vertical horizontal both) | refresh

Usage
  python stoeckie_bridge.py           run the bridge
  python stoeckie_bridge.py --check   test the AEG and FGLair logins and list appliances
  python stoeckie_bridge.py --demo    fake devices, to test the Edge driver
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import html
import ipaddress
import json
import os
import logging
import logging.handlers
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web

VERSION = "2026-10-30c"
HERE = Path(__file__).resolve().parent
# Data folder: next to the script on Windows, /data in the Docker image (STBRIDGE_DATA)
DATA = Path(os.environ.get("STBRIDGE_DATA") or HERE)
DATA.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA / "config.json"
LOG_FILE = DATA / "bridge.log"
# Extra networks allowed to open the setup page, e.g. "192.168.2.0/24" (Docker without host networking)
SETUP_ALLOW = [ipaddress.ip_network(n.strip(), strict=False)
               for n in os.environ.get("STBRIDGE_SETUP_ALLOW", "").split(",") if n.strip()]

log = logging.getLogger("bridge")

# ---------------------------------------------------------------- registry

class Registry:
    """All devices the bridge exposes, keyed by a stable id."""

    def __init__(self) -> None:
        self.devices: dict[str, dict[str, Any]] = {}
        self.handlers: dict[str, Any] = {}  # id -> async fn(cmd, value)

    def upsert(self, dev_id: str, dev_type: str, name: str, state: dict[str, Any],
               online: bool = True, handler=None) -> None:
        d = self.devices.setdefault(dev_id, {"id": dev_id, "type": dev_type, "name": name})
        d["type"], d["name"], d["online"] = dev_type, name, online
        d.setdefault("state", {}).update(state)
        d["updated"] = int(time.time())
        if handler:
            self.handlers[dev_id] = handler

    def set_online(self, prefix: str, online: bool) -> None:
        for d in self.devices.values():
            if d["id"].startswith(prefix):
                d["online"] = online


REG = Registry()


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def save_config(cfg: dict[str, Any]) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


# ---------------------------------------------------------------- Electrolux / AEG

DRYER_TYPES = {"TD", "WD"}  # tumble dryer, washer-dryer


def aeg_configured(e: dict[str, Any]) -> bool:
    keys = ("api_key", "access_token", "refresh_token")
    return all(e.get(k) and not str(e.get(k)).startswith("PLAK_HIER") for k in keys)


def _iso_in(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(seconds))
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def dryer_state(reported: dict[str, Any], connection: str | None) -> dict[str, Any]:
    """Flatten an Electrolux TD/WD reported state into what the Edge driver needs."""
    app_state = str(reported.get("applianceState") or "OFF").upper()
    phase = str(reported.get("cyclePhase") or "").upper()
    sel = reported.get("userSelections") or {}
    tte = reported.get("timeToEnd")
    try:
        tte = float(tte) if tte is not None else None
    except (TypeError, ValueError):
        tte = None
    running = app_state in ("RUNNING", "DELAYED_START")
    return {
        "applianceState": app_state,       # OFF IDLE RUNNING PAUSED READY_TO_START DELAYED_START END_OF_CYCLE
        "cyclePhase": phase,
        "program": sel.get("programUID"),
        "timeToEnd": tte,                  # seconds
        "completionTime": _iso_in(tte) if running else None,
        "doorState": reported.get("doorState"),
        "remoteControl": reported.get("remoteControl"),
        "alerts": reported.get("alerts") or [],
        "connection": connection,
    }


class Electrolux:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.client = None
        self.appliances: dict[str, dict[str, Any]] = {}  # id -> {type, name, reported, connection}

    def _on_tokens(self, access: str, refresh: str, api_key: str) -> None:
        e = self.cfg.setdefault("electrolux", {})
        if e.get("refresh_token") != refresh or e.get("access_token") != access:
            e["access_token"], e["refresh_token"], e["api_key"] = access, refresh, api_key
            save_config(self.cfg)
            log.info("[aeg] tokens refreshed and saved")

    def _publish(self, app_id: str) -> None:
        a = self.appliances[app_id]
        online = str(a.get("connection") or "").lower() != "disconnected"
        REG.upsert("aeg-" + app_id, "dryer", a["name"],
                   dryer_state(a["reported"], a.get("connection")), online, self._command(app_id))

    def _command(self, app_id: str):
        async def handler(cmd: str, value: Any) -> None:
            if cmd == "refresh":
                await self._load_state(app_id)
                return
            mapping = {"run": "START", "pause": "PAUSE", "resume": "RESUME", "stop": "STOPRESET"}
            if cmd not in mapping:
                raise ValueError(f"unsupported command {cmd}")
            await self.client.send_command(app_id, {"executeCommand": mapping[cmd]})
        return handler

    async def _load_state(self, app_id: str) -> None:
        st = await self.client.get_appliance_state(app_id)
        a = self.appliances[app_id]
        a["reported"] = (st.properties or {}).get("reported", {}) or {}
        a["connection"] = st.connectionState
        self._publish(app_id)

    def _on_event(self, event: dict[str, Any]) -> None:
        app_id = event.get("applianceId")
        if app_id not in self.appliances:
            return
        prop, value = event.get("property"), event.get("value")
        if prop is None:
            return
        a = self.appliances[app_id]
        if prop in ("connectionState", "connectivityState"):
            a["connection"] = value
        if prop != "connectionState":
            target = a["reported"]
            path = prop.split("/")
            for key in path[:-1]:
                target = target.setdefault(key, {})
            target[path[-1]] = value
        log.info("[aeg] %s %s = %s", a["name"], prop, value)
        self._publish(app_id)

    async def run(self) -> None:
        e = self.cfg.get("electrolux") or {}
        if not aeg_configured(e):
            log.info("[aeg] not configured: open http://localhost:%s/setup on this pc", self.cfg.get("port", 8787))
            return
        from electrolux_group_developer_sdk.auth.token_manager import TokenManager
        from electrolux_group_developer_sdk.client.appliance_client import ApplianceClient

        tm = TokenManager(e["access_token"], e["refresh_token"], e["api_key"], on_token_update=self._on_tokens)
        self.client = ApplianceClient(tm, external_user_agent=f"StoeckieBridge/{VERSION}")
        while True:
            try:
                apps = await self.client.get_appliances()
                break
            except Exception as ex:  # noqa: BLE001
                log.error("[aeg] cannot list appliances: %s (retry in 60 s)", ex)
                await asyncio.sleep(60)
        for app in apps:
            t = str(app.applianceType).upper()
            log.info("[aeg] appliance %s '%s' type %s", app.applianceId, app.applianceName, t)
            if t not in DRYER_TYPES:
                continue
            self.appliances[app.applianceId] = {"type": t, "name": app.applianceName or "Droger",
                                                "reported": {}, "connection": None}
            self.client.add_listener(app.applianceId, self._on_event)
            try:
                await self._load_state(app.applianceId)
            except Exception as ex:  # noqa: BLE001
                log.error("[aeg] state of %s failed: %s", app.applianceId, ex)
        if not self.appliances:
            log.warning("[aeg] no dryer found on this account")
            return

        async def resync() -> None:
            for app_id in self.appliances:
                try:
                    await self._load_state(app_id)
                except Exception as ex:  # noqa: BLE001
                    log.warning("[aeg] resync %s failed: %s", app_id, ex)

        async def poll() -> None:  # fallback when the stream is quiet or broken
            while True:
                await asyncio.sleep(int(e.get("poll_seconds", 300)))
                await resync()

        asyncio.create_task(poll())
        await self.client.start_event_stream(do_on_livestream_opening_list=[resync])


# ---------------------------------------------------------------- Fujitsu FGLair (Ayla cloud)

def fglair_configured(f: dict[str, Any]) -> bool:
    return bool(f.get("username") and f.get("password") and not str(f.get("password")).startswith("PLAK_HIER"))


def _name(e) -> str:
    return getattr(e, "name", str(e)).lower()


SWING_NAMES = {"OFF": "off", "SWING_VERTICAL": "vertical", "SWING_HORIZONTAL": "horizontal", "SWING_BOTH": "both"}


def aircon_state(dev) -> dict[str, Any]:
    """Flatten a FujitsuHVAC object into what the Edge driver needs."""
    def safe(fn, default=None):
        try:
            return fn()
        except Exception:  # noqa: BLE001  (missing property on this model)
            return default
    mode = safe(lambda: _name(dev.op_mode), "off")
    lo, hi = safe(lambda: dev.temperature_range, (16.0, 30.0)) if mode not in ("off", "on") else (16.0, 30.0)
    return {
        "power": "off" if mode == "off" else "on",
        "mode": mode,                                   # off on auto cool dry fan heat
        "setpoint": safe(lambda: dev.set_temp),
        "temperature": safe(lambda: dev.sensed_temp),   # None on models without a room sensor
        "outdoorTemperature": safe(lambda: dev.outdoor_temperature),
        "fan": safe(lambda: _name(dev.fan_speed)),
        "swing": safe(lambda: SWING_NAMES.get(dev.swing_mode.name, "off"), "off"),
        "supportedModes": [m for m in (_name(x) for x in safe(lambda: dev.supported_op_modes, []))
                           if m not in ("off", "on")],
        "supportedFans": [_name(x) for x in safe(lambda: dev.supported_fan_speeds, [])],
        "supportedSwings": [SWING_NAMES.get(x.name, "off") for x in safe(lambda: dev.supported_swing_modes, [])],
        "setpointRange": [lo, hi],
        "model": getattr(dev, "oem_model_number", None),
    }


class FGLair:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.f = cfg.get("fglair") or {}
        self.api = None
        self.devices: dict[str, Any] = {}  # dsn -> FujitsuHVAC
        self.skipped: set[str] = set()
        self.lock = asyncio.Lock()

    def _new_api(self, session):
        from ayla_iot_unofficial import new_ayla_api
        from ayla_iot_unofficial.fujitsu_consts import FGLAIR_APP_CREDENTIALS
        region = str(self.f.get("region") or "eu").lower()
        app_id, app_secret = FGLAIR_APP_CREDENTIALS.get(region, FGLAIR_APP_CREDENTIALS["default"])
        return new_ayla_api(self.f["username"], self.f["password"], app_id, app_secret,
                            websession=session, europe=(region == "eu"))

    async def _auth(self) -> None:
        from ayla_iot_unofficial.exc import AylaAuthExpiringError
        try:
            self.api.check_auth()
            return
        except AylaAuthExpiringError:
            try:
                await self.api.async_refresh_auth()
                return
            except Exception as ex:  # noqa: BLE001
                log.info("[fglair] token refresh failed (%s), signing in again", ex)
        except Exception:  # noqa: BLE001  (not signed in or expired)
            pass
        await self.api.async_sign_in()

    def _publish(self, dsn: str) -> None:
        dev = self.devices[dsn]
        try:
            name = dev.device_name or dev.name
        except Exception:  # noqa: BLE001
            name = getattr(dev, "name", None) or "Airco"
        REG.upsert("fglair-" + dsn, "aircon", name, aircon_state(dev), dev.is_online(), self._command(dsn))

    async def _refresh(self, dsn: str) -> None:
        dev = self.devices[dsn]
        if dev.is_online():
            await dev.async_update()
        self._publish(dsn)

    async def _sync(self) -> None:
        """List devices (online state), then read every unit."""
        from ayla_iot_unofficial.fujitsu_hvac import FujitsuHVAC
        async with self.lock:
            await self._auth()
            listed = await self.api.async_get_devices()
            for d in listed:
                dsn = d.serial_number
                if not isinstance(d, FujitsuHVAC):
                    if dsn not in self.skipped:
                        self.skipped.add(dsn)
                        log.warning("[fglair] device %s '%s' model %s not supported by ayla-iot-unofficial",
                                    dsn, d.name, getattr(d, "oem_model_number", "?"))
                    continue
                if dsn in self.devices:
                    self.devices[dsn]._connection_status = d._connection_status
                else:
                    log.info("[fglair] unit %s '%s' model %s", dsn, d.name, d.oem_model_number)
                    self.devices[dsn] = d
                try:
                    await self._refresh(dsn)
                except Exception as ex:  # noqa: BLE001
                    log.warning("[fglair] update %s failed: %s", dsn, ex)
                    REG.set_online("fglair-" + dsn, False)

    def _command(self, dsn: str):
        async def handler(cmd: str, value: Any) -> None:
            from ayla_iot_unofficial.fujitsu_consts import FanSpeed, OpMode
            dev = self.devices[dsn]
            async with self.lock:
                await self._auth()
                if cmd == "refresh":
                    pass
                elif cmd == "on":
                    await dev.async_set_op_mode(OpMode.ON)
                elif cmd == "off":
                    await dev.async_set_op_mode(OpMode.OFF)
                elif cmd == "mode":
                    await dev.async_set_op_mode(OpMode[str(value).upper()])
                elif cmd == "setpoint":
                    await dev.async_set_set_temp(float(value))
                elif cmd == "fan":
                    await dev.async_set_fan_speed(FanSpeed[str(value).upper()])
                elif cmd == "swing":
                    # set each axis the unit has (the library also touches axes the unit lacks)
                    from ayla_iot_unofficial.fujitsu_consts import Capability
                    want = str(value).lower()
                    if want not in ("off", "vertical", "horizontal", "both"):
                        raise ValueError(f"unsupported swing {value}")
                    if dev.has_capability(Capability.SWING_VERTICAL):
                        await dev.async_set_vertical_swing(want in ("vertical", "both"))
                    if dev.has_capability(Capability.SWING_HORIZONTAL):
                        await dev.async_set_horizontal_swing(want in ("horizontal", "both"))
                else:
                    raise ValueError(f"unsupported command {cmd}")
                log.info("[fglair] %s %s %s", dsn, cmd, "" if value is None else value)
                if cmd != "refresh":
                    await asyncio.sleep(2)  # the cloud needs a moment before the unit reports back
                await self._refresh(dsn)
        return handler

    async def run(self) -> None:
        if not fglair_configured(self.f):
            log.info("[fglair] not configured")
            return
        import aiohttp
        async with aiohttp.ClientSession() as session:
            self.api = self._new_api(session)
            while True:
                try:
                    await self.api.async_sign_in()
                    break
                except Exception as ex:  # noqa: BLE001
                    log.error("[fglair] login failed: %s (retry in 5 min)", ex)
                    await asyncio.sleep(300)
            log.info("[fglair] logged in")
            while True:
                try:
                    await self._sync()
                except Exception as ex:  # noqa: BLE001
                    log.warning("[fglair] poll failed: %s", ex)
                    REG.set_online("fglair-", False)
                await asyncio.sleep(int(self.f.get("poll_seconds", 60)))


# ---------------------------------------------------------------- LAN HTTP API

SETUP_HTML = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Stoeckie Bridge setup</title>
<style>body{font:15px system-ui,sans-serif;max-width:640px;margin:32px auto;padding:0 16px;color:#222}
label{display:block;margin:14px 0 4px;font-weight:600}input,textarea,select{width:100%;box-sizing:border-box;padding:8px;
font:13px ui-monospace,monospace}textarea{height:90px}button{margin-top:18px;padding:10px 18px;font-size:15px}
.st{padding:10px;border-radius:6px;background:#f2f2f2;margin:12px 0}h3{margin-top:36px}</style></head><body>
<h2>Stoeckie Bridge</h2><div class="st">{status}</div>
<p>Alles wat je hier invult wordt alleen lokaal in config.json op deze pc opgeslagen.</p>
<h3>AEG / Electrolux</h3>
<p>Maak op <b>developer.electrolux.one</b> (inloggen met je AEG account) een API key aan en genereer
een access token en refresh token.</p>
<form method="post"><input type="hidden" name="section" value="aeg">
<label>API key</label><input name="api_key" autocomplete="off" required>
<label>Access token</label><textarea name="access_token" required></textarea>
<label>Refresh token</label><textarea name="refresh_token" required></textarea>
<button type="submit">AEG opslaan en verbinden</button></form>
<h3>Fujitsu airco (FGLair)</h3>
<p>E-mail en wachtwoord van de FGLair app.</p>
<form method="post"><input type="hidden" name="section" value="fglair">
<label>E-mail</label><input name="username" type="email" autocomplete="off" required>
<label>Wachtwoord</label><input name="password" type="password" autocomplete="off" required>
<label>Regio</label><select name="region"><option value="eu">Europa</option><option value="default">Overig</option>
<option value="cn">China</option></select>
<button type="submit">FGLair opslaan en verbinden</button></form></body></html>"""


def make_app(bridge_id: str, on_setup=None) -> web.Application:
    app = web.Application(middlewares=[])

    def local_only(req) -> bool:
        """Setup page: only from this pc (or STBRIDGE_SETUP_ALLOW), only addressed by IP or localhost (blocks
        DNS rebinding) and never posted from another site (blocks cross-site requests from a browser)."""
        try:
            ip = ipaddress.ip_address(req.remote or "")
        except ValueError:
            return False
        if not (ip.is_loopback or any(ip in net for net in SETUP_ALLOW)):
            return False
        host = (req.host or "").rsplit(":", 1)[0].strip("[]").lower()
        if host != "localhost":
            try:
                ipaddress.ip_address(host)
            except ValueError:
                return False
        origin = req.headers.get("Origin")
        if origin and origin != "null":
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc.lower() != (req.host or "").lower():
                return False
        elif origin == "null":
            return False
        return True

    @web.middleware
    async def guard(req, handler):
        # the LAN API is meant for the hub: refuse anything that is not from a private or local address
        if req.path.startswith("/stbridge/"):
            try:
                ip = ipaddress.ip_address(req.remote or "")
            except ValueError:
                ip = None
            if ip is None or not (ip.is_private or ip.is_loopback or ip.is_link_local):
                return web.json_response({"error": "forbidden"}, status=403)
        resp = await handler(req)
        if req.path.startswith("/setup"):
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        return resp

    def status_text() -> str:
        cfg = load_config()
        e, f = cfg.get("electrolux") or {}, cfg.get("fglair") or {}
        lines = []
        devs = [d for d in REG.devices.values() if d["type"] == "dryer"]
        if not aeg_configured(e):
            lines.append("AEG: nog niet ingesteld.")
        elif devs:
            lines.append("AEG verbonden: " + ", ".join(f"{d['name']} ({d['state'].get('applianceState')})" for d in devs))
        else:
            lines.append("AEG ingesteld, nog geen droger gevonden (zie bridge.log).")
        acs = [d for d in REG.devices.values() if d["type"] == "aircon"]
        if not fglair_configured(f):
            lines.append("FGLair: nog niet ingesteld.")
        elif acs:
            lines.append("FGLair verbonden: " + ", ".join(
                f"{d['name']} ({d['state'].get('mode')}, {d['state'].get('setpoint')} C)" for d in acs))
        else:
            lines.append("FGLair ingesteld, nog geen airco gevonden (zie bridge.log).")
        return "<br>".join(html.escape(x) for x in lines)

    async def setup_get(req):
        if not local_only(req):
            return web.Response(status=403, text="setup alleen op deze pc (localhost)")
        return web.Response(text=SETUP_HTML.replace("{status}", status_text()), content_type="text/html")

    async def setup_post(req):
        if not local_only(req):
            return web.Response(status=403, text="setup alleen op deze pc (localhost)")
        form = await req.post()
        cfg = load_config()
        section = str(form.get("section", "aeg"))
        if section == "fglair":
            f = cfg.setdefault("fglair", {})
            f["username"] = str(form.get("username", "")).strip()
            f["password"] = str(form.get("password", ""))
            f["region"] = str(form.get("region", "eu"))
            save_config(cfg)
            log.info("[setup] FGLair login saved")
        else:
            e = cfg.setdefault("electrolux", {})
            for k in ("api_key", "access_token", "refresh_token"):
                e[k] = "".join(str(form.get(k, "")).split())
            save_config(cfg)
            log.info("[setup] AEG credentials saved")
        if on_setup:
            on_setup(section)
        await asyncio.sleep(4)
        raise web.HTTPFound("/setup")

    async def info(_req):
        return web.json_response({"service": "stoeckie-bridge", "id": bridge_id, "version": VERSION,
                                  "name": socket.gethostname()})

    async def devices(_req):
        return web.json_response({"devices": list(REG.devices.values()), "time": int(time.time())})

    async def command(req):
        dev_id = req.match_info["dev_id"]
        handler = REG.handlers.get(dev_id)
        if not handler:
            return web.json_response({"error": "unknown device"}, status=404)
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001
            body = {}
        try:
            await handler(str(body.get("cmd", "")), body.get("value"))
        except Exception as ex:  # noqa: BLE001
            log.error("command %s on %s failed: %s", body, dev_id, ex)
            return web.json_response({"error": str(ex)}, status=500)
        return web.json_response({"ok": True, "device": REG.devices.get(dev_id)})

    app.middlewares.append(guard)
    app.router.add_get("/setup", setup_get)
    app.router.add_post("/setup", setup_post)
    async def root(_req):
        raise web.HTTPFound("/setup")

    app.router.add_get("/", root)
    app.router.add_get("/stbridge/info", info)
    app.router.add_get("/stbridge/devices", devices)
    app.router.add_post("/stbridge/devices/{dev_id}/command", command)
    return app


async def demo_devices() -> None:
    """Fake devices for testing the Edge driver without real hardware (--demo)."""
    pos = {"v": 0}

    async def win(cmd, value):
        pos["v"] = {"open": 100, "close": 0}.get(cmd, int(value or 0) if cmd == "level" else pos["v"])
        REG.upsert("demo-window", "window", "Demo dakraam", {"position": pos["v"], "target": pos["v"],
                                                             "positionState": "stopped"})

    async def dry(cmd, value):
        pass

    ac = {"power": "on", "mode": "heat", "setpoint": 21.0, "temperature": 19.5, "outdoorTemperature": 8.0,
          "fan": "auto", "swing": "off", "supportedModes": ["auto", "cool", "dry", "fan", "heat"],
          "supportedFans": ["quiet", "low", "medium", "high", "auto"],
          "supportedSwings": ["off", "vertical"], "setpointRange": [16.0, 30.0], "model": "DEMO"}

    async def aircon(cmd, value):
        if cmd == "on":
            ac["power"] = "on"
            ac["mode"] = ac.get("last_mode", "heat")
        elif cmd == "off":
            ac["last_mode"], ac["power"], ac["mode"] = ac["mode"], "off", "off"
        elif cmd == "mode":
            ac["power"], ac["mode"] = "on", str(value)
        elif cmd == "setpoint":
            ac["setpoint"] = float(value)
        elif cmd in ("fan", "swing"):
            ac[cmd] = str(value)
        REG.upsert("demo-aircon", "aircon", "Demo airco", ac, True, aircon)

    await win("refresh", None)
    REG.handlers["demo-window"] = win
    await aircon("refresh", None)
    t0 = time.time()
    while True:
        el = int(time.time() - t0) % 180
        reported = {"applianceState": "RUNNING" if el < 120 else "END_OF_CYCLE",
                    "cyclePhase": "DRY" if el < 90 else ("COOL" if el < 120 else "UNAVAILABLE"),
                    "timeToEnd": max(0, 120 - el), "userSelections": {"programUID": "COTTON_PR_COTTONSECO"},
                    "doorState": "CLOSED", "remoteControl": "ENABLED"}
        REG.upsert("demo-dryer", "dryer", "Demo droger", dryer_state(reported, "connected"), True, dry)
        await asyncio.sleep(5)


def seed_from_env(cfg: dict[str, Any]) -> None:
    """First start in Docker: take credentials from env vars. Afterwards config.json (with refreshed tokens) wins."""
    e = cfg.get("electrolux") or {}
    env = {k: os.environ.get("ELECTROLUX_" + k.upper()) for k in ("api_key", "access_token", "refresh_token")}
    if all(env.values()) and not aeg_configured(e):
        cfg.setdefault("electrolux", {}).update(env)
        save_config(cfg)
        log.info("[setup] AEG credentials taken from environment")
    f = cfg.get("fglair") or {}
    user, pw = os.environ.get("FGLAIR_USERNAME"), os.environ.get("FGLAIR_PASSWORD")
    if user and pw and not fglair_configured(f):
        cfg.setdefault("fglair", {}).update({"username": user, "password": pw,
                                             "region": os.environ.get("FGLAIR_REGION", "eu")})
        save_config(cfg)
        log.info("[setup] FGLair login taken from environment")


async def serve(args) -> None:
    cfg = load_config()
    seed_from_env(cfg)
    bridge_id = cfg.get("bridge_id")
    if not bridge_id:
        bridge_id = cfg["bridge_id"] = uuid.uuid4().hex[:12]
        save_config(cfg)
    port = int(cfg.get("port", 8787))
    sources = {"aeg": Electrolux, "fglair": FGLair}
    tasks: dict[str, asyncio.Task] = {}

    def start(section: str) -> None:
        if section not in sources:
            return
        if tasks.get(section):
            tasks[section].cancel()
        for dev_id in [d for d in REG.devices if d.startswith(section + "-")]:
            REG.devices.pop(dev_id, None)
            REG.handlers.pop(dev_id, None)
        tasks[section] = asyncio.create_task(guarded(section, sources[section](load_config()).run))

    runner = web.AppRunner(make_app(bridge_id, None if args.demo else start), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Stoeckie Bridge %s listening on port %d (id %s)", VERSION, port, bridge_id)

    async def guarded(name, coro):
        while True:
            try:
                await coro()
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("[%s] crashed, restart in 60 s", name)
                await asyncio.sleep(60)

    if args.demo:
        asyncio.create_task(demo_devices())
    else:
        for section in sources:
            start(section)
    await asyncio.Event().wait()


# ---------------------------------------------------------------- CLI

async def check_fglair() -> None:
    """Quick test of the FGLair login in config.json."""
    cfg = load_config()
    if not fglair_configured(cfg.get("fglair") or {}):
        print("FGLair: nog niet ingesteld.")
        return
    import aiohttp
    fg = FGLair(cfg)
    async with aiohttp.ClientSession() as session:
        fg.api = fg._new_api(session)
        await fg.api.async_sign_in()
        await fg._sync()
    print(f"FGLair OK, {len(fg.devices)} airco('s):")
    for d in REG.devices.values():
        if d["type"] == "aircon":
            s = d["state"]
            print(f"  {d['name']} model={s.get('model')} online={d['online']} stand={s.get('mode')} "
                  f"setpoint={s.get('setpoint')} binnen={s.get('temperature')} buiten={s.get('outdoorTemperature')} "
                  f"fan={s.get('fan')} modes={s.get('supportedModes')}")


async def check_all() -> None:
    for name, fn in (("AEG", check_aeg), ("FGLair", check_fglair)):
        try:
            await fn()
        except Exception as ex:  # noqa: BLE001
            print(f"{name} FOUT: {ex}")


async def check_aeg() -> None:
    """Quick test of the Electrolux credentials in config.json."""
    e = (load_config().get("electrolux") or {})
    if not aeg_configured(e):
        print("AEG: nog niet ingesteld.")
        return
    from electrolux_group_developer_sdk.auth.token_manager import TokenManager
    from electrolux_group_developer_sdk.client.appliance_client import ApplianceClient
    cfg = load_config()
    holder = Electrolux(cfg)
    tm = TokenManager(e["access_token"], e["refresh_token"], e["api_key"], on_token_update=holder._on_tokens)
    client = ApplianceClient(tm, external_user_agent=f"StoeckieBridge/{VERSION}")
    apps = await client.get_appliances()
    print(f"AEG OK, {len(apps)} apparaat/apparaten:")
    for a in apps:
        st = await client.get_appliance_state(a.applianceId)
        rep = (st.properties or {}).get("reported", {}) or {}
        print(f"  {a.applianceName} type={a.applianceType} verbinding={st.connectionState} "
              f"status={rep.get('applianceState')} fase={rep.get('cyclePhase')} resttijd={rep.get('timeToEnd')}")


def setup_logging(verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stdout is not None:  # pythonw has no console
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("aiohttp.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    p = argparse.ArgumentParser(description="Stoeckie Bridge")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--demo", action="store_true", help="fake devices, for testing the driver")
    p.add_argument("--check", action="store_true", help="test the AEG and FGLair logins, list appliances and exit")
    args = p.parse_args()
    setup_logging(args.verbose)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if args.check:
        asyncio.run(check_all())
    else:
        asyncio.run(serve(args))


if __name__ == "__main__":
    main()
