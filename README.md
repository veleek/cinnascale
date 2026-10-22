# CinnaScale

A network connected kitchen scale that uses a QT Py ESP32-S3 to measure the cat's food bowl and report the results to
[Home Assistant] to notify me when the bowl is empty.

## Hardware

- QT Py ESP32-S3 8MB NO PSRAM ([Adafruit Product Page], [CircuitPython Board Info]) - Any ESP32 board (or other
  microcontroller) should work, but this one is small and has all the built in features I need including a battery
  charger and USB-C port and a STEMMA quick connector which is connected to...
- [AdaFruit NAU7802] - A 24-bit ADC with a STEMMA connector which provides a very simple API for measuring the force
  from...
- Any kitchen scale that has a single four wire strain gauge. I used [this random one that I found for free][Pelouze Dymo SP5].

## Software

The microcontroller runs [CircuitPython]. To update the code, you just edit the files on the device. It will
automatically restart whenever it detects any changes.

One of the biggest problems I found with CircuitPython is that it doesn't play particularly friendly with git. Because
it will auto-restart when anything changes, you don't really want to have a git repository on the device (also
since git will make a BUNCH of small file edits you may reduce the lifetime of your device). So after you sync the git
repo, use `tools/Sync-CircuitPy.ps1` (PowerShell 7) to keep the repo and the `CIRCUITPY` drive in sync:

```powershell
./tools/Sync-CircuitPy.ps1 Watch   # edit in the repo OR on the device; the other side follows on save
./tools/Sync-CircuitPy.ps1         # one-off two-way sync
./tools/Sync-CircuitPy.ps1 Status  # show what would be copied (add -Diff for a git diff of each file)
./tools/Sync-CircuitPy.ps1 Push    # repo wins (Pull: device wins); add -Path main.py to limit it
```

It copies everything git knows about except repo-only files (README, `.vscode`, `tools`, `requirements.txt`) and never
touches `lib/` (use circup) or `secrets.py`. It remembers file hashes from the last sync in `.circuitpy-sync.json`, so
it knows which side changed and warns instead of overwriting when both did. Files you create on the device are listed
but only brought into the repo with `Pull`. Deletes are never synced. The drive is found by its `CIRCUITPY` label, or
pass `-Drive E:\`. The same commands are available as VS Code tasks ("Sync device", "Watch device").

The same commands work over WiFi through CircuitPython's [web workflow] file API. Add `-Address <device>` (e.g.
`cpy-abc123.local` or its IP), and provide the `CIRCUITPY_WEB_API_PASSWORD` from the device's `settings.toml` with
`-Password`, `$env:CIRCUITPY_WEB_API_PASSWORD`, or a local copy of `settings.toml` at the repo root (it's gitignored).
Set `$env:CIRCUITPY_WEB_ADDRESS` to fall back to the web automatically whenever the drive isn't plugged in. `Watch`
polls the device every couple of seconds (`-PollSeconds`). Web writes fail with a 409 while the board is mounted over
USB, because the USB host owns the filesystem then; use `-Drive` or eject it.

## Getting Started

- Connect to the device:

  - USB - if the device is connected to a PC, it will mount a USB device and the files can be edited directly.
  - Web - you can access the CircuitPython web UI directly by navigating to the IP address of the device. From here, you
    can edit files as you normally would.

  The Serial Monitor can be used to see the debug output. Mu (the CircuitPython editor) will automatically detect the
  connected device if connected via USB. Through the Web editor, you can create a remote serial connection as well.

- Install the library dependencies using [circup]

  ```bash
  circup install -r ./requirements.txt
  ```

  `circup` expects to be able to discover the connected CircuitPython device in order to figure out which versions of
  each library it can updated to. If you're trying to do this without a CircuitPython device connected locally you'll
  need to manually specify a few additional things BEFORE the `install` command argument.

  - If you're updating a remote device you'll need to specify `--host <DEVICE NAME or IP>` and `--password <PASSWORD>`
  - If you're updating a git repo that you've pulled down you can specify `--board-id` and `--cpy-version` which you can
    find in `boot.txt` on the device. If you don't have a device connected you can go to the [CircuitPython Board Info]
    page, the board-id is the last part of the URL, but you'll need to know which version of CircuitPython you have
    installed.

- Create a `secrets.py` with your WiFi credentials, Home Assistant URL and [Long Lived Access Token]

  ```python
  secrets = {
    "ssid": "<WiFi SSID>",
    "password": "<WiFi Password>",
    "homeassistant_url": "<Home Assistant URL>",
    "token": "<Long Lived Access Token>"
  }
  ```

  Alternatively, skip `secrets.py` and use setup mode (below).

- Create a `settings.toml` for the web workflow and the device hostname. The web workflow connects to WiFi and starts
  mDNS before `code.py` runs, so `CIRCUITPY_WIFI_HOSTNAME` is the only way to change the name it shows (the default is
  `cpy-<board>-<mac>`). This needs CircuitPython 10.3.1 or later.

  ```toml
  CIRCUITPY_WIFI_SSID = "<WiFi SSID>"
  CIRCUITPY_WIFI_PASSWORD = "<WiFi Password>"
  CIRCUITPY_WEB_API_PASSWORD = "<Web workflow password>"
  CIRCUITPY_WIFI_HOSTNAME = "cinnascale"
  ```

  The device is then at `http://cinnascale.local/`.

- Copy everything from this folder over to the remote device.

### Setup mode

If WiFi or Home Assistant settings are missing, or you hold the Tare button while the device powers on, it starts an
open access point called `CinnaScale-Setup` (the LED blinks green). Connect to it and browse to `http://192.168.4.1/`
to set the WiFi SSID/password, Home Assistant URL and token. Blank fields keep their current value. Saved settings are
stored in NVM, override `secrets.py`, and the device restarts after saving. If nothing is saved within 15 minutes it
restarts and tries the normal startup again.

### Crash reports

The device keeps a small record of what it's doing (last step, last error, and why it gave up if it did) in
`alarm.sleep_memory`. That's RTC RAM rather than flash, so it can be updated constantly without wearing anything out,
and it survives watchdog resets and `microcontroller.reset()` (but not losing power). On boot, the previous run's record
plus `microcontroller.cpu.reset_reason` becomes a report that's queued until it can be posted to Home Assistant as
`sensor.cinnascale_last_reset`, so reports from several resets in a row while offline are delivered once it reconnects.

The sensor's state is the reason and the details are in its attributes, so its history doubles as a crash log:

| State | Meaning |
| --- | --- |
| `hang` | The hardware watchdog fired with nothing recorded, i.e. the event loop was stuck. `step` says where. |
| `no_ha_success` | Nothing got through to Home Assistant for 10 minutes, so the watchdog was left to reset the board. |
| `exception` | An unhandled exception; the traceback is in `fatal_detail`. |
| `safe_mode` | CircuitPython went into safe mode (`safe_mode` has the reason), usually after a hard crash. |
| `manual_restart`, `setup_portal` | The Off button, or leaving setup mode. |
| `power_on`, `brownout`, ... | Any other reset reason, lowercased. A soft reload shows up as `reload_<run reason>`. |

Attributes include `boot` (resets since power on), `uptime` (seconds into that run), `mem_free`, `step` and `error` (the
last recoverable error, e.g. a failed post). Older CircuitPython releases (8.x) clear sleep memory on every reset, so
there you only get the reset reason.

## Notes

- The scale that I'm using has 3 buttons on it.
  - Unit (g/oz) is connected to the MOSI pin on the board. Pressing it will force a manual report of the current weight
    on the scale.
  - Tare is connected to the MISO pin. Pressing it will sleep for 5 seconds giving you time to get the device into a
    "zero weight" state (in my case this is putting the empty cat bowl on top). It will then read the scale's weight (an
    average of 20 samples) and store the result in the microcontroller's non-volatile memory (NVM) which allows it to
    persist across restarts, so if there's a power outage it won't reset the scale's state.
  - Off is connected to the SCK pin. Pressing it manually restarts the micro controller.

[Home Assistant]: https://www.home-assistant.io/
[Adafruit Product Page]: https://www.adafruit.com/product/5426
[CircuitPython Board Info]: https://circuitpython.org/board/adafruit_qtpy_esp32s3_nopsram/
[AdaFruit NAU7802]: https://www.adafruit.com/product/4538
[Pelouze Dymo SP5]: https://www.newegg.com/dymo-by-pelouze-sp5/p/N82E16848077011
[CircuitPython]: https://circuitpython.org/
[circup]: https://github.com/adafruit/circup
[web workflow]: https://docs.circuitpython.org/en/latest/docs/workflows.html#web
[Long Lived Access Token]: https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token
