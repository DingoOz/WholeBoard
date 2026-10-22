# Build and flash the firmware

When the board has an ESP32 (any variant), RP2040/RP2350, STM32, ATmega328P/32U4 or ATtiny85, the outputs include
a PlatformIO project (Arduino framework).

- `include/pins.h` names every GPIO after its net, using the pin names in the KiCad symbol:
  `#define PIN_LED_STATUS 4`.
- The starter `main.cpp` blinks the LEDs, reads buttons and analog inputs, and scans the I²C bus.

## Compile

Press *Compile with PlatformIO* on the firmware card. It offers to install PlatformIO the first time; or install it
with `python -m wholeboard.doctor --install platformio`. From a terminal: `pio run` in the firmware folder.

## Flash

- **ESP32** boards can be flashed from the browser over Web Serial (Chrome or Edge, on `localhost` or HTTPS).
- **RP2040** boards get a `.uf2` file to drop on the BOOTSEL drive.
- Anything else: `pio run -t upload` with your programmer.
