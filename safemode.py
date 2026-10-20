import microcontroller
import supervisor

try:
    import crashlog

    crashlog.safe_mode(supervisor.runtime.safe_mode_reason)
except Exception as e:
    print("Unable to record safe mode: {!r}".format(e))

microcontroller.reset()
