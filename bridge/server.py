#!/usr/bin/env python3
"""G-Force: Bank & Fire bridge: Free Wili tilt + LaunchPad trigger -> browser game.

Serves web/ over HTTP and streams JSON events to the page over Server-Sent Events:
  {"t":"tilt","x":..,"y":..,"z":..}      accelerometer, milli-g (z is about 1000 when the Free Wili lies flat)
  {"t":"fire","src":"launchpad|wili"}    trigger pulled
  {"t":"btn","name":"gray|..."}          Free Wili button pressed
  {"t":"status","wili":bool,"launchpad":str}
The page can POST /api/cmd {"cmd":"hit|hurt|off"}: flash the LaunchPad LED and the Free Wili LEDs.
It can POST /api/hud {"state","hull","slot"}: LED animation on the Free Wili (slot 0-6 = bank angle) and its one-word screen.
"""
import argparse
import json
import queue
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUTTONS = ("gray", "yellow", "green", "blue", "red")
LAUNCHPAD_VID = 0x1CBE  # Luminary Micro / TI ICDI virtual COM port
LED_CMDS = {"hit": b"g", "hurt": b"r", "off": b"o"}
GREEN, AMBER, RED, BLUE = (0, 90, 0), (110, 60, 0), (120, 0, 0), (0, 0, 110)


def log(msg: str) -> None:
    print(f"[bank-and-fire] {msg}", file=sys.stderr, flush=True)


class Hub:
    def __init__(self) -> None:
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.status = {"t": "status", "wili": False, "launchpad": "off"}
        self.serial = None
        self.hud: dict | None = None                    # latest game state for the Free Wili screen, newest wins
        self.fx: queue.Queue = queue.Queue(maxsize=8)   # hit / hurt flashes for the Free Wili LEDs

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self.lock:
            self.subs.append(q)
        q.put(self.status)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def publish(self, msg: dict) -> None:
        with self.lock:
            for q in self.subs:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass  # slow client: drop, tilt is high rate and self-healing

    def set_status(self, **kw) -> None:
        if all(self.status.get(k) == v for k, v in kw.items()):
            return
        self.status = {**self.status, **kw}
        self.publish(self.status)

    def led(self, cmd: str) -> None:
        port, byte = self.serial, LED_CMDS.get(cmd)
        if port and byte:
            try:
                port.write(byte)
            except Exception as e:  # noqa: BLE001
                log(f"launchpad write failed: {e}")
        if cmd in ("hit", "hurt"):
            try:
                self.fx.put_nowait(cmd)
            except queue.Full:
                pass


def call_with_timeout(fn, seconds: float):
    box: dict = {}

    def run() -> None:
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"timed out after {seconds:g}s")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def dim(colour: tuple, f: float) -> tuple:
    return tuple(int(v * f) for v in colour)


class WiliScreen:
    """The Free Wili as the cockpit display, built from what the stock OG firmware can do:
    one short word of text and the 7 RGB LEDs (no pictures, no score: the screen is small).

      screen  "G-FORCE", or "DOWN" when you crash. Nothing else: no score, no animated text
      title   a blue light bounces along the LEDs
      flying  a lit LED follows the bank angle; its colour is the hull
      down    a red light sweeps the LEDs
      hits    all LEDs flash white (you hit) or red (you were hit)

    Only ever called from the thread that owns the device. Updates are rate limited and only changed
    LEDs are sent: the board is also streaming tilt over the same link."""

    TEXT_GAP, LED_GAP, LED_REFRESH = 0.5, 0.05, 2.5
    FX = {"hit": (0.12, (110, 110, 110)), "hurt": (0.3, (140, 0, 0))}

    def __init__(self, dev, hub: Hub) -> None:
        import onewili

        self.dev, self.hub, self.onewili = dev, hub, onewili
        self.mode = onewili.enums.owLEDManagerLEDMode.SIMPLEVALUE
        self.shown_text = None
        self.leds: list = [None] * 7
        self.text_t = self.led_t = self.refresh_t = 0.0
        self.fx_until, self.fx_colour = 0.0, (0, 0, 0)
        self.failed: set[str] = set()

    @staticmethod
    def text(h: dict) -> str:
        """Static on purpose: the board hums while its text is being updated often, so the screen
        only changes when the game starts or ends and all the animation is on the LEDs."""
        return "DOWN" if h.get("state") == "over" else "G-FORCE"

    @staticmethod
    def led_targets(h: dict, now: float) -> list:
        state = h.get("state", "ready")

        def glow(pos: int, colour: tuple) -> list:
            return [colour if i == pos else dim(colour, 0.3) if abs(i - pos) == 1 else (0, 0, 0) for i in range(7)]

        if state == "over":
            pos = int(now * 14) % 7
            return [RED if i == pos else (30, 0, 0) for i in range(7)]
        if state == "ready":
            step = int(now * 8) % 12
            return glow(step if step <= 6 else 12 - step, BLUE)
        hull = max(0, min(5, int(h.get("hull", 5))))
        colour = GREEN if hull >= 4 else AMBER if hull == 3 else RED
        return glow(max(0, min(6, int(h.get("slot", 3)))), colour)

    def _call(self, fn, *args) -> None:
        try:
            res = fn(*args)
        except Exception as e:  # noqa: BLE001
            res = e
        if isinstance(res, (self.onewili.Err, Exception)) and fn.__name__ not in self.failed:
            self.failed.add(fn.__name__)
            log(f"Free Wili {fn.__name__}: {res}")

    def service(self) -> None:
        now, hud = time.time(), self.hub.hud or {}
        while True:
            try:
                kind = self.hub.fx.get_nowait()
            except queue.Empty:
                break
            length, colour = self.FX[kind]
            self.fx_until, self.fx_colour = now + length, colour
        if now - self.text_t >= self.TEXT_GAP:
            text = self.text(hud)
            if text != self.shown_text:
                self.shown_text, self.text_t = text, now
                self._call(self.dev.gui.show_text, text)
        if now - self.led_t >= self.LED_GAP:
            self.led_t = now
            target = [self.fx_colour] * 7 if now < self.fx_until else self.led_targets(hud, now)
            stale = now - self.refresh_t >= self.LED_REFRESH
            if stale:
                self.refresh_t = now
            for i, colour in enumerate(target):
                if colour != self.leds[i] or stale:
                    self.leds[i] = colour
                    self._call(self.dev.gui.set_led_color, i, *colour, 3000, self.mode)

    def off(self) -> None:
        for i in range(7):
            self._call(self.dev.gui.set_led_color, i, 0, 0, 0, 0, self.mode)


def wili_loop(hub: Hub, stop: threading.Event) -> None:
    import onewili

    while not stop.is_set():
        dev = screen = None
        try:
            dev = call_with_timeout(onewili.connect, 12)
            dev.io.sensors.enable_motion_stream(10)
            dev.gui.stream_io(20)
            hub.set_status(wili=True)
            log("Free Wili connected")
            screen = WiliScreen(dev, hub)
            screen._call(dev.gui.show_text, screen.text({}))
            events, last, held = dev._transport.events, time.time(), [0] * 5
            while not stop.is_set():
                screen.service()
                try:
                    f = events.get(timeout=0.05)
                except queue.Empty:
                    if time.time() - last > 5:
                        raise RuntimeError("no frames for 5s")
                    continue
                last = time.time()
                path, parts = getattr(f, "path", ""), f.response.split()
                if path == "*motion" and len(parts) >= 3:
                    x, y, z = (float(p) for p in parts[:3])
                    hub.publish({"t": "tilt", "x": x, "y": y, "z": z})
                elif path == "*button" and len(parts) >= 5:
                    now = [int(p) for p in parts[:5]]
                    for i, name in enumerate(BUTTONS):
                        if now[i] and not held[i]:
                            hub.publish({"t": "btn", "name": name})
                    held = now
        except Exception as e:  # noqa: BLE001
            log(f"Free Wili: {e}")
            hub.set_status(wili=False)
            stop.wait(2)
        finally:
            if dev is not None:
                for fn in (lambda: screen and screen.off(), lambda: dev.gui.stream_io(0),
                           lambda: dev.io.sensors.enable_motion_stream(0), dev.close):
                    try:
                        call_with_timeout(fn, 2)
                    except Exception:  # noqa: BLE001
                        pass


def find_launchpad_port() -> str | None:
    from serial.tools import list_ports

    for p in list_ports.comports():
        if p.vid == LAUNCHPAD_VID:
            return p.device
    return None


def launchpad_loop(hub: Hub, stop: threading.Event) -> None:
    import serial

    while not stop.is_set():
        path = find_launchpad_port()
        if not path:
            hub.set_status(launchpad="missing")
            stop.wait(1.5)
            continue
        try:
            port = serial.Serial(path, 115200, timeout=0.5)
        except Exception as e:  # noqa: BLE001
            denied = "Permission denied" in str(e)
            state = "permission" if denied else "missing"
            if hub.status["launchpad"] != state:
                log(f"no permission for {path}: run `sudo usermod -aG dialout $USER`, then log out and in"
                    if denied else f"launchpad open failed: {e}")
            hub.set_status(launchpad=state)
            stop.wait(3)
            continue
        log(f"LaunchPad on {path}")
        hub.serial = port
        hub.set_status(launchpad="ok")
        try:
            while not stop.is_set():
                line = port.readline().strip()
                if line == b"FIRE":
                    hub.publish({"t": "fire", "src": "launchpad"})
        except Exception as e:  # noqa: BLE001
            log(f"LaunchPad lost: {e}")
        finally:
            hub.serial = None
            hub.set_status(launchpad="missing")
            try:
                port.close()
            except Exception:  # noqa: BLE001
                pass


def make_handler(hub: Hub):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(ROOT / "web"), **kw)

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/events":
                return super().do_GET()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = hub.subscribe()
            try:
                while True:
                    try:
                        msg = q.get(timeout=10)
                        self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except OSError:
                pass  # the browser went away
            finally:
                hub.unsubscribe(q)

        def do_POST(self):
            if self.path not in ("/api/cmd", "/api/hud"):
                self.send_error(404)
                return
            try:
                size = min(int(self.headers.get("Content-Length", 0)), 4096)
                body = json.loads(self.rfile.read(size) or b"{}")
                if self.path == "/api/hud":
                    hub.hud = {"state": str(body["state"]), "hull": int(body["hull"]), "slot": int(body["slot"])}
                else:
                    hub.led(str(body.get("cmd", "")))
            except Exception:  # noqa: BLE001
                pass
            self.send_response(204)
            self.end_headers()

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-wili", action="store_true", help="skip the Free Wili (keyboard steering)")
    ap.add_argument("--no-launchpad", action="store_true", help="skip the LaunchPad (space bar fires)")
    args = ap.parse_args()

    hub, stop = Hub(), threading.Event()
    if not args.no_wili:
        threading.Thread(target=wili_loop, args=(hub, stop), daemon=True).start()
    if not args.no_launchpad:
        threading.Thread(target=launchpad_loop, args=(hub, stop), daemon=True).start()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(hub))
    srv.daemon_threads = True
    log(f"open http://localhost:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
