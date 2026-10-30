# Fujitsu FGLair air conditioners in SmartThings

Get **Fujitsu air conditioners with an FGLair wifi adapter** into SmartThings: on/off, mode, target temperature, fan speed, swing, room and outdoor temperature.

The code lives in **[Stoeckie/smartthings-stoeckie-bridge](https://github.com/Stoeckie/smartthings-stoeckie-bridge)**: one small bridge (Windows or Docker) plus the Edge driver **Stoeckie Bridge (local)**. The same bridge also does AEG / Electrolux dryers and Škoda cars; you only set up what you have.

## What you get

* `switch`: on / off
* `airConditionerMode`: auto, cool, dry, fan only, heat (what the unit supports)
* `thermostatCoolingSetpoint`: the target temperature in every mode, heat included (16 to 30 °C, 0.5 steps)
* `airConditionerFanMode`: quiet, low, medium, high, auto
* `fanOscillationMode`: fixed, vertical, horizontal, all (what the unit supports)
* `temperatureMeasurement`: room temperature (models with a sensor), plus an `outdoor` component with the outdoor temperature

Tested with adapters `AP-WF2E` and `AP-WD1E` (FGLair app 3.4). Changes from the FGLair app or the remote show up within a minute.

## Install

1. Install the bridge and the Edge driver: see [smartthings-stoeckie-bridge](https://github.com/Stoeckie/smartthings-stoeckie-bridge#1-bridge-on-windows).
2. Enter the e-mail and password of your FGLair app in the **Fujitsu airco (FGLair)** section of the bridge setup page (region Europe for EU accounts).
3. SmartThings app: **Add device → Scan nearby**. The air conditioners appear under the Stoeckie Bridge with their FGLair names.

## Notes

* FGLair has no local API and no official cloud API. The bridge uses [ayla-iot-unofficial](https://pypi.org/project/ayla-iot-unofficial/), the library of the Home Assistant FGLair integration. It can stop working when Fujitsu changes the cloud.
* Units with a wired controller (AC-UTY) are not supported by the library. Newer Airstage modules have a local API and are not covered.

## License

Apache 2.0. Not affiliated with Fujitsu General, Ayla Networks or Samsung SmartThings.
