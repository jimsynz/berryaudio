import math

from display.utils import scale_colour


class WidgetLoader:
    def __init__(self, display_width=128, display_height=64, color=None,
                 spinner_radius=8, dot_radius=2):
        self.display_width = display_width
        self.display_height = display_height
        self.color = color
        self._loading_frame = 0
        self.spinner_radius = spinner_radius
        self.dot_radius = dot_radius
        self.dot_count = 8
        self.animation_speed = 5

    def _draw_loading_spinner(self, draw, frame):
        """Draw animated loading spinner"""
        center_x = self.display_width // 2
        center_y = self.display_height // 2

        for i in range(self.dot_count):
            angle = (i * 45) - (frame * 45)
            x = center_x + int(self.spinner_radius * math.cos(math.radians(angle)))
            y = center_y + int(self.spinner_radius * math.sin(math.radians(angle)))

            brightness = max(50, 255 - (i * 30))
            if self.color is None:
                fill = brightness
            else:
                fill = scale_colour(self.color, brightness / 255)

            draw.ellipse(
                [
                    x - self.dot_radius,
                    y - self.dot_radius,
                    x + self.dot_radius,
                    y + self.dot_radius,
                ],
                fill=fill,
            )

    def draw(self, draw):
        frame = (self._loading_frame // self.animation_speed) % self.dot_count
        self._draw_loading_spinner(draw, frame)
        self._loading_frame += 1
