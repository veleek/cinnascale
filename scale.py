import board

from cedargrove_nau7802_async import NAU7802

scale: NAU7802 = None


async def init_scale() -> NAU7802:
    global scale

    # Instantiate NAU7802 ADC
    print("Initializing scale... ", end="")
    i2c = board.STEMMA_I2C()
    scale: NAU7802 = NAU7802(i2c, address=0x2A, active_channels=1)
    # scale.gain = 2

    enabled = await scale.enable()
    if not enabled:
        raise RuntimeError("Unable to enable NAU7802 ADC")

    # await scale.zero_channel()

    internal_calibrated = await scale.calibrate("INTERNAL")
    if not internal_calibrated:
        raise RuntimeError("Unable to calibrate NAU7802 internal")

    offset_calibrated = await scale.calibrate("OFFSET")
    if not offset_calibrated:
        raise RuntimeError("Unable to calibrate NAU7802 offset")

    # The first value after calibration seems to be from prior to calibration.
    scale.read()

    print("Done!")

    return scale


async def read_weight() -> int:
    global scale
    return await readWeight(scale)


async def readWeight(scale: NAU7802) -> int:
    raw = await scale.read_raw_value(3)
    scaled = round(raw / 100)

    return scaled
