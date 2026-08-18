import logging
import threading

from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

from PIL import Image

from display.utils import parse_colour, scale_colour

logging.getLogger("PIL").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).parent.parent.parent / "web" / "www"
PLACEHOLDER = Path(__file__).parent.parent / "icons" / "music_note.png"

FETCH_TIMEOUT = 5.0
PALETTE_SIZE = 8
THUMBNAIL_SIZE = 32
MIN_ACCENT_LUMA = 96


class WidgetCoverArt:
    """
    Renders album art scaled to a fixed square, and derives an accent colour
    from it. Both the scaled image and the accent are cached against the source
    URI so a track only costs one decode.
    """

    def __init__(self, size=152, default_accent="#00b0a0", border=1):
        self.size = size
        self.default_accent = parse_colour(default_accent)
        self.border = border
        self.accent = self.default_accent
        self._uri = None
        self._image = None
        self._placeholder = None

    def set_track(self, track):
        uri = self._image_uri(track)
        if uri == self._uri:
            return False

        self._uri = uri
        self._image = None
        self.accent = self.default_accent

        if not uri:
            return True

        if uri.startswith("http://") or uri.startswith("https://"):
            threading.Thread(target=self._apply, args=(uri,), daemon=True).start()
        else:
            self._apply(uri)

        return True

    def _apply(self, uri):
        image = self._load(uri)

        if uri != self._uri:
            return

        self._image = image
        self.accent = (
            self._extract_accent(image) if image is not None else self.default_accent
        )

    def draw(self, draw, x, y):
        image = self._image if self._image is not None else self._get_placeholder()

        if self.border:
            draw.rectangle(
                (
                    x - self.border,
                    y - self.border,
                    x + self.size + self.border - 1,
                    y + self.size + self.border - 1,
                ),
                outline=self.accent,
                width=self.border,
            )

        draw._image.paste(image, (x, y))

    def _image_uri(self, track):
        if track is None:
            return None

        for image in track.images or ():
            if image.uri:
                return image.uri

        for album in track.albums or ():
            for image in album.images or ():
                if image.uri:
                    return image.uri

        return None

    def _load(self, uri):
        try:
            if uri.startswith("http://") or uri.startswith("https://"):
                with urlopen(uri, timeout=FETCH_TIMEOUT) as response:
                    source = Image.open(BytesIO(response.read()))
            else:
                source = Image.open(WEB_ROOT / uri.lstrip("/"))

            return self._fit(source)
        except Exception as e:
            logger.warning(f"Unable to load cover art from '{uri}': {e}")
            return None

    def _fit(self, source):
        source = source.convert("RGB")
        width, height = source.size
        edge = min(width, height)
        left = (width - edge) // 2
        top = (height - edge) // 2
        square = source.crop((left, top, left + edge, top + edge))
        return square.resize((self.size, self.size), Image.BILINEAR)

    def _get_placeholder(self):
        if self._placeholder is None:
            note = Image.open(PLACEHOLDER).getchannel("A")
            scale = max(1, (self.size // 3) // max(note.size))
            note = note.resize(
                (note.width * scale, note.height * scale), Image.NEAREST
            )
            canvas = Image.new("RGB", (self.size, self.size), (18, 18, 18))
            canvas.paste(
                scale_colour(self.default_accent, 0.6),
                ((self.size - note.width) // 2, (self.size - note.height) // 2),
                note,
            )
            self._placeholder = canvas
        return self._placeholder

    def _extract_accent(self, image):
        thumbnail = image.resize((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.BILINEAR)
        palette = thumbnail.quantize(colors=PALETTE_SIZE, method=Image.MEDIANCUT)
        colours = palette.convert("RGB").getcolors(THUMBNAIL_SIZE**2) or []

        best = None
        best_score = -1.0
        for count, colour in colours:
            r, g, b = colour
            saturation = max(r, g, b) - min(r, g, b)
            score = saturation * (count**0.5)
            if score > best_score:
                best_score = score
                best = colour

        if best is None:
            return self.default_accent

        return self._brighten(best)

    def _brighten(self, colour):
        r, g, b = colour
        luma = (r * 299 + g * 587 + b * 114) / 1000
        if luma >= MIN_ACCENT_LUMA:
            return colour
        if luma < 8:
            return self.default_accent
        return scale_colour(colour, MIN_ACCENT_LUMA / luma)
