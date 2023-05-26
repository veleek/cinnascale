# SPDX-FileCopyrightText: 2020 Brent Rubell for Adafruit Industries
#
# SPDX-License-Identifier: MIT

import board
import neopixel
import asyncio

import wifi
from secrets import secrets
# import storage

from scale import init_scale, read_weight
from network import init_network, update_sensor_async

print()
print("=================================================")
print("CinnaScale - Automated Pet Food Reminder")
print("=================================================")
print()

pixels = neopixel.NeoPixel(board.NEOPIXEL, 1)
color: int = 0x000000

pixels.fill(0x110011)

print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])

pixels.fill(0x001111)

print("Available WiFi networks:")
for network in wifi.radio.start_scanning_networks():
    print(
        "\t%s\t\tRSSI: %d\tChannel: %d"
        % (str(network.ssid, "utf-8"), network.rssi, network.channel)
    )
wifi.radio.stop_scanning_networks()

pixels.fill(0x111100)

print("Connecting to %s" % secrets["ssid"])
wifi.radio.connect(secrets["ssid"], secrets["password"])
print("Connected to {} with ip {}!".format(
    secrets["ssid"],
    wifi.radio.ipv4_address))


async def main():
    pixels.fill(0x000033)
    await asyncio.sleep(0.5)
    pixels.fill(0x330000)
    await asyncio.sleep(0.5)
    pixels.fill(0x003300)
    await asyncio.sleep(0.5)

    await asyncio.gather(
        init_network(),
        init_scale()
    )

    print("Doing stuff...")

    await asyncio.gather(
        weigh(),
        # asyncio.create_task(blink(0.1, 0xFF0000))
        # asyncio.create_task(blink(5, 0x00FF00))
        asyncio.create_task(blink(0.1, 0x000001))
        # asyncio.create_task(fade())
    )


async def weigh():
    while True:
        print("Weighing...")
        result = await read_weight()
        # output = "Got result: {0:6}".format(result)
        # print(output, end="")
        # print("\b" * len(output), end="")
        # await asyncio.sleep(0.001)

        print("Got result: {0:6}".format(result))
        await update_sensor_async(result)
        print("Updated sensor...")

        await asyncio.sleep(2)


async def blink(delay: float, colorMask: int):
    global color
    while True:
        color = color ^ colorMask
        pixels.fill(color)
        await asyncio.sleep(delay)
        color = color & ~colorMask
        pixels.fill(color)
        await asyncio.sleep(delay)


async def fade():
    brightness: float = 0
    diff: float = 0.005
    while True:
        brightness += diff
        if brightness >= 1:
            diff = diff * -1
        if brightness <= 0:
            diff = diff * -1
        pixels.brightness = brightness
        await asyncio.sleep(0.01)


asyncio.run(main())
