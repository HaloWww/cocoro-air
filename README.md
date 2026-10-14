# cocoro-air

https://cocoroplusapp.jp.sharp/air

## Supported features

- [x] Air Cleaner
    - [x] Temperature Sensor
    - [x] Humidity Sensor
    - [x] Humidifier Mode
    - [x] Fan speed setting (auto or ECHONET levels 1–8, read-only)
    - [x] Operation mode and running status (read-only)
    - [x] PM2.5 concentration and measurement status
    - [x] Odor, dust and air pollution levels
    - [x] Estimated remaining life of dust, deodorizing and humidifier filters, Ag cartridge and PCI unit
    - [x] Operating hours, humidifier filter and dust box maintenance flags

You can use [echonetlite](https://github.com/scottyphillips/echonetlite_homeassistant) for additional controls. The humidity switch remains this integration's only control; new entities report information without changing the device.

Extra readings are decoded using Sharp's published [COCORO AIR Web application](https://cocoroplusapp.jp.sharp/air). They are enabled according to the device's reported capabilities. Version 1.0.2 was verified on KI-PX100; other models may expose fewer fields.

The fan sensor reports the configured ECHONET airflow level, not measured RPM or air velocity. A level does not imply a percentage or a specific physical flow rate. Odor (0–3), dust (0–4) and air pollution (0–4) are Sharp's ordinal levels, where 0 is cleanest; they are not standard AQI values.

PM2.5 uses the low 10 bits of `k1.s7`; its measuring flag and out-of-range values are reported by a separate status sensor rather than as valid numeric concentrations. Filter percentages use the model lifetime, current usage, and account supplies multiplier, matching the vendor's calculation. Capability and supplies metadata refreshes hourly. No CO2 or particle-count entity is created for KI-PX100 because its specification reports those sensors as absent.

## Installation

### Install via HACS Custom repositories

https://hacs.xyz/docs/faq/custom_repositories

## Configuration

1. Go to https://cocoroplusapp.jp.sharp/air and sign in/up
2. Get `device_id` and model name from devtools
3. Go to integrations and search for "Cocoro Air"
4. Input your email, password, device_id and model name

## Model supported
Model that I have tested:
- KILS50
- KIPX100 (live validation of extra readings)

## Authentication and recovery

Version 1.0.1 supports Sharp's Auth0 login flow. Each integration entry uses
its own persistent HTTP cookie session. Expired sessions are renewed and the
failed request is retried once. Polling errors mark entities unavailable rather
than displaying old measurements as current readings.

If authentication requests account confirmation or acceptance of updated terms,
sign in to [COCORO AIR](https://cocoroplusapp.jp.sharp/air) in a browser and complete
that step yourself. Then reload the integration or follow its reauthentication
prompt. The integration never accepts account terms automatically.

Existing temperature, humidity, water tank and humidity mode entity IDs are
preserved. The water tank remains an `on`/`off` enum sensor for compatibility with
existing dashboards. The humidity mode remains a switch.

After replacing the integration files manually, restart Home Assistant to load
the new Python modules.

## Development checks

Install `httpx`, then run:

```sh
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```
