import logging
import threading
import time

import spidev

logger = logging.getLogger(__name__)

READ_X = 0xD0
READ_Y = 0x90

SAMPLES = 5
SAMPLE_DISCARD = 1
RAW_MIN = 100
RAW_MAX = 4000

POLL_INTERVAL = 0.02
RELEASE_INTERVAL = 0.12
REPEAT_INTERVAL = 0.30


class TouchXPT2046:
    """
    Reads the XPT2046 resistive touch controller directly over SPI.

    The controller shares the display's SPI bus on its own chip select, so it
    gets its own handle clocked far slower than the panel. The kernel
    `ads7846` driver is deliberately not used: it regressed on Bookworm and
    needs a device tree overlay we would otherwise not want.

    Touches are reported as `(x, y)` in screen pixels once per press, plus a
    repeat while a press is held, and are dropped entirely while the readings
    disagree with each other - which is how this part reports "no finger".
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

        self.running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        logger.info("XPT2046 touch controller initialised")

    def stop(self):
        self.running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._spi is not None:
            self._spi.close()
            self._spi = None

    def _poll(self):
        pressed_at = None
        repeated_at = None
        released_at = 0.0

        while self.running:
            time.sleep(POLL_INTERVAL)
            now = time.monotonic()
            position = self._read()

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

    def _read(self):
        raw_x = self._read_axis(READ_X)
        raw_y = self._read_axis(READ_Y)

        if raw_x is None or raw_y is None:
            return None

        return self._to_screen(raw_x, raw_y)

    def _read_axis(self, command):
        samples = []
        for _ in range(SAMPLES):
            try:
                response = self._spi.xfer2([command, 0x00, 0x00])
            except OSError as e:
                logger.error(f"Touch controller read failed: {e}")
                return None

            value = ((response[1] << 8) | response[2]) >> 4
            if RAW_MIN < value < RAW_MAX:
                samples.append(value)

        if len(samples) < SAMPLES - SAMPLE_DISCARD:
            return None

        samples.sort()
        return samples[len(samples) // 2]

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
