import logging
import threading
import time

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.core.sprite_system import framerate_regulator
from luma.lcd.device import ili9341

from core.types import Command, DisplayPage, PlaybackState
from display.widgets.cover_art import WidgetCoverArt
from display.widgets.vu_meter import WidgetVUMeter
from display.widgets.spectrum_analyzer import WidgetSpectumAnalyzer
from display.widgets.text_scrollable import WidgetTextScrollable
from display.widgets.codec_bitrate import WidgetCodecBitrate
from display.widgets.list_scrollable import WidgetListScrollable
from display.widgets.play_pause import WidgetPlayPause
from display.widgets.progress_bar import WidgetProgressBar
from display.widgets.loader import WidgetLoader
from display.utils import format_time, mix_colour, power_state_name, scale_colour
from .xpt2046 import TouchXPT2046

logger = logging.getLogger(__name__)

FONT_BODY = Path(__file__).parent.parent / "fonts" / "pixChicago.ttf"
FONT_NUMERIC = Path(__file__).parent.parent / "fonts" / "DotMatrix-Custom-5x7.ttf"
FONT_LABEL = Path(__file__).parent.parent / "fonts" / "3x5pexel.ttf"

ICON_SPEAKER = Path(__file__).parent.parent / "icons" / "speaker.png"
ICON_SHUFFLE = Path(__file__).parent.parent / "icons" / "shuffle.png"
ICON_REPEAT = Path(__file__).parent.parent / "icons" / "repeat.png"
ICON_SINGLE = Path(__file__).parent.parent / "icons" / "single.png"

SPI_PORT = 0
SPI_DEVICE = 0
SPI_DC_PIN = 22
SPI_RST_PIN = 27
SPI_SPEED_HZ = 48000000

TOUCH_DEVICE = 1
TOUCH_SPEED_HZ = 1000000
TOUCH_HINT_DURATION = 1.5

DISPLAY_WIDTH = 320
DISPLAY_HEIGHT = 240
DISPLAY_FRAMERATE = 20

STATUS_HEIGHT = 22
COVER_X = 10
COVER_Y = 30
COVER_SIZE = 152
TEXT_X = 176
TEXT_WIDTH = 138
BAND_Y = 188
BAND_HEIGHT = 44
PROGRESS_X = 4
PROGRESS_WIDTH = DISPLAY_WIDTH - (2 * PROGRESS_X)

COLOUR_PRIMARY = "white"
COLOUR_SECONDARY = (154, 154, 154)
COLOUR_DIM = (104, 104, 104)
COLOUR_TRACK = (38, 38, 38)
COLOUR_OVERLAY = (16, 16, 16)

ANIMATED_PAGES = (
    DisplayPage.LOADING,
    DisplayPage.MUTE,
    DisplayPage.NOW_PLAYING,
    DisplayPage.POWER_STATE_CHANGING,
    DisplayPage.STANDBY,
)

LIST_PAGES = (DisplayPage.DIRECTORY, DisplayPage.SOURCE_DIRECTORY)

LIST_ZONES = (
    (Command.UP, (260, 0, 320, 120), "UP"),
    (Command.DOWN, (260, 120, 320, 240), "DOWN"),
    (Command.BACK, (0, 0, 60, 240), "BACK"),
    (Command.SELECT, (60, 0, 260, 240), "SELECT"),
)

PLAYER_ZONES = (
    (Command.SOURCE, (0, 0, 106, 60), "SOURCE"),
    (Command.STANDBY, (214, 0, 320, 60), "STANDBY"),
    (Command.VISUALISER, (0, 186, 320, 240), "VISUALISER"),
    (Command.DIRECTORY, (0, 60, 320, 186), "BROWSE"),
)


class DisplayILI9341:
    def __init__(self, config=None, on_command=None):
        config = config or {}
        self.width = DISPLAY_WIDTH
        self.height = DISPLAY_HEIGHT
        self._rotate = config.get("rotate", 0)
        self._framerate = config.get("framerate", DISPLAY_FRAMERATE)
        self._spi_speed_hz = config.get("spi_speed_hz", SPI_SPEED_HZ)
        self._backlight_pin = config.get("backlight_pin")
        self._config = config
        self._on_command = on_command
        self._serial = None
        self._device = None
        self._touch = None
        self.running = False
        self.display_thread = None
        self._dirty = True
        self._volume = 0
        self._muted = False
        self._playback_state = PlaybackState.STOPPED
        self._single = False
        self._repeat = False
        self._shuffle = False
        self._page = None
        self._source = None
        self._power_state = "standby"
        self._blink_visible = False
        self._current_track = None
        self._current_elapsed = 0
        self._current_time = None
        self._current_dir = None
        self._source_dir = None
        self._widget_visualizer = None
        self._visualizer_layout = 1
        self._accent = None
        self._spectrum_gradient = None
        self._vu_gradient = None
        self._hint_overlays = {}
        self._hint_until = 0.0
        self._font_status = ImageFont.truetype(str(FONT_BODY), 8)
        self._font_volume = ImageFont.truetype(str(FONT_NUMERIC), 24)
        self._font_headline = ImageFont.truetype(str(FONT_NUMERIC), 48)
        self._font_hint = ImageFont.truetype(str(FONT_BODY), 10)
        self._icon_speaker = self._load_icon(ICON_SPEAKER)
        self._icon_shuffle = self._load_icon(ICON_SHUFFLE)
        self._icon_repeat = self._load_icon(ICON_REPEAT)
        self._icon_single = self._load_icon(ICON_SINGLE)
        self._cover_art = WidgetCoverArt(size=COVER_SIZE)
        self._widget_bitrate = WidgetCodecBitrate(
            font_path=FONT_LABEL,
            outline_color=COLOUR_DIM,
            highlight_color=COLOUR_DIM,
            highlight_text_color="black",
        )
        self._widget_title = WidgetTextScrollable(
            font_size=14, font_path=FONT_BODY, scroll_speed=1.0,
            start_pause_duration=30, end_pause_duration=30,
        )
        self._widget_artist = WidgetTextScrollable(
            font_size=10, font_path=FONT_BODY, scroll_speed=1.0,
            start_pause_duration=30, end_pause_duration=30,
        )
        self._widget_album = WidgetTextScrollable(
            font_size=8, font_path=FONT_BODY, scroll_speed=1.0,
            start_pause_duration=30, end_pause_duration=30,
        )
        self._widget_headline = WidgetTextScrollable(
            font_size=48, font_path=FONT_NUMERIC, scroll_speed=1.0
        )
        self._widget_play_pause = WidgetPlayPause(width=9, height=13)
        self.list_scrollable = WidgetListScrollable(
            display_width=self.width,
            display_height=self.height,
            font_size=16,
            font_path=FONT_BODY,
            line_height=30,
            max_chars=28,
            show_counter=False,
            icon_scale=2,
            icon_dim_color=COLOUR_DIM,
        )
        self.list_scrollable_source = WidgetListScrollable(
            display_width=self.width,
            display_height=self.height,
            font_size=16,
            font_path=FONT_BODY,
            line_height=30,
            max_chars=28,
            show_counter=False,
            icon_scale=2,
            icon_dim_color=COLOUR_DIM,
        )
        self.progress_bar = WidgetProgressBar(
            bar_height=4,
            bar_outline_color=False,
            track_color=COLOUR_TRACK,
            track_pattern="solid",
            show_labels=False,
        )
        self.loader = WidgetLoader(
            display_width=self.width,
            display_height=self.height,
            spinner_radius=20,
            dot_radius=4,
        )

    def _load_icon(self, path):
        icon = Image.open(path)
        return icon.resize((icon.width * 2, icon.height * 2), Image.NEAREST)

    def _set_power_state(self, state):
        self._power_state = state
        self._dirty = True

    def _set_page(self, page):
        self._page = page
        self._dirty = True

    def _set_source_dir(self, dir, selected_index=0, scroll_offset=0):
        self._source_dir = dir
        self.list_scrollable_source.set_items(
            self._source_dir, selected_index, scroll_offset
        )
        self._dirty = True

    def _get_selected_source(self):
        if self._source_dir is not None:
            return self.list_scrollable_source.get_selected_item()
        return (None, 0, 0)

    def _set_source(self, source):
        self._source = source
        self._dirty = True

    def _set_current_track(self, track):
        self._current_track = track
        self._cover_art.set_track(track)
        self._dirty = True

    def _set_current_elapsed(self, elapsed=0):
        self._current_elapsed = elapsed
        self._dirty = True

    def _set_dir_scroll_up(self):
        self.list_scrollable.scroll_up()
        self.list_scrollable_source.scroll_up()
        self._dirty = True

    def _set_dir_scroll_down(self):
        self.list_scrollable.scroll_down()
        self.list_scrollable_source.scroll_down()
        self._dirty = True

    def _get_selected_item(self):
        if self._current_dir is not None:
            return self.list_scrollable.get_selected_item()
        return (None, 0, 0)

    def _set_dir(self, dir, selected_index=0, scroll_offset=0):
        self._current_dir = dir
        self.list_scrollable.set_items(self._current_dir, selected_index, scroll_offset)
        self._dirty = True

    def _set_playback_state(self, state):
        self._playback_state = state
        self._dirty = True

    def _set_playback_mode(self, single, repeat, shuffle):
        self._single = single
        self._repeat = repeat
        self._shuffle = shuffle
        self._dirty = True

    def _set_volume(self, volume):
        self._volume = volume
        self._dirty = True

    def _set_mute(self, mute):
        self._muted = mute
        self._dirty = True

    def _set_current_time(self, time):
        self._current_time = format_time(time)
        self._dirty = True

    def _set_blink_visible(self, state):
        self._blink_visible = state
        self._dirty = True

    def _set_visualizer_layout(self, layout):
        self._visualizer_layout = layout
        if self._widget_visualizer:
            self._widget_visualizer.cleanup()
            self._widget_visualizer = None

        if self._visualizer_layout in [1, 2, 3]:
            self._widget_visualizer = WidgetSpectumAnalyzer(num_bars=64)
        elif self._visualizer_layout == 4:
            self._widget_visualizer = WidgetSpectumAnalyzer(num_bars=128)
        elif self._visualizer_layout in [5, 6]:
            self._widget_visualizer = WidgetVUMeter()

        self._dirty = True

    def init(self):
        try:
            self._serial = spi(
                port=SPI_PORT,
                device=SPI_DEVICE,
                gpio_DC=SPI_DC_PIN,
                gpio_RST=SPI_RST_PIN,
                bus_speed_hz=self._spi_speed_hz,
                reset_hold_time=0.05,
                reset_release_time=0.15,
            )
            self._device = ili9341(
                self._serial,
                width=self.width,
                height=self.height,
                rotate=self._rotate,
                **self._backlight_options(),
            )
        except Exception as e:
            logger.error(f"Failed to initialize ILI9341 display: {e}")
            self._serial = None
            self._device = None
            return

        self._start_touch()

        if not self.running:
            self.running = True
            self.display_thread = threading.Thread(
                target=self._handle_messages, daemon=True
            )
            self.display_thread.start()
            logger.info("ILI9341 Display Initialized")

    def stop(self):
        self.running = False
        if self.display_thread is not None and self.display_thread.is_alive():
            self.display_thread.join(timeout=1.0)
        if self._touch is not None:
            self._touch.stop()
            self._touch = None
        if self._widget_visualizer is not None:
            self._widget_visualizer.cleanup()
            self._widget_visualizer = None
        if self._device:
            self._device.clear()
        logger.info("Stopped")

    def _backlight_options(self):
        if self._backlight_pin is None:
            return {"backlight": lambda enabled: None}
        return {"gpio_LIGHT": self._backlight_pin}

    def _start_touch(self):
        if not self._config.get("touch_enabled", True):
            return

        self._touch = TouchXPT2046(
            width=self.width,
            height=self.height,
            port=SPI_PORT,
            device=TOUCH_DEVICE,
            speed_hz=self._config.get("touch_speed_hz", TOUCH_SPEED_HZ),
            rotate=self._config.get("touch_rotate", 0),
            x_min=self._config.get("touch_x_min", 300),
            x_max=self._config.get("touch_x_max", 3800),
            y_min=self._config.get("touch_y_min", 300),
            y_max=self._config.get("touch_y_max", 3800),
            on_touch=self._on_touch,
        )
        self._touch.start()

    def _on_touch(self, x, y):
        self._hint_until = time.monotonic() + TOUCH_HINT_DURATION
        self._dirty = True

        for action, (left, top, right, bottom), _ in self._zones():
            if left <= x < right and top <= y < bottom:
                logger.debug(f"Touch at ({x}, {y}) is '{action}'")
                if self._on_command is not None:
                    self._on_command(action)
                return

    def _zones(self):
        return LIST_ZONES if self._page in LIST_PAGES else PLAYER_ZONES

    def _handle_messages(self):
        regulator = framerate_regulator(fps=self._framerate)

        while self.running:
            with regulator:
                if not self._dirty and self._page not in ANIMATED_PAGES:
                    continue

                self._dirty = False
                self._refresh_accent()

                with canvas(self._device) as draw:
                    self._draw_page(draw)
                    self._draw_hints(draw)

    def _refresh_accent(self):
        if self._cover_art.accent == self._accent:
            return

        self._accent = self._cover_art.accent
        self._spectrum_gradient = self._build_gradient(
            self.width, BAND_HEIGHT, vertical=True
        )
        self._vu_gradient = self._build_gradient(
            self.width, BAND_HEIGHT, vertical=False
        )
        self.progress_bar.bar_color = self._accent
        self.loader.color = self._accent
        self.list_scrollable.selection_color = self._accent
        self.list_scrollable_source.selection_color = self._accent
        self._hint_overlays = {}

    def _build_gradient(self, width, height, vertical):
        gradient = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(gradient)
        span = height if vertical else width

        for step in range(span):
            position = step / max(1, span - 1)
            if vertical:
                colour = mix_colour(
                    scale_colour(self._accent, 0.5), "white", 0.55 * (1 - position)
                )
                draw.line((0, step, width - 1, step), fill=colour)
            else:
                colour = mix_colour(
                    scale_colour(self._accent, 0.5), "white", 0.55 * position
                )
                draw.line((step, 0, step, height - 1), fill=colour)

        return gradient

    def _draw_page(self, draw):
        if self._page == DisplayPage.STANDBY:
            if self._blink_visible:
                self._draw_headline(draw, self._current_time)

        elif self._page == DisplayPage.POWER_STATE_CHANGING:
            self._draw_headline(draw, power_state_name(self._power_state), scroll=False)

        elif self._page == DisplayPage.SOURCE:
            self._draw_headline(draw, self._source.name, scroll=False)

        elif self._page == DisplayPage.VOLUME:
            self._draw_headline(draw, f"VOLUME {self._volume}")

        elif self._page == DisplayPage.MUTE:
            self._draw_headline(draw, "MUTE")

        elif self._page == DisplayPage.LOADING:
            self.loader.draw(draw)

        elif self._page == DisplayPage.SOURCE_DIRECTORY:
            self.list_scrollable_source.draw(draw)

        elif self._page == DisplayPage.DIRECTORY:
            self.list_scrollable.draw(draw)

        elif self._page == DisplayPage.NOW_PLAYING:
            self._draw_now_playing(draw)

    def _draw_headline(self, draw, text, scroll=True):
        if not text:
            return
        self._widget_headline.draw(
            draw,
            width=self.width,
            y=(self.height // 2) - 24,
            text=text,
            auto_scroll=scroll,
            center=True,
        )

    def _draw_now_playing(self, draw):
        self._draw_status_bar(draw)
        self._cover_art.draw(draw, COVER_X, COVER_Y)
        self._draw_track_details(draw)
        self._draw_band(draw)
        self._draw_progress(draw)

    def _draw_status_bar(self, draw):
        draw.line(
            (0, STATUS_HEIGHT, self.width - 1, STATUS_HEIGHT), fill=COLOUR_TRACK
        )

        if self._source is not None and self._source.name:
            draw.text(
                (6, 5), self._source.name, font=self._font_status, fill=COLOUR_SECONDARY
            )

        if self._shuffle:
            draw.bitmap((168, 3), self._icon_shuffle, fill=self._accent)
        if self._repeat:
            draw.bitmap((188, 3), self._icon_repeat, fill=self._accent)
        if self._single:
            draw.bitmap((208, 3), self._icon_single, fill=self._accent)

        if self._current_track is not None and self._current_track.audio_codec:
            self._widget_bitrate.draw(
                draw,
                230,
                0,
                bitrate=self._current_track.bitrate,
                audio_codec=self._current_track.audio_codec,
                box_width=30,
                box_height=22,
                codec_font_size=8,
                bitrate_font_size=8,
            )

        draw.bitmap((266, 4), self._icon_speaker, fill=COLOUR_SECONDARY)
        draw.text(
            (278, 1),
            str(self._volume),
            font=self._font_volume,
            fill=COLOUR_PRIMARY,
        )

    def _draw_track_details(self, draw):
        track = self._current_track

        title = track.name if track is not None and track.name else None
        if title is None and self._source is not None and self._source.state:
            title = self._source.state.name

        if title:
            self._widget_title.draw(
                draw, width=TEXT_WIDTH, x=TEXT_X, y=38, text=title
            )

        if self._muted:
            if self._blink_visible:
                self._widget_artist.draw(
                    draw, width=TEXT_WIDTH, x=TEXT_X, y=70, text="MUTED"
                )
        else:
            artist = None
            if track is not None and len(track.artists):
                artist = next(iter(track.artists)).name
            elif self._source is not None:
                artist = self._source.name

            if artist:
                self._widget_artist.draw(
                    draw, width=TEXT_WIDTH, x=TEXT_X, y=70, text=artist
                )

        album = None
        if track is not None and len(track.albums):
            album = next(iter(track.albums)).name

        if album:
            self._widget_album.draw(
                draw,
                width=TEXT_WIDTH,
                x=TEXT_X,
                y=94,
                text=album,
                text_color=COLOUR_SECONDARY,
            )

        if track is not None and track.name:
            self._widget_play_pause.draw(
                draw, TEXT_X, 164, state=self._playback_state
            )

        draw.text(
            (TEXT_X + 18, 165),
            f"{self._format_elapsed(self._current_elapsed)} / "
            f"{self._format_elapsed(track.length if track else None)}",
            font=self._font_status,
            fill=COLOUR_SECONDARY,
        )

    def _format_elapsed(self, milliseconds):
        if not milliseconds:
            return "0:00"
        seconds = int(milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _draw_band(self, draw):
        if isinstance(self._widget_visualizer, WidgetSpectumAnalyzer):
            self._widget_visualizer.draw(
                draw,
                width=self.width,
                height=BAND_HEIGHT,
                x=0,
                y=BAND_Y,
                bar_image=self._spectrum_gradient,
                peak_color=COLOUR_PRIMARY,
                baseline_color=COLOUR_TRACK,
                bar_gap=1 if self._visualizer_layout == 4 else 2,
            )

        elif isinstance(self._widget_visualizer, WidgetVUMeter):
            self._widget_visualizer.draw(
                draw,
                width=self.width - 16,
                height=BAND_HEIGHT - 14,
                x=8,
                y=BAND_Y + 4,
                bar_pattern="solid" if self._visualizer_layout == 5 else "checkerboard",
                bar_image=self._vu_gradient if self._visualizer_layout == 5 else None,
                bar_color=self._accent,
                peak_color=COLOUR_PRIMARY,
                frame_color=COLOUR_DIM,
                label_color=COLOUR_DIM,
                font_size=20,
            )

    def _draw_progress(self, draw):
        expanded = self._widget_visualizer is None
        self.progress_bar.bar_height = 10 if expanded else 4
        self.progress_bar.draw(
            draw,
            width=PROGRESS_WIDTH,
            x=PROGRESS_X,
            y=222 if expanded else 234,
            elapsed=self._current_elapsed,
            total=self._current_track.length if self._current_track else None,
        )

    def _draw_hints(self, draw):
        if time.monotonic() >= self._hint_until:
            return

        zones = self._zones()
        overlay = self._hint_overlays.get(zones)
        if overlay is None:
            overlay = self._build_hints(zones)
            self._hint_overlays[zones] = overlay

        draw._image.paste(overlay[0], (0, 0), overlay[1])

    def _build_hints(self, zones):
        image = Image.new("RGB", (self.width, self.height), COLOUR_OVERLAY)
        mask = Image.new("L", (self.width, self.height), 0)
        image_draw = ImageDraw.Draw(image)
        mask_draw = ImageDraw.Draw(mask)

        for _, (left, top, right, bottom), label in zones:
            box = (left + 3, top + 3, right - 4, bottom - 4)
            mask_draw.rectangle(box, fill=150)
            mask_draw.rectangle(box, outline=235, width=2)
            image_draw.rectangle(box, outline=self._accent, width=2)

            bounds = image_draw.textbbox((0, 0), label, font=self._font_hint)
            image_draw.text(
                (
                    (left + right - (bounds[2] - bounds[0])) // 2,
                    (top + bottom - (bounds[3] - bounds[1])) // 2,
                ),
                label,
                font=self._font_hint,
                fill=COLOUR_PRIMARY,
            )
            mask_draw.text(
                (
                    (left + right - (bounds[2] - bounds[0])) // 2,
                    (top + bottom - (bounds[3] - bounds[1])) // 2,
                ),
                label,
                font=self._font_hint,
                fill=255,
            )

        return (image, mask)
