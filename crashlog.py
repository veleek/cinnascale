import gc
import json
import struct
import time

import alarm
import microcontroller
import supervisor

# Diagnostics about why the board last reset, kept in alarm.sleep_memory.  That's RTC RAM, not flash, so writing it
# often costs nothing, and on CircuitPython 10.x it survives watchdog resets and microcontroller.reset() (but not power
# loss or a brownout).  On older releases it's wiped by those resets too, so reports only carry the reset reason.
#
# Sleep memory is split into two regions, each holding 4 magic bytes, a 2 byte length, then JSON:
#  - RUN: what this boot is doing (last step, last error, and why we gave up if we did).  Rewritten often.
#  - PENDING: reports about earlier resets that haven't been posted to Home Assistant yet.  Rewritten rarely.
#
# On boot the previous run's record is turned into a report and queued, so reports from several resets in a row (e.g.
# a reset loop while WiFi is down) are kept until we get through to Home Assistant.

_RUN_OFFSET = 0
_RUN_SIZE = 1024
_PENDING_OFFSET = _RUN_SIZE
_RUN_MAGIC = b"CSR1"
_PENDING_MAGIC = b"CSP1"
_HEADER = 6

MAX_PENDING = 4
MAX_TEXT = 500

_mem = alarm.sleep_memory
_run: dict = {}
_pending: list = []
_dropped = 0


def _read(offset: int, size: int, magic: bytes):
    if _mem[offset : offset + 4] != magic:
        return None
    length = struct.unpack(">H", _mem[offset + 4 : offset + _HEADER])[0]
    if length > size - _HEADER:
        return None
    try:
        return json.loads(bytes(_mem[offset + _HEADER : offset + _HEADER + length]).decode())
    except (ValueError, UnicodeError):
        return None


def _write(offset: int, size: int, magic: bytes, value) -> bool:
    data = json.dumps(value).encode()
    if len(data) > size - _HEADER:
        return False
    _mem[offset + _HEADER : offset + _HEADER + len(data)] = data
    _mem[offset : offset + _HEADER] = magic + struct.pack(">H", len(data))
    return True


def _clip(text: str) -> str:
    # The end of a traceback is the useful part.
    text = str(text)
    return text if len(text) <= MAX_TEXT else "..." + text[-MAX_TEXT:]


def _enum_name(value) -> str:
    return str(value).split(".")[-1].lower()


def _flush_run():
    _run["uptime"] = int(time.monotonic())
    _run["mem_free"] = gc.mem_free()
    # Text is clipped so this should always fit, but if it doesn't, drop the long fields rather than lose the rest.
    for key in ("error", "fatal_detail"):
        if _write(_RUN_OFFSET, _RUN_SIZE, _RUN_MAGIC, _run):
            return
        _run.pop(key, None)
    _write(_RUN_OFFSET, _RUN_SIZE, _RUN_MAGIC, _run)


def _flush_pending():
    global _dropped
    size = len(_mem) - _PENDING_OFFSET
    while not _write(_PENDING_OFFSET, size, _PENDING_MAGIC, {"reports": _pending, "dropped": _dropped}):
        _pending.pop(0)
        _dropped += 1


def _previous_reason(previous: dict) -> str:
    run_reason = _enum_name(supervisor.runtime.run_reason)
    if run_reason != "startup":
        # A soft reload (e.g. a file was saved) doesn't reset the chip, so reset_reason is still the old one.
        return "reload_" + run_reason
    if previous and previous.get("fatal"):
        return previous["fatal"]

    reset_reason = _enum_name(microcontroller.cpu.reset_reason)
    if reset_reason == "watchdog":
        # Nothing got a chance to record why, so the event loop was stuck.  "step" says where.
        return "hang"
    return reset_reason


def start():
    """
    Call once at boot, before anything else can fail.  Turns the previous run's record into a pending report and
    starts a fresh record for this run.
    """
    global _dropped
    previous = _read(_RUN_OFFSET, _RUN_SIZE, _RUN_MAGIC)
    saved = _read(_PENDING_OFFSET, len(_mem) - _PENDING_OFFSET, _PENDING_MAGIC) or {}
    _pending.extend(saved.get("reports", []))
    _dropped = saved.get("dropped", 0)

    report = {"reason": _previous_reason(previous), "reset_reason": _enum_name(microcontroller.cpu.reset_reason)}
    if previous:
        report["boot"] = previous.get("boot", 0)
        for key in ("fatal_detail", "step", "error", "uptime", "mem_free", "safe_mode"):
            if key in previous:
                report[key] = previous[key]

    traceback = supervisor.get_previous_traceback()
    if traceback:
        report["traceback"] = _clip(traceback)

    _pending.append(report)
    while len(_pending) > MAX_PENDING:
        _pending.pop(0)
        _dropped += 1
    _flush_pending()

    _run.clear()
    _run["boot"] = previous.get("boot", 0) + 1 if previous else 1
    _flush_run()

    print("Last reset: {}".format(report))


def step(name: str):
    """Record what we're about to do, so a hang can be traced to where it happened."""
    _run["step"] = name
    _flush_run()


def error(message):
    """Record the most recent recoverable error (e.g. a failed post)."""
    _run["error"] = _clip(message)
    _flush_run()


def fatal(reason: str, detail=None):
    """Record why we're about to reset the board (or let the watchdog do it)."""
    _run["fatal"] = reason
    if detail is not None:
        _run["fatal_detail"] = _clip(detail)
    _flush_run()


def safe_mode(reason):
    """Record that we ended up in safe mode.  Called from safemode.py, where start() hasn't run."""
    # Keep the crashed run's last step and error; they're the best clue to what put us here.
    _run.update(_read(_RUN_OFFSET, _RUN_SIZE, _RUN_MAGIC) or {})
    _run["fatal"] = "safe_mode"
    _run["safe_mode"] = _enum_name(reason)
    _flush_run()


def boot_count() -> int:
    return _run.get("boot", 0)


def pending() -> list:
    return _pending


def report_pending(post) -> bool:
    """
    Post each queued report with post(report) -> bool, oldest first, removing the ones that succeed.  Stops at the
    first failure so the rest are retried next time.  Returns True once the queue is empty.
    """
    global _dropped
    sent = False
    while _pending:
        report = _pending[0]
        if _dropped:
            report["dropped_reports"] = _dropped
        if not post(report):
            break
        _pending.pop(0)
        _dropped = 0
        sent = True

    if sent:
        _flush_pending()
    return not _pending
