from PIL import ImageFont, Image
from core.models import RefType
from pathlib import Path

FONT_STYLE_1 = Path(__file__).parent.parent / "fonts" / "3x5pexel.ttf"

ICON_MUSIC_NOTE = Path(__file__).parent.parent / "icons" / "music_note.png"
ICON_DIRECTORY = Path(__file__).parent.parent / "icons" / "directory.png"
ICON_BULLET = Path(__file__).parent.parent / "icons" / "bullet.png"
ICON_STORAGE = Path(__file__).parent.parent / "icons" / "storage.png"
ICON_BLUETOOTH = Path(__file__).parent.parent / "icons" / "bluetooth.png"


class WidgetListScrollable:
    def __init__(
        self,
        display_width=128,
        display_height=64,
        show_counter=True,
        font_path=None,
        font_size=8,
        line_height=16,
        max_chars=35,
        icon_scale=1,
        text_color="white",
        selected_text_color="black",
        selection_color="white",
        icon_color="white",
        icon_dim_color=240,
        counter_font_size=5,
    ):
        self.items = []
        self.selected_index = 0
        self.scroll_offset = 0
        self.width = display_width
        self.height = display_height
        self.show_counter = show_counter
        self.max_chars = max_chars
        self.icon_scale = icon_scale
        self.text_color = text_color
        self.selected_text_color = selected_text_color
        self.selection_color = selection_color
        self.icon_color = icon_color
        self.icon_dim_color = icon_dim_color
        self.counter_font_size = counter_font_size

        # Load font
        if font_path:
            try:
                self.font = ImageFont.truetype(
                    font_path, font_size, layout_engine=ImageFont.Layout.BASIC
                )
            except:
                self.font = ImageFont.load_default()
        else:
            self.font = ImageFont.load_default()

        # Calculate layout
        self.line_height = line_height
        self.visible_items = self.height // self.line_height
        self.padding_left = 4
        self.scrollbar_width = 4
        self.scrollbar_padding = 2
        self.icon_offset = (line_height - (8 * icon_scale)) // 2
        self.icon_x = 2 * icon_scale
        self.bullet_x = 4 * icon_scale
        self.text_indent = self.padding_left + (12 * icon_scale)
        self.folder_icon = self._load_icon(ICON_DIRECTORY)
        self.track_icon = self._load_icon(ICON_MUSIC_NOTE)
        self.bullet_icon = self._load_icon(ICON_BULLET)
        self.storage_icon = self._load_icon(ICON_STORAGE)
        self.bluetooth_icon = self._load_icon(ICON_BLUETOOTH)

    def set_items(self, items, selected_index=0, scroll_offset=0):
        self.items = items
        self.selected_index = selected_index
        self.scroll_offset = scroll_offset

    def draw(self, draw):
        items = self.items

        if items is None:
            return

        start_idx = self.scroll_offset
        end_idx = min(start_idx + self.visible_items, len(items))
        content_width = self.width - self.scrollbar_width - self.scrollbar_padding
        for i in range(start_idx, end_idx):
            y_pos = (i - start_idx) * self.line_height
            display_name = items[i].name
            display_type = items[i].uri
            display_active = getattr(items[i], "active", False) or getattr(
                items[i], "connected", False
            )

            if len(display_name) > self.max_chars:
                display_name = display_name[: self.max_chars - 3] + "..."

            # Selected
            if i == self.selected_index:
                draw.rectangle(
                    [(0, y_pos), (content_width, y_pos + self.line_height)],
                    fill=self.selection_color,
                    outline=self.selection_color,
                )

                if display_type == RefType.TRACK:
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.track_icon,
                        fill=self.selected_text_color,
                    )
                elif (
                    display_type == RefType.DIRECTORY
                    or display_type == RefType.ALBUM
                    or display_type == RefType.ARTIST
                ):
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.folder_icon,
                        fill=self.selected_text_color,
                    )
                elif (
                    display_type == RefType.STORAGE
                    or display_type == RefType.NAS
                    or display_type == RefType.REMOVABLE
                ):
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.storage_icon,
                        fill=self.selected_text_color,
                    )
                elif display_type == RefType.BLUETOOTH:
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.bluetooth_icon,
                        fill=self.selected_text_color,
                    )
                else:
                    draw.bitmap(
                        (self.bullet_x, y_pos + self.icon_offset),
                        self.bullet_icon,
                        fill=self.selected_text_color,
                    )

                draw.text(
                    (self.text_indent, y_pos + 1),
                    f"{display_name}",
                    font=self.font,
                    fill=self.selected_text_color,
                )

            else:
                # Draw normal text
                if display_type == RefType.TRACK:
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.track_icon,
                        fill=self.icon_dim_color,
                    )
                elif (
                    display_type == RefType.DIRECTORY
                    or display_type == RefType.ALBUM
                    or display_type == RefType.ARTIST
                ):
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.folder_icon,
                        fill=self.icon_dim_color,
                    )
                elif (
                    display_type == RefType.STORAGE
                    or display_type == RefType.NAS
                    or display_type == RefType.REMOVABLE
                ):
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.storage_icon,
                        fill=self.icon_dim_color,
                    )

                elif display_type == RefType.BLUETOOTH:
                    draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.bluetooth_icon,
                        fill=self.icon_dim_color,
                    )
                else:
                    pass

                if display_active:
                    if display_type == RefType.BLUETOOTH:
                        draw.bitmap(
                        (self.icon_x, y_pos + self.icon_offset),
                        self.bluetooth_icon,
                        fill=self.icon_color,
                    )
                    else:
                        draw.bitmap(
                        (self.bullet_x, y_pos + self.icon_offset),
                        self.bullet_icon,
                        fill=self.icon_color,
                    )

                draw.text(
                    (self.text_indent, y_pos + 1),
                    display_name,
                    font=self.font,
                    fill=self.text_color,
                )

        # Draw scrollbar if needed
        if len(self.items) > self.visible_items:
            scrollbar_x = self.width - self.scrollbar_width

            # Draw scrollbar track
            draw.rectangle(
                [(scrollbar_x, 0), (self.width - 1, self.height - 1)],
                outline=self.icon_dim_color,
            )

            # Calculate scrollbar thumb size and position
            thumb_height = max(
                8,  # Minimum thumb height
                int((self.visible_items / len(self.items)) * self.height),
            )

            # Calculate thumb position
            scroll_range = self.height - thumb_height
            if len(self.items) > self.visible_items:
                thumb_pos = int(
                    (self.scroll_offset / (len(self.items) - self.visible_items))
                    * scroll_range
                )
            else:
                thumb_pos = 0

            # Draw scrollbar thumb
            draw.rectangle(
                [
                    (scrollbar_x + 1, thumb_pos),
                    (self.width - 2, thumb_pos + thumb_height),
                ],
                fill=self.selection_color,
            )

        if self.show_counter:
            counter_text = f"{self.selected_index + 1}/{len(self.items)}"
            font = ImageFont.truetype(
                FONT_STYLE_1,
                self.counter_font_size,
                layout_engine=ImageFont.Layout.BASIC,
            )

            bbox = font.getbbox(counter_text)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            rect_x1 = content_width - 40
            rect_y1 = self.height - 12
            rect_x2 = content_width
            rect_y2 = self.height

            text_x = rect_x1 + (40 - text_w) // 2
            text_y = rect_y1 + (12 - text_h) // 2

            draw.rectangle(
                [(rect_x1, rect_y1), (rect_x2, rect_y2)],
                fill="black",
                outline=self.text_color,
            )
            draw.text((text_x, text_y), counter_text, font=font, fill=self.text_color)

    def scroll_down(self):
        if self.items is None:
            return

        if self.selected_index < len(self.items) - 1:
            self.selected_index += 1

            # Adjust scroll offset if needed
            if self.selected_index >= self.scroll_offset + self.visible_items:
                self.scroll_offset = self.selected_index - self.visible_items + 1

            return True
        return False

    def scroll_up(self):
        if self.items is None:
            return

        if self.selected_index > 0:
            self.selected_index -= 1

            # Adjust scroll offset if needed
            if self.selected_index < self.scroll_offset:
                self.scroll_offset = self.selected_index

            return True
        return False

    def page_up(self):
        if self.items is None:
            return

        """Jump up by one page"""
        if self.selected_index > 0:
            self.selected_index = max(0, self.selected_index - self.visible_items)
            self.scroll_offset = max(0, self.scroll_offset - self.visible_items)
            return True
        return False

    def page_down(self):
        if self.items is None:
            return

        """Jump down by one page"""
        if self.selected_index < len(self.items) - 1:
            self.selected_index = min(
                len(self.items) - 1, self.selected_index + self.visible_items
            )
            if self.selected_index >= self.scroll_offset + self.visible_items:
                self.scroll_offset = min(
                    len(self.items) - self.visible_items,
                    self.scroll_offset + self.visible_items,
                )
            return True
        return False

    def get_selected_item(self):
        """Returns tuple: (item, selected_index, scroll_offset)"""
        if (
            not self.items
            or self.selected_index >= len(self.items)
            or self.selected_index < 0
        ):
            return None
        return self.items[self.selected_index], self.selected_index, self.scroll_offset

    def get_selected_index(self):
        return self.selected_index

    def _load_icon(self, path):
        icon = Image.open(path)
        if self.icon_scale == 1:
            return icon
        return icon.resize(
            (icon.width * self.icon_scale, icon.height * self.icon_scale),
            Image.NEAREST,
        )
