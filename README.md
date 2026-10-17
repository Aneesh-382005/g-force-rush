# G-Force: Bank & Fire

Fly a plane by tilting a **Free Wili**, shoot by pressing a button on a **TI Tiva C LaunchPad** (EK-TM4C123GXL). Strap the two together and they act as one flight stick.

```
Free Wili  --USB-->  bridge/server.py  --HTTP + SSE-->  browser game (Three.js)
LaunchPad  --USB-->        ^
 (SW1 = trigger)           '-- POST /api/cmd, /api/hud: LEDs on both boards
```

## Run

```bash
uv venv .venv && uv pip install --python .venv/bin/python "git+https://github.com/freewili/onewili#subdirectory=python" pyserial
./run.sh                 # then open http://localhost:8765
./run.sh --no-wili       # keyboard steering (arrow keys), no Free Wili
./run.sh --no-launchpad  # space bar fires
```

Press the trigger (LaunchPad SW1, Free Wili green button, or space) to start. Stop the bridge with Ctrl+C before running anything else against the Free Wili: it has one host connection at a time.

## Controls

| Input | Action |
|---|---|
| Tilt Free Wili | roll steers left/right, pitch climbs/dives (nose down = descend) |
| LaunchPad SW1 / Free Wili green / space | fire |
| Free Wili gray / `C` | zero the tilt (do this with the stack held level) |
| `X` `R` `P` | swap axes / flip roll / flip pitch, if a direction feels backwards |
| `N` | skip to the next time of day |
| `M` or the music button | music on / off |

The game speeds up as your score climbs.

## Look and sound

- Day and night: a smooth 150-second cycle. Blue day, orange dusk with the sun setting, a night sky with stars and a moon, then dawn. Navigation lights on the plane, a strobe on the tail.
- Music: put your own audio file at `web/music.mp3` and the game loops it. The file is not included. Browsers only start audio after a click or key press (or sometimes on load), so the music button says "click to start music" until then.
- Free Wili: its stock OG firmware shows text and LEDs, not pictures or animation. The screen says "G-FORCE" (or "DOWN"), and the 7 LEDs do the animation: a bouncing light on the title, a light that follows your bank angle (colored by hull), flashes on hits, a red sweep at game over. The screen text is deliberately static, because the board hums when its text updates often.

## Hardware notes

- The Free Wili is read through **OneWili** (`dev.io.sensors.enable_motion_stream`, `dev.gui.stream_io`). The older `freewili` pip package does not work on OG firmware. Accelerometer frames are `x y z` in milli-g, z about 1000 when flat.
- On OG firmware, custom images, GUI panels and screenshots are unsupported. A pixel-level screen would need a custom display app built with [wiliOGbsp](https://github.com/freewili/wiliOGbsp).
- LaunchPad firmware is bare metal C in `firmware/` (no TivaWare). SW1 (left button, bottom edge) sends `FIRE\n` on UART0 (115200, the ICDI virtual COM port). Host bytes `g` / `r` / `o` flash the LED green / red / back to blue.

```bash
sudo apt install gcc-arm-none-eabi openocd
cd firmware && make && make flash
```
