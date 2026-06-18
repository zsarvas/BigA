import time
import board
import neopixel

# Configuration
LED_PIN = board.D19      # GPIO pin 19 (replaces Arduino pin 6)
LED_COUNT = 30
BRIGHTNESS = 10 / 255   # Arduino setBrightness(10) out of 255
RACER_LENGTH = 3
WAIT = 0.030             # 30ms delay

# Initialize NeoPixel strip
strip = neopixel.NeoPixel(
    LED_PIN,
    LED_COUNT,
    brightness=BRIGHTNESS,
    auto_write=False,
    pixel_order=neopixel.GRB
)

# Colors (R, G, B)
RED   = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE  = (0, 0, 255)
WHITE = (255, 255, 255)
OFF   = (0, 0, 0)


def racer_chase(color1, color2):
    """A 'racer' of color1 pixels sweeps across a color2 background."""
    for i in range(RACER_LENGTH, LED_COUNT):
        strip[i] = color1
        if i > RACER_LENGTH:
            strip[i - (RACER_LENGTH + 1)] = color2
        strip.show()
        time.sleep(WAIT)

    for i in range(LED_COUNT - RACER_LENGTH - 1, LED_COUNT):
        strip[i] = color2
        strip[i - LED_COUNT + RACER_LENGTH + 1] = color1
        strip.show()
        time.sleep(WAIT)


def theater_chase(color, wait):
    """Every third pixel lights up, cycling through offsets 0-2, repeated 10 times."""
    for _ in range(10):
        for b in range(3):
            strip.fill(OFF)
            for c in range(b, LED_COUNT, 3):
                strip[c] = color
            strip.show()
            time.sleep(wait / 1000)  # wait is in ms, convert to seconds


def main():
    strip.fill(OFF)
    strip.show()

    try:
        while True:
            strip.fill(RED)
            strip.show()

            racer_chase(WHITE, RED)
            racer_chase(WHITE, RED)
            racer_chase(WHITE, RED)
            theater_chase(WHITE, 50)

            racer_chase(RED, WHITE)
            racer_chase(RED, WHITE)
            racer_chase(RED, WHITE)
            theater_chase(RED, 50)

    except KeyboardInterrupt:
        strip.fill(OFF)
        strip.show()


if __name__ == "__main__":
    main()