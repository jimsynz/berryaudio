import logging
import threading
import time

import spidev

logger = logging.getLogger(__name__)

CHIP_ID = 0x0811

REG_CHIP_ID = 0x00
REG_SYS_CTRL1 = 0x03
REG_SYS_CTRL2 = 0x04
REG_INT_CTRL = 0x09
REG_INT_EN = 0x0A
REG_INT_STA = 0x0B
REG_ADC_CTRL1 = 0x20
REG_ADC_CTRL2 = 0x21
REG_TSC_CTRL = 0x40
REG_TSC_CFG = 0x41
REG_FIFO_TH = 0x4A
REG_FIFO_STA = 0x4B
REG_TSC_DATA_X = 0x4D
REG_TSC_FRACTION_Z = 0x56
REG_TSC_I_DRIVE = 0x58

SYS_CTRL1_RESET = 0x02
TSC_CTRL_EN = 0x01
TSC_CTRL_TOUCHED = 0x80
TSC_CFG_4SAMPLE = 0x80
TSC_CFG_DELAY_1MS = 0x20
TSC_CFG_SETTLE_5MS = 0x04
ADC_CTRL1_10BIT = 0x00
ADC_CTRL2_6_5MHZ = 0x02
INT_EN_TOUCHDET = 0x01
INT_CTRL_POL_HIGH = 0x04
INT_CTRL_ENABLE = 0x01
FIFO_STA_RESET = 0x01
FIFO_STA_EMPTY = 0x20

MIN_PRESSURE = 8
POLL_INTERVAL = 0.02
RELEASE_INTERVAL = 0.12
REPEAT_INTERVAL = 0.30


class TouchSTMPE610:
    """
    Reads the STMPE610 resistive touch controller over SPI.

    The controller shares the display's SPI bus on its own chip select, so it
    gets its own handle clocked far slower than the panel. The kernel `stmpe`
    driver is deliberately not used: it needs a device tree overlay that would
    also bind the display, taking the panel away from this process.

    Touches are reported as `(x, y)` in screen pixels once per press, plus a
    repeat while a press is held. The controller reports pressure alongside
    each coordinate, so readings with no weight behind them are discarded
    rather than inferred from the coordinates looking implausible.
    """

    def __init__(
        self,
        width,
        height,
        port=0,
        device=1,
        speed_hz=1000000,
        rotate=0,
        x_min=300,
        x_max=3800,
        y_min=300,
        y_max=3800,
        on_touch=None,
    ):
        self.width = width
        self.height = height
        self.port = port
        self.device = device
        self.speed_hz = speed_hz
        self.rotate = rotate % 4
        self.x_min = x_min
        self.x_max = x_max
        self.y_min = y_min
        self.y_max = y_max
        self.on_touch = on_touch
        self.running = False
        self._spi = None
        self._thread = None

    def start(self):
        try:
            self._spi = spidev.SpiDev()
            self._spi.open(self.port, self.device)
            self._spi.max_speed_hz = self.speed_hz
            self._spi.mode = 0
        except Exception as e:
            logger.error(f"Failed to open touch controller: {e}")
            self._spi = None
            return

        identifier = (self._read(REG_CHIP_ID) << 8) | self._read(REG_CHIP_ID + 1)
        if identifier != CHIP_ID:
            logger.error(
                f"Expected an STMPE610 (0x{CHIP_ID:04x}) on SPI{self.port}.{self.device} "
                f"but read 0x{identifier:04x}; touch disabled"
            )
            self._spi.close()
            self._spi = None
            return

        self._configure()

        self.running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        logger.info("STMPE610 touch controller initialised")

    def stop(self):
        self.running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._spi is not None:
            self._spi.close()
            self._spi = None

    def _configure(self):
        self._write(REG_SYS_CTRL1, SYS_CTRL1_RESET)
        time.sleep(0.01)
        self._write(REG_SYS_CTRL2, 0x00)
        self._write(REG_TSC_CTRL, TSC_CTRL_EN)
        self._write(REG_INT_EN, INT_EN_TOUCHDET)
        self._write(REG_ADC_CTRL1, ADC_CTRL1_10BIT | (0x6 << 4))
        self._write(REG_ADC_CTRL2, ADC_CTRL2_6_5MHZ)
        self._write(
            REG_TSC_CFG, TSC_CFG_4SAMPLE | TSC_CFG_DELAY_1MS | TSC_CFG_SETTLE_5MS
        )
        self._write(REG_TSC_FRACTION_Z, 0x06)
        self._write(REG_FIFO_TH, 0x01)
        self._write(REG_FIFO_STA, FIFO_STA_RESET)
        self._write(REG_FIFO_STA, 0x00)
        self._write(REG_TSC_I_DRIVE, 0x01)
        self._write(REG_INT_STA, 0xFF)
        self._write(REG_INT_CTRL, INT_CTRL_POL_HIGH | INT_CTRL_ENABLE)

    def _poll(self):
        pressed_at = None
        repeated_at = None
        released_at = 0.0

        while self.running:
            time.sleep(POLL_INTERVAL)
            now = time.monotonic()
            position = self._read_point()

            if position is None:
                if pressed_at is not None:
                    released_at = now
                pressed_at = None
                repeated_at = None
                continue

            if pressed_at is None:
                if now - released_at < RELEASE_INTERVAL:
                    continue
                pressed_at = now
                repeated_at = now
                self._report(position)
            elif now - repeated_at >= REPEAT_INTERVAL:
                repeated_at = now
                self._report(position)

    def _report(self, position):
        if self.on_touch is None:
            return
        try:
            self.on_touch(*position)
        except Exception as e:
            logger.error(f"Error handling touch at {position}: {e}")

    def _read_point(self):
        try:
            if not self._read(REG_TSC_CTRL) & TSC_CTRL_TOUCHED:
                self._drain()
                return None

            if self._read(REG_FIFO_STA) & FIFO_STA_EMPTY:
                return None

            data = [self._read(REG_TSC_DATA_X + offset) for offset in range(4)]
        except OSError as e:
            logger.error(f"Touch controller read failed: {e}")
            return None

        self._drain()

        pressure = data[3]
        if pressure < MIN_PRESSURE:
            return None

        raw_x = (data[0] << 4) | (data[1] >> 4)
        raw_y = ((data[1] & 0x0F) << 8) | data[2]
        return self._to_screen(raw_x, raw_y)

    def _drain(self):
        self._write(REG_FIFO_STA, FIFO_STA_RESET)
        self._write(REG_FIFO_STA, 0x00)

    def _to_screen(self, raw_x, raw_y):
        x = (raw_x - self.x_min) / (self.x_max - self.x_min)
        y = (raw_y - self.y_min) / (self.y_max - self.y_min)

        if self.rotate == 1:
            x, y = y, 1.0 - x
        elif self.rotate == 2:
            x, y = 1.0 - x, 1.0 - y
        elif self.rotate == 3:
            x, y = 1.0 - y, x

        return (
            max(0, min(self.width - 1, int(x * self.width))),
            max(0, min(self.height - 1, int(y * self.height))),
        )

    def _read(self, register):
        return self._spi.xfer2([register | 0x80, 0x00])[1]

    def _write(self, register, value):
        self._spi.xfer2([register & 0x7F, value])
