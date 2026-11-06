# Fujitsu FGLair air conditioners in SmartThings

Get **Fujitsu air conditioners with an FGLair wifi adapter** into SmartThings: on/off, mode, target temperature, fan speed, swing, Minimum Heat, room and outdoor temperature.

The code lives in **[Stoeckie/smartthings-stoeckie-bridge](https://github.com/Stoeckie/smartthings-stoeckie-bridge)**: one small bridge (Windows or Docker) plus the Edge driver **Stoeckie Bridge (local)**. The same bridge also does AEG / Electrolux dryers, Škoda cars, the VELUX ACTIVE departure mode and a rain radar (Buienradar in NL/BE, Open-Meteo worldwide); you only set up what you have. The Stoeckie Bridge device has a Connection status (Connected or Offline), so a routine can notify you when the bridge goes offline.

## What you get

* `switch`: on / off
* Mode: auto, cool, dry, fan only, heat (what the unit supports), plus **Minimum Heat** on units that have it, just like in the FGLair app (keeps the room at a low frost protection temperature, handy for holiday homes); labels in English or Dutch following the phone language
* Target temperature, shown as **Set temperature** (custom capability, also kept as standard `thermostatCoolingSetpoint` for voice assistants), 16 to 30 °C in 0.5 steps, in every mode
* `airConditionerFanMode`: quiet, low, medium, high, auto
* `fanOscillationMode`: fixed, vertical, horizontal, all (what the unit supports)
* **Airflow direction** (Blaasrichting): louver position 1 to n when swing is off, on units that report positions (both test units have 4)
* `temperatureMeasurement`: room temperature (models with a sensor), plus an `outdoor` component with the outdoor temperature

Tested with adapters `AP-WF2E` and `AP-WD1E` (FGLair app 3.4). Changes from the FGLair app or the remote show up within a minute.

## Install

1. Install the bridge and the Edge driver: see [smartthings-stoeckie-bridge](https://github.com/Stoeckie/smartthings-stoeckie-bridge#1-bridge-on-windows).
2. Enter the e-mail and password of your FGLair app in the **Fujitsu airco (FGLair)** section of the bridge setup page (region Europe for EU accounts).
3. SmartThings app: **Add device → Scan nearby**. The air conditioners appear under the Stoeckie Bridge with their FGLair names.

## Notes

* The bridge reads the units every 60 seconds. Change it in the settings of any air conditioner in SmartThings (applies to all) or on the bridge setup page (30 to 600 s).
* FGLair has no local API and no official cloud API. The bridge uses [ayla-iot-unofficial](https://pypi.org/project/ayla-iot-unofficial/), the library of the Home Assistant FGLair integration. It can stop working when Fujitsu changes the cloud.
* Units with a wired controller (AC-UTY) are not supported by the library. Newer Airstage modules have a local API and are not covered.

## License

Apache 2.0. Not affiliated with Fujitsu General, Ayla Networks or Samsung SmartThings.
