"""The PiSugar 3 battery under the Pi: its charge and whether it's plugged in, read over I2C (address 0x57,
https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c). read() is None without one
(or with I2C off), and the screens leave the battery out."""

ADDRESS = 0x57
STATUS, VOLT_HIGH, VOLT_LOW, PERCENT = 0x02, 0x22, 0x23, 0x2A
EXTERNAL = 0x80                         # STATUS bit 7: external power is plugged in
LOW, EMPTY = 15, 5                      # percent: warn at LOW, shut down safely at EMPTY (running on battery)


def read(bus=1):
    """{"percent", "mv", "plugged"} or None"""
    try:
        from smbus2 import SMBus
        with SMBus(bus) as b:
            status = b.read_byte_data(ADDRESS, STATUS)
            mv = (b.read_byte_data(ADDRESS, VOLT_HIGH) << 8) | b.read_byte_data(ADDRESS, VOLT_LOW)
            percent = b.read_byte_data(ADDRESS, PERCENT)
    except (ImportError, OSError):
        return None
    if not 2500 <= mv <= 4500 or percent > 100:      # something else answering at 0x57
        return None
    return {"percent": percent, "mv": mv, "plugged": bool(status & EXTERNAL)}
