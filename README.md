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
repo, there are two different ways you can work with this code:

1. Copy everything except for `.git` over to the device and then work directly from there.
2. Work from within the git repository and then "deploy" by copying the files you changed.

They both have their pros and cons, so pick your poison.

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

- Copy everything from this folder over to the remote device.

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
[Long Lived Access Token]: https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token
