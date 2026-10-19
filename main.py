import asyncio
import board
import microcontroller
import digitalio
import time
import traceback
from watchdog import WatchDogMode

import config
import network

from led import blink, blink_n
from scale import init_scale, read_weight_with_validation, tare
from network import init_network, connect_to_network, CinnaScaleDevice

# Hardware watchdog timeout.  If the event loop stalls (e.g. a blocking network call that never returns) for this long
# the board is hard reset.
WATCHDOG_TIMEOUT = 60  # seconds
# If we haven't successfully posted anything to Home Assistant for this long, stop feeding the watchdog so the board
# hard resets.  The connection strength sensor is force-updated every weigh cycle, so this should never be hit while
# things are working.
MAX_TIME_WITHOUT_SUCCESS = 10 * 60  # seconds
NS_PER_SECOND = 1_000_000_000

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
trigger_weigh_event = asyncio.Event()
scale_device = CinnaScaleDevice()


async def main():
    # Hold the tare button while powering on to enter setup mode.
    if config.needs_setup() or not tare_button.value:
        print("Entering setup mode")
        # Imported here so the HTTP server only costs heap when it's actually used.
        from portal import run_portal

        await asyncio.gather(run_portal(), blink(0.5, 0x003300))

    await blink_n(0.2, 0x000033, 3)

    await asyncio.gather(init_network(), init_scale())

    await blink_n(0.1, 0x110033, 3)

    await asyncio.gather(
        weigh(trigger_weigh_event),
        blink(1.0, 0x000005),
        watch_buttons(),
        feed_watchdog(),
    )


async def feed_watchdog():
    """
    Feed the hardware watchdog as long as we've talked to Home Assistant recently.  This catches both a stalled event
    loop (nothing feeds the watchdog) and a "zombie" state where everything is running but no updates are getting
    through, which a reboot usually fixes.
    """
    wdt = microcontroller.watchdog
    wdt.timeout = WATCHDOG_TIMEOUT
    wdt.mode = WatchDogMode.RESET

    while True:
        since_success = (time.monotonic_ns() - network.last_success_ns) // NS_PER_SECOND
        if since_success < MAX_TIME_WITHOUT_SUCCESS:
            wdt.feed()
        else:
            print(f"No successful update for {since_success}s, letting the watchdog reset the board")
        await asyncio.sleep(5)


async def watch_buttons():
    global off_button, tare_button, taring, trigger_weigh_event

    while True:
        await asyncio.sleep(0.05)

        if not off_button.value:
            print("Manual restart...")
            await blink_n(0.05, 0x226600, 10)
            microcontroller.reset()

        if not tare_button.value:
            print("Taring...")
            taring = True
            await blink_n(0.05, 0x441133, 20)
            # Cheap debounce so we don't tare a whole bunch of times.
            await asyncio.sleep(5)
            await tare()
            taring = False

        if not unit_button.value:
            print("Measurement requested.")
            await asyncio.sleep(2)
            # Set this event which should cause the weigh loop to immediately restart
            print("Triggering measurement...")
            trigger_weigh_event.set()


async def weigh_once() -> bool:
    global scale_device

    # Try to reconnect if WiFi dropped.  Keep this short so the rest of the loop stays responsive; if we never get
    # back online the watchdog will reset the board.
    await connect_to_network(max_retries=2)

    scale_device.record_connection_strength()
    success, result = await try_weigh()
    scale_device.record_weight(success, result)
    return success


async def weigh(trigger_weigh_event: asyncio.Event):
    global taring, scale_device

    while True:
        if taring:
            await asyncio.sleep(1)
            continue

        next_delay = 60  # seconds

        if not await weigh_once():
            # If we failed to weigh because we were unstable, then try again more quickly.
            next_delay = 5  # seconds

        # If we happen to have been triggered right after we completed a report, we'll skip it.
        trigger_weigh_event.clear()
        await cancellable_sleep(next_delay, trigger_weigh_event)


async def try_weigh() -> tuple[bool, float]:
    try:
        result = await read_weight_with_validation()
        return True, result
    except ValueError:
        # We got a value error which means that the scale is not stable just skip this reading and try again later
        return False, 0.0


async def cancellable_sleep(delay: float, cancel_event: asyncio.Event):
    '''
    Sleep for the given delay duration while waiting for the provided event to be set.  If the event is set we will
    cancel the sleep early.
    '''
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=delay)
    except asyncio.CancelledError:
        if cancel_event.is_set():
            print("Sleep was cancelled.")
            cancel_event.clear()
    except asyncio.TimeoutError:
        # print("Timeout elapsed.  This is expected")
        pass


try:
    asyncio.run(main())
except Exception as e:
    print("Unhandled exception!  Resetting...")
    traceback.print_exception(e)

    # A hard reset re-initializes the WiFi radio and the NAU7802, unlike supervisor.reload().
    print("Hard resetting...")
    microcontroller.reset()
