import board
import microcontroller
import struct

from cedargrove_nau7802_async import NAU7802

scale: NAU7802 = None
tare_weight: int = 0

# Max fractional deviation of any trimmed sample from their average.
MAX_SAMPLE_DEVIATION = 0.01


async def init_scale() -> NAU7802:
    global scale

    load_tare_weight()

    # Instantiate NAU7802 ADC
    print("Initializing scale... ", end="")
    i2c = board.STEMMA_I2C()
    # create_async resets and enables the ADC and raises if either fails.
    scale = await NAU7802.create_async(i2c, address=0x2A, active_channels=1)
    # scale.gain = 2

    internal_calibrated = await scale.calibrate_async("INTERNAL")
    if not internal_calibrated:
        raise RuntimeError("Unable to calibrate NAU7802 internal")

    # offset_calibrated = await scale.calibrate_async("OFFSET")
    # if not offset_calibrated:
    #     raise RuntimeError("Unable to calibrate NAU7802 offset")

    # The first value after calibration seems to be from prior to calibration.
    scale.read()

    print("Done!")

    return scale


def convert_to_grams(raw: int) -> float:
    # These values are specific to my scale.
    # ZERO = 546562
    # 45G = 633114
    # SLOPE = TEST_WEIGHT - ZERO_WEIGHT / 45
    GRAMS_MULTIPLIER = 1923.3
    DEAD_ZONE = 0.15

    grams = (raw - tare_weight) / GRAMS_MULTIPLIER

    # Give ourselves a little buffer so that we don't get jitter around zero.
    if grams < DEAD_ZONE and grams > -DEAD_ZONE:
        grams = 0

    # Round to the nearest 0.1g
    grams = round(grams, 1)

    # print("Raw: {0:6} Scaled: {1:6} Result: {2:6}".format(raw, (raw - tare_weight), grams))
    return grams


async def read_weight() -> float:
    global scale, tare_weight

    raw = await scale.read_async(3)

    return convert_to_grams(raw)


async def read_weight_with_validation() -> float:
    '''
    Attempts to read a weight from the scale.  This  will gather five separate measurements, discard the highest and the
    lowest, and average the remaining values.  If any value differs from the average by more than MAX_SAMPLE_DEVIATION
    then a ValueError will be raised.  Otherwise the value in grams will be returned.
    '''
    global scale, tare_weight

    values = await scale.read_values_async(5)

    if len(values) < 3:
        raise ValueError("At least three values are required")

    sorted_values = sorted(values)
    trimmed_values = sorted_values[1:-1]
    avg = sum(trimmed_values) / len(trimmed_values)
    for value in trimmed_values:
        if abs(value - avg) > MAX_SAMPLE_DEVIATION * abs(avg):
            raise ValueError(
                f"Value {value} is more than {MAX_SAMPLE_DEVIATION * 100:g}% different than the average"
            )

    return convert_to_grams(avg)


async def tare():
    global scale, tare_weight
    print("Taring scale... ", end="")
    raw = await scale.read_async(20)
    tare_weight = raw
    save_tare_weight()
    print("Done!")


def load_tare_weight():
    global tare_weight
    tare_bytes = microcontroller.nvm[0:4]
    tare_weight = struct.unpack(">I", tare_bytes)[0]
    print("Loaded tare weight: {0:10}".format(tare_weight))


def save_tare_weight():
    global tare_weight
    print("Saving tare weight: {0:10}".format(tare_weight))
    tare_bytes = struct.pack(">I", tare_weight)
    microcontroller.nvm[0:4] = tare_bytes
