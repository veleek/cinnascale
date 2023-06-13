import asyncio
import board
import digitalio
import traceback

from led import blink, pixels
from scale import init_scale, read_weight_with_validation, tare
from network import init_network, CinnaBinarySensor, CinnaSensor

EHOSTUNREACH = 118

print()
print("=================================================")
print("CinnaScale - Automated Pet Food Reminder")
print("=================================================")
print()


async def main():
    pixels.fill(0x000033)
    await asyncio.sleep(0.2)
    pixels.fill(0x000000)
    await asyncio.sleep(0.2)
    pixels.fill(0x000033)

    await asyncio.gather(init_network(), init_scale())

    while True:
        try:
            pixels.fill(0x003300)
            await asyncio.sleep(0.1)
            pixels.fill(0x000000)
            await asyncio.sleep(0.1)
            pixels.fill(0x003300)
            await asyncio.sleep(0.1)
            pixels.fill(0x000000)
            await asyncio.sleep(0.1)
            pixels.fill(0x003300)
            await asyncio.sleep(0.1)
            pixels.fill(0x000000)
            await asyncio.sleep(0.1)

            await asyncio.gather(
                weigh(),
                # asyncio.create_task(blink(0.1, 0xFF0000))
                # asyncio.create_task(blink(5, 0x00FF00))
                asyncio.create_task(blink(1.0, 0x000001))
                # asyncio.create_task(fade())
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


async def weigh_test():
    tare_button = digitalio.DigitalInOut(board.BUTTON)
    tare_button.switch_to_input(pull=digitalio.Pull.UP)

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
    tare_button = digitalio.DigitalInOut(board.BUTTON)
    tare_button.switch_to_input(pull=digitalio.Pull.UP)

    weight_sensor = CinnaSensor(
        "cinnascale", "CinnaScale", "weight", "mdi:scale", "measurement", "g"
    )
    empty_sensor = CinnaBinarySensor("cinnascale_empty", "CinnaScale Empty", "battery")
    unstable_sensor = CinnaBinarySensor(
        "cinnascale_unstable", "CinnaScale Unstable", "vibration"
    )

    while True:
        if not tare_button.value:
            await tare()
            continue

        next_delay = 60  # seconds

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

        # print(output)
        # timeAlarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + next_delay)
        # alarm.light_sleep_until_alarms(timeAlarm)

        print("Sleeping a bit...")
        await asyncio.sleep(2)
        print("Done sleeping.")


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
