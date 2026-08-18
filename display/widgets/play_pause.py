from core.types import PlaybackState


class WidgetPlayPause:
    def __init__(self, width=7, height=11, color="white"):
        self.width = width
        self.height = height
        self.color = color

    def draw(self, draw, x, y, state=PlaybackState.STOPPED):
        if state == PlaybackState.PLAYING:
            for i in range(self.height):
                line_length = min(i + 1, self.height - i)
                if line_length > 0:
                    draw.line(
                        [(x, y + i), (x + line_length - 1, y + i)],
                        fill=self.color,
                        width=1,
                    )
        elif state == PlaybackState.PAUSED:
            bar_width = max(2, self.width // 3)
            gap = self.width - (2 * bar_width)
            draw.rectangle(
                [(x, y), (x + bar_width - 1, y + self.height - 1)],
                fill=self.color,
            )
            draw.rectangle(
                [
                    (x + bar_width + gap, y),
                    (x + self.width - 1, y + self.height - 1),
                ],
                fill=self.color,
            )
        else:
            square_size = self.height - 4
            draw.rectangle(
                [(x, y + 2), (x + square_size, y + 2 + square_size)],
                fill=self.color,
            )
