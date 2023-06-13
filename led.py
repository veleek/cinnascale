import board
import neopixel
import asyncio

pixels = neopixel.NeoPixel(board.NEOPIXEL, 1)
color: int = 0x000000


async def blink(delay: float, colorMask: int):
    global color, pixels
    while True:
        color = color ^ colorMask
        pixels.fill(color)
        await asyncio.sleep(delay)
        color = color & ~colorMask
        pixels.fill(color)
        await asyncio.sleep(delay)


async def fade():
    global pixels
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
