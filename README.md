# Fujitsu FGLair air conditioners in SmartThings (bridge + Edge driver)

Get **Fujitsu air conditioners with an FGLair wifi adapter** into SmartThings: on/off, mode, target temperature, fan speed, swing, room and outdoor temperature. Works with routines ("heat the office at 7:00", "off when the window opens").

FGLair adapters only talk to the Fujitsu (Ayla) cloud, and SmartThings Edge drivers can not reach the internet. So there are two parts:

```
FGLair (Ayla) cloud <--poll 60 s-- bridge (Windows pc or Docker, port 8787) <--LAN-- Edge driver on the hub
```

* `bridge/`: small Python service for a pc that is always on: Windows (installer) or any Docker host (Linux, NAS, Raspberry Pi)
* `driver/`: SmartThings Edge driver "Stoeckie Bridge (local)" that finds the bridge and creates the devices

The same bridge and driver also handle AEG / Electrolux dryers, see [smartthings-aeg-bridge](https://github.com/Stoeckie/smartthings-aeg-bridge). Use one bridge for both.

## What you get

Per air conditioner:

* `switch`: on / off
* `airConditionerMode`: auto, cool, dry, fan only, heat (what the unit supports)
* `thermostatCoolingSetpoint`: the target temperature in every mode, heat included (16 to 30 °C, 0.5 steps)
* `airConditionerFanMode`: quiet, low, medium, high, auto
* `fanOscillationMode`: fixed, vertical, horizontal, all (what the unit supports)
* `temperatureMeasurement`: room temperature (models with a sensor), plus an `outdoor` component with the outdoor temperature

Tested with indoor units with adapters `AP-WF2E` and `AP-WD1E` (FGLair app 3.4).

## 1. Bridge (Windows pc)

1. Copy the `bridge` folder to the pc, e.g. `C:\StoeckieBridge`.
2. Double-click `install.bat`. It installs Python if needed (winget), the packages, a firewall rule for port 8787 (Windows asks for permission once) and a scheduled task that starts the bridge at logon and restarts it on failure. Then the setup page opens.
3. Enter the e-mail and password of your FGLair app in the **Fujitsu airco (FGLair)** section of the setup page (`setup.bat` or http://localhost:8787/setup, only reachable on the pc itself). Region Europe for EU accounts.
4. `check.bat` tests the login and lists your units. `restart.bat` restarts the bridge. Log: `bridge.log`.

Set the Windows network to **Private** and give the pc a fixed IP (DHCP reservation), otherwise the hub can not reach the bridge. The login only lives in `config.json` on the pc (it is in `.gitignore`).

To update: copy the new files over the old ones and run `install.bat` again; `config.json` stays.

## 1b. Bridge in Docker (instead of Windows)

```
cd bridge
docker compose up -d --build
```

* Host networking, so the hub finds the bridge on the host IP, port 8787. Data (config.json, log) lives in `bridge/data`.
* Setup page: http://localhost:8787/setup on the Docker host. From another computer: set `STBRIDGE_SETUP_ALLOW: "192.168.x.0/24"` in `docker-compose.yml`.
* Or pass the login once as `FGLAIR_USERNAME`, `FGLAIR_PASSWORD`, `FGLAIR_REGION` (only read on the first start).
* Without host networking (Docker Desktop on Windows/Mac): use `ports: ["8787:8787"]` instead of `network_mode: host`, and set `STBRIDGE_SETUP_ALLOW`.

## 2. Edge driver (hub)

1. Enroll your hub in the channel: https://bestow-regional.api.smartthings.com/invite/qWjBdybRNG2y (Stoeckie Edge Drivers) and install **Stoeckie Bridge (local)**.
2. SmartThings app: **Add device → Scan nearby**. The driver scans the hub's subnet on port 8787 and creates a "Stoeckie Bridge" device; the air conditioners appear at the next poll with the names from the FGLair app.

Bridge LAN API (no auth, LAN only): `GET /stbridge/info`, `GET /stbridge/devices`, `POST /stbridge/devices/<id>/command` with `{"cmd": "on|off|mode|setpoint|fan|swing|refresh", "value": ...}`.

## Notes

* FGLair has no local API and no official cloud API. The bridge uses [ayla-iot-unofficial](https://pypi.org/project/ayla-iot-unofficial/), the library the Home Assistant Fujitsu FGLair integration uses. It can stop working when Fujitsu changes the cloud.
* Units the library does not know (for example AC-UTY wired controllers) are skipped with a line in `bridge.log` that shows the model; open an issue with that line.
* Newer Fujitsu **Airstage** modules have a local API and are not covered here.
* Changes made in the FGLair app or with the remote show up in SmartThings within a minute.

## Development

```
pip install -r bridge/requirements.txt pytest lupa
python -m pytest bridge/tests driver/tests
```

The FGLair test runs against a fake Ayla cloud; the driver test runs `driver/src/init.lua` with a mocked SmartThings runtime against the bridge in `--demo` mode.

## License

Apache 2.0, see LICENSE. Not affiliated with Fujitsu General, Ayla Networks or Samsung SmartThings.
