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

Security: the setup page only answers on the pc itself (or the networks in `STBRIDGE_SETUP_ALLOW`), only when opened by IP or `localhost`, and refuses forms posted from other websites. The LAN API (`/stbridge/...`) only answers private and local addresses.

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

## Škoda (official MyŠkoda public API)

The bridge also reads a **Škoda** car through Škoda's official public API (free, personal use). In SmartThings the car shows, grouped: **main** battery %, charging on/off (switch) and charging power; **Charging cable** plugged in or not; **Climate** air conditioning on/off; **Car** at home (saved charging location) and doors.

1. In the MyŠkoda app (version 8.16 or newer) create an API key: open **go.skoda.eu/api-keys** on your phone and pick your car.
2. Enter the key and the VIN (17 characters) in the **Škoda** section of the setup page.

The API allows 20 calls per hour, so the bridge reads the car every 10 minutes (`"poll_minutes"` in `config.json`, at least 5) and 30 s after a command (once more 60 s later when the car has not reported the change yet). Interval and climate temperature can be changed on the setup page or in the car's settings in the SmartThings app. Keys expire; the setup page shows until when.

## iRobot favorites (optional)

With the [iRobot Roomba (local)](https://github.com/Stoeckie/smartthings-irobot-roomba-local) Edge driver, the bridge can read the names and rooms of your iRobot app favorites, so they show up in SmartThings without setup. Enter the e-mail and password of the iRobot app in the **iRobot** section of the setup page. The bridge only reads the favorites (twice a day, or when you press Refresh on the robot); the robot itself is controlled locally by the hub. Uses [roombapy-prime](https://github.com/johnnyh1975/roombapy-prime), an unofficial client: iRobot limits the number of logins per account.

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
