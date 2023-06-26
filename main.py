import asyncio
import os
import board
import microcontroller
import digitalio
import traceback
import supervisor
import time
import alarm
import wifi

from led import blink, pixels, blink_n
from scale import init_scale, read_weight_with_validation, tare
from network import init_network, CinnaBinarySensor, CinnaSensor

EHOSTUNREACH = 118

print()
print("=================================================")
print("CinnaScale - Automated Pet Food Reminder")
print("=================================================")
print()


def get_button(pin: microcontroller.Pin) -> digitalio.DigitalInOut:
    button = digitalio.DigitalInOut(pin)
    button.switch_to_input(pull=digitalio.Pull.UP)
    return button


boot_button = get_button(board.BUTTON)
unit_button = get_button(board.MOSI)
tare_button = get_button(board.MISO)
off_button = get_button(board.SCK)

taring: bool = False

async def main():
    await blink_n(0.2, 0x000033, 3)

    await asyncio.gather(init_network(), init_scale())

    await blink_n(0.1, 0x110033, 3)

    while True:
        try:
            await asyncio.gather(
                weigh(),
                # asyncio.create_task(blink(0.1, 0xFF0000))
                # asyncio.create_task(blink(5, 0x00FF00))
                blink(1.0, 0x000001),
                # asyncio.create_task(fade())
                watch_buttons(),
            )
        except RuntimeError as re:
            # Check if the cause of the exception was an OSError with EHOSTUNREACH
            if isinstance(re.__cause__, OSError) and re.__cause__.errno == EHOSTUNREACH:
                print("Host unreachable.  Restarting network... ", end="")
                reconnected = await init_network()
                if reconnected:
                    print("Reconnected!")
                else:
                    print("Failed to reconnect.")
                    raise
            else:
                raise


async def watch_buttons():
    global off_button, tare_button, taring

    while True:
        await asyncio.sleep(0.0)

        if not off_button.value:
            print("Shutting down...")
            await blink_n(0.05, 0x226600, 10)
            supervisor.reload()

        if not tare_button.value:
            print("Taring...")
            taring = True
            await blink_n(0.05, 0x441133, 20)
            # Cheap debounce so we don't tare a whole bunch of times.
            await asyncio.sleep(5)
            # await tare()
            taring = False


async def weigh_test():
    global tare_button

    while True:
        if not tare_button.value:
            await tare()
            continue

        success, result = await try_weigh()

        if success:
            output = "Got result: {0:10}".format(result)
        else:
            output = "Unstable                  "

        print(output, end="")
        print("\b" * len(output), end="")
        await asyncio.sleep(1)


async def weigh():
    global taring

    weight_sensor = CinnaSensor(
        "cinnascale", "CinnaScale", "weight", "mdi:scale", "measurement", "g"
    )
    empty_sensor = CinnaBinarySensor("cinnascale_empty", "CinnaScale Empty", "battery")
    unstable_sensor = CinnaBinarySensor(
        "cinnascale_unstable", "CinnaScale Unstable", "vibration"
    )
    connection_strength_sensor = CinnaSensor(
        "cinnascale_connection_strength", "CinnaScale Connection Strength", "signal_strength", "mdi:wifi"
    )

    while True:
        if taring:
            await asyncio.sleep(1)
            continue

        next_delay = 60  # seconds

        connection_strength_sensor.update(wifi.radio.ap_info.rssi)

        success, result = await try_weigh()

        if success:
            weight_sensor.update(result)
            empty_sensor.update(result < 10)
            unstable_sensor.update(False)
        else:
            unstable_sensor.update(True)
            next_delay = 5

        # output = "Got result: {0:10}".format(result)
        # print(output, end="")
        # print("\b" * len(output), end="")
        # await asyncio.sleep(5)

        # print("Sleeping a bit...")
        # print(output)
        # timeAlarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + next_delay)
        # alarm.light_sleep_until_alarms(timeAlarm)

        await asyncio.sleep(next_delay)
        # await asyncio.sleep(2)
        # print("Network: {} RSSI: {}".format(wifi.radio.ap_info.ssid, wifi.radio.ap_info.rssi))
        # print("Done sleeping.")


async def try_weigh() -> tuple[bool, float]:
    try:
        result = await read_weight_with_validation()
        return True, result
    except ValueError:
        # print("Scale is not stable.  Trying again later.")
        # We got a value error which means that the scale is not stable just skip this reading and try again later
        return False, 0.0


try:
    asyncio.run(main())
except Exception as e:
    print("Unhandled exception!  Resetting...")
    traceback.print_exception(e)

    print("Soft resetting...")
    import supervisor

    supervisor.reload()
