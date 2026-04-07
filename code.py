import board
import busio
import digitalio
import time
import neopixel
from adafruit_mcp230xx.mcp23s17 import MCP23S17
from DispDriver import LCD12864

# ─── Display constants ──────────────────────────────────────────────────────
CHAR_W    = 8        # pixels per character (font is 8px wide)
MAX_PX    = 120      # maximum content width in pixels
MAX_CHARS = MAX_PX // CHAR_W  # 15 characters

def trunc(s):
    """Clip string to MAX_CHARS so it stays within MAX_PX pixels."""
    return s[:MAX_CHARS] if len(s) > MAX_CHARS else s

# ─── Zone data ──────────────────────────────────────────────────────────────
# Physical button index -> logical zone index
translation = [
    8, 7, 6, 2, 1, 0, 29, 5, 5, 4, 11, 10, 17, 16, 9, 3, 25, 26, 18, 19, 20, 13, 14, 15,
    21, 22, 23, 27, 28, 3, 38, 37, 36, 32, 31, 30, 59, 35, 35, 34, 41, 40, 47, 46, 39,
    33, 55, 56, 48, 49, 50, 43, 44, 45, 51, 52, 53, 57, 58, 33
]

# Zone names (truncated at display time via trunc() to 15 chars / 120px).
# Shorten any entries here if you want the full name visible on screen.
room_csv = [
    "FIRST FL GARAGE DETECTOR", "FIRST FL KITCHEN DETECTOR", "FIRST FL HALLWAY DETECTOR",
    "FIRST FL LIVING DETECTOR", "FIRST FL STAIRWELL DETECTOR", "FIRST FL ENTRY DETECTOR",
    "FIRST FL GARAGE FLOW", "FIRST FL KITCHEN FLOW", "FIRST FL HALLWAY FLOW",
    "FIRST FL LIVING FLOW", "FIRST FL STAIRWELL FLOW", "FIRST FL ENTRY FLOW",
    "FIRST FL STAIRWELL DETECTOR", "", "", "", "", "", "STANDPIPE FLOW", "", "", "", "", "",
    "WATERMAIN TAMPER", "BACKFLOW TAMPER", "SPRINKLER TAMPER", "DRY SPRINKLER LOW AIR",
    "DRY SYSTEM CHARGED", "", "SECOND FL DEN DETECTOR", "SECOND FL BEDROOM DETECTOR",
    "SECOND FL HALLWAY DETECTOR", "SECOND FL STAIRWELL DETECTOR", "", "",
    "SECOND FL DEN FLOW", "SECOND FL BEDROOM FLOW", "SECOND FL HALLWAY FLOW",
    "SECOND FL STAIRWELL FLOW", "", "", "ATTIC FLOW", "", "", "", "", "",
    "THIRD FL HALLWAY DETECTOR", "THIRD FL STAIRWELL DETECTOR", "", "", "", "",
    "THIRD FL HALLWAY FLOW", "THIRD FL STAIRWELL FLOW", "THIRD FL BALCONY FLOW", "", "", ""
]

STATE_NAMES = {0: "NORMAL", 1: "ALARM", 2: "TROUBLE"}

# Names used in debug prints
CTRL_NAMES = {8: "LEFT", 9: "RIGHT", 10: "ENTER", 11: "SILENCE", 12: "DOWN", 13: "UP", 15: "EYE"}

# ─── Global state ───────────────────────────────────────────────────────────
button_states   = [0] * 60  # 0=normal, 1=alarm, 2=trouble
training_mode   = False
buzzer_silenced = False
alarms_cleared  = True

# ─── Hardware: SPI + MCPs ───────────────────────────────────────────────────
spi = busio.SPI(board.GP18, MOSI=board.GP19, MISO=board.GP16)

def setup_mcp(cs_pin, int_pin, addr, start_pin, count, interrupt_enable):
    cs  = digitalio.DigitalInOut(cs_pin)
    mcp = MCP23S17(spi, cs, address=addr)
    int_p = digitalio.DigitalInOut(int_pin)
    int_p.direction = digitalio.Direction.INPUT
    for i in range(start_pin, start_pin + count):
        pin = mcp.get_pin(i)
        pin.direction = digitalio.Direction.INPUT
        pin.pull = digitalio.Pull.UP
    mcp.interrupt_enable = interrupt_enable
    mcp.interrupt_configuration = 0x0000
    mcp.default_value = 0xFFFF
    mcp.io_control = 0x40
    mcp.clear_ints()
    return mcp, int_p

mcp_configs = [
    (board.GP17, board.GP15, 0x00, 16, 0xFFFF),  # MCP 1
    (board.GP28, board.GP14, 0x01, 14, 0x3FFF),  # MCP 2
    (board.GP7,  board.GP9,  0x00, 16, 0xFFFF),  # MCP 3
    (board.GP12, board.GP8,  0x01, 14, 0x3FFF),  # MCP 4
]

button_mcps = []
offset = 0
for cs_pin, int_pin, addr, count, inten in mcp_configs:
    mcp, ipin = setup_mcp(cs_pin, int_pin, addr, 0, count, inten)
    button_mcps.append({
        "mcp": mcp, "int": ipin, "count": count, "offset": offset,
        "pressed": [False] * count, "last_edge": [0.0] * count,
    })
    offset += count

mcp_control, int_pin_control = setup_mcp(board.GP20, board.GP21, 0x00, 8, 8, 0xFF00)

print("MCPs initialised")

# ─── NeoPixels ──────────────────────────────────────────────────────────────
main_pixels    = neopixel.NeoPixel(board.GP2,  12, brightness=.1, auto_write=False, pixel_order=neopixel.GRB)
alarm_pixels   = neopixel.NeoPixel(board.GP0,  60, brightness=.1, auto_write=False, pixel_order=neopixel.GRB)
trouble_pixels = neopixel.NeoPixel(board.GP1,  60, brightness=.1, auto_write=False, pixel_order=neopixel.GRB)

def _test_pixels(pixels, color, count):
    for i in range(count):
        pixels[i] = color
    pixels.show()
    time.sleep(1)
    pixels.fill((0, 0, 0))

print("Testing NeoPixels...")
_test_pixels(main_pixels,    (0, 0, 255), 12)
_test_pixels(alarm_pixels,   (255, 0, 0), 60)
_test_pixels(trouble_pixels, (255, 165, 0), 60)
main_pixels.show(); alarm_pixels.show(); trouble_pixels.show()
print("NeoPixel test complete!")

def update_leds():
    for i in range(60):
        target_idx = 29 - i if i < 30 else 89 - i
        state = button_states[i]
        alarm_pixels[target_idx]   = (255, 0, 0)    if state == 1 else (0, 0, 0)
        trouble_pixels[target_idx] = (255, 165, 0)  if state == 2 else (0, 0, 0)
    alarm_pixels.show()
    trouble_pixels.show()

# ─── Buzzer ─────────────────────────────────────────────────────────────────
buzzer = digitalio.DigitalInOut(board.GP26)
buzzer.direction = digitalio.Direction.OUTPUT
buzzer.value = False
last_buzzer_toggle = 0

def pulse_buzzer(current_time):
    global last_buzzer_toggle, buzzer_silenced, alarms_cleared
    if training_mode:
        buzzer.value = False
        return
    any_alarm = any(s != 0 for s in button_states)
    if not any_alarm:
        alarms_cleared = True
        buzzer.value = False
    else:
        if alarms_cleared:
            buzzer_silenced = False
            alarms_cleared = False
        if not buzzer_silenced and (current_time - last_buzzer_toggle) >= 0.5:
            buzzer.value = not buzzer.value
            last_buzzer_toggle = current_time
        elif buzzer_silenced:
            buzzer.value = False

# ─── Display ────────────────────────────────────────────────────────────────
display = LCD12864(cs_pin=board.GP13)
display.fill(0)
display.show()

def room_label(idx):
    """Return truncated zone name, or 'Room N' if the zone has no name."""
    name = room_csv[idx] if idx < len(room_csv) else ""
    return trunc(name) if name else trunc(f"Room {idx + 1}")

# ─── Menu system ─────────────────────────────────────────────────────────────
# Modes:
#   main     – top-level menu (4 items)
#   alarms   – cycle through zones currently in ALARM
#   troubles – cycle through zones currently in TROUBLE
#   buttons  – cycle through all 60 zones
#   clear    – confirm/cancel clear-all
#
# Controls (all short press only):
#   UP      (pin 13) – scroll up
#   DOWN    (pin 12) – scroll down
#   RIGHT   (pin  9) – secondary scroll down
#   ENTER   (pin 10) – select / confirm
#   LEFT    (pin  8) – back to main menu from any sub-menu
#   SILENCE (pin 11) – silence / re-enable buzzer
#   EYE     (pin 15) – toggle training mode

class MenuSystem:
    def __init__(self, disp):
        self.display = disp
        self.mode = "main"
        self.idx  = 0

    # ── Helpers ───────────────────────────────────────────────────────────
    def _filtered(self, state_val):
        return [i for i, s in enumerate(button_states) if s == state_val]

    def max_idx(self):
        if self.mode == "main":     return 3
        if self.mode == "alarms":   return max(0, len(self._filtered(1)) - 1)
        if self.mode == "troubles": return max(0, len(self._filtered(2)) - 1)
        if self.mode == "buttons":  return 59
        if self.mode == "clear":    return 1
        return 0

    # ── Navigation ────────────────────────────────────────────────────────
    def scroll_up(self):
        self.idx = self.idx - 1 if self.idx > 0 else self.max_idx()

    def scroll_down(self):
        m = self.max_idx()
        self.idx = self.idx + 1 if self.idx < m else 0

    def back_to_main(self):
        self.mode = "main"
        self.idx  = 0

    def enter(self):
        """Short-press ENTER: navigate in from main, or confirm in clear."""
        if self.mode == "main":
            self.mode = ["alarms", "troubles", "buttons", "clear"][self.idx]
            self.idx  = 0
        elif self.mode == "clear":
            if self.idx == 0:   # YES
                for i in range(60):
                    button_states[i] = 0
                update_leds()
                self._flash_cleared()
            # YES or NO both return to main
            self.back_to_main()
        # In alarms / troubles / buttons: ENTER is a no-op (view-only modes)

    def _flash_cleared(self):
        self.display.fill(0)
        self.display.text("ALL CLEARED!", 0, 25)
        self.display.show()
        time.sleep(1.5)

    # ── Rendering ─────────────────────────────────────────────────────────
    # All strings are kept to MAX_CHARS (15) or fewer so no line exceeds
    # MAX_PX (120) pixels at CHAR_W (8) pixels per character.
    def draw(self):
        d   = self.display
        idx = self.idx
        d.fill(0)

        if self.mode == "main":
            # "FIRE PANEL" = 10 chars = 80 px
            d.text("FIRE PANEL", 0, 0)
            if training_mode:
                # "TRN" starts at x=96: 96 + 3*8 = 120 px
                d.text("TRN", 96, 0)
            n_al = button_states.count(1)
            n_tr = button_states.count(2)
            # Max item width: "> Troubles: 60" = 14 chars = 112 px
            items = [
                f"Alarms: {n_al}",
                f"Troubles: {n_tr}",
                "All Rooms",
                "Clear All",
            ]
            for i, item in enumerate(items):
                sel  = (i == idx)
                line = trunc(f"> {item}" if sel else f"  {item}")
                d.text(line, 0, 10 + i * 11, color=0 if sel else 1, bg=1 if sel else 0)
            # Status bar: "ENT:Sel EYE:Trn" = 15 chars = 120 px
            d.text("ENT:Sel EYE:Trn", 0, 55)

        elif self.mode in ("alarms", "troubles"):
            state_val = 1 if self.mode == "alarms" else 2
            title     = "ALARMS"   if self.mode == "alarms" else "TROUBLES"
            s_name    = "ALARM"    if self.mode == "alarms" else "TROUBLE"
            lst       = self._filtered(state_val)

            # Title: max "TROUBLES" = 8 chars = 64 px; "TRN" at x=96
            d.text(title, 0, 0)
            if training_mode:
                d.text("TRN", 96, 0)

            if not lst:
                # "No alarms" / "No troubles" <= 11 chars = 88 px
                d.text(f"No {title.lower()}", 0, 24)
            else:
                zone = lst[idx]
                # "Rm 60: TROUBLE" = 14 chars = 112 px
                d.text(trunc(f"Rm {zone + 1}: {s_name}"), 0, 12)
                d.text(room_label(zone), 0, 22)
                # "60 of 60" = 8 chars = 64 px
                d.text(f"{idx + 1} of {len(lst)}", 0, 33)
            # "L:Bk U/D:Scroll" = 15 chars = 120 px
            d.text("L:Bk U/D:Scroll", 0, 55)

        elif self.mode == "buttons":
            d.text("ALL ROOMS", 0, 0)
            if training_mode:
                d.text("TRN", 96, 0)
            state_name = STATE_NAMES[button_states[idx]]
            # "Rm 60: TROUBLE" = 14 chars = 112 px
            d.text(trunc(f"Rm {idx + 1}: {state_name}"), 0, 12)
            d.text(room_label(idx), 0, 22)
            # "60 of 60" = 8 chars = 64 px
            d.text(f"{idx + 1} of 60", 0, 33)
            d.text("L:Bk U/D:Scroll", 0, 55)

        elif self.mode == "clear":
            d.text("CLEAR ALL?", 0, 0)
            # "Alarms: 60" = 10 chars = 80 px
            d.text(f"Alarms: {button_states.count(1)}", 0, 12)
            # "Troubles: 60" = 12 chars = 96 px
            d.text(f"Troubles: {button_states.count(2)}", 0, 22)
            sel_yes = (idx == 0)
            d.text("> YES" if sel_yes     else "  YES", 0, 36,
                   color=0 if sel_yes     else 1, bg=1 if sel_yes     else 0)
            d.text("> NO"  if not sel_yes else "  NO",  0, 46,
                   color=0 if not sel_yes else 1, bg=1 if not sel_yes else 0)
            # "L:Cancel ENT:OK" = 15 chars = 120 px
            d.text("L:Cancel ENT:OK", 0, 55)

        d.show()

menu = MenuSystem(display)
menu.draw()

# ─── Control button startup sync ────────────────────────────────────────────
CTRL_PINS    = [8, 9, 10, 11, 12, 13, 15]
ctrl_pressed = {p: False for p in CTRL_PINS}

startup_time = time.monotonic()

print("Syncing hardware state...")
for _ in range(2):
    for p in CTRL_PINS:
        ctrl_pressed[p] = not mcp_control.get_pin(p).value
    mcp_control.clear_ints()
    for c in button_mcps:
        for i in range(c["count"]):
            c["pressed"][i] = not c["mcp"].get_pin(i).value
        c["mcp"].clear_ints()
    time.sleep(0.05)

# Block spurious edges during power-on by starting debounce timers at now
ctrl_last_edge = {p: startup_time for p in CTRL_PINS}
for c in button_mcps:
    c["last_edge"] = [startup_time] * c["count"]

# ─── Info popup state ───────────────────────────────────────────────────────
show_info  = False
info_drawn = False
show_start = 0.0
show_btn   = -1
show_state = -1

print("System ready!")

# ─── Main loop ──────────────────────────────────────────────────────────────
while True:
    now        = time.monotonic()
    needs_draw = False

    # 1. Control buttons (navigation / training / silence)
    if not int_pin_control.value:
        for p in CTRL_PINS:
            is_low = not mcp_control.get_pin(p).value

            if is_low and not ctrl_pressed[p]:
                if (now - ctrl_last_edge[p]) > 0.2:
                    ctrl_pressed[p]   = True
                    ctrl_last_edge[p] = now

                    name = CTRL_NAMES.get(p, str(p))
                    print(f"CTRL PRESSED  pin={p} ({name})")

                    if p == 13:         # UP
                        menu.scroll_up()
                        show_info  = False
                        needs_draw = True
                    elif p == 12:       # DOWN
                        menu.scroll_down()
                        show_info  = False
                        needs_draw = True
                    elif p == 9:        # RIGHT (secondary scroll down)
                        menu.scroll_down()
                        show_info  = False
                        needs_draw = True
                    elif p == 10:       # ENTER
                        show_info  = False
                        menu.enter()
                        needs_draw = True
                    elif p == 8:        # LEFT → back to main
                        if menu.mode != "main":
                            menu.back_to_main()
                            show_info  = False
                            needs_draw = True
                    elif p == 11:       # SILENCE
                        buzzer_silenced = not buzzer_silenced
                        if buzzer_silenced:
                            buzzer.value = False
                        print(f"  Buzzer {'silenced' if buzzer_silenced else 're-enabled'}")
                    elif p == 15:       # EYE → toggle training mode
                        training_mode = not training_mode
                        print(f"  Training mode {'ON' if training_mode else 'OFF'}")
                        needs_draw = True

            elif not is_low and ctrl_pressed[p]:
                if (now - ctrl_last_edge[p]) > 0.2:
                    ctrl_pressed[p]   = False
                    ctrl_last_edge[p] = now
                    name = CTRL_NAMES.get(p, str(p))
                    print(f"CTRL RELEASED pin={p} ({name})")

        mcp_control.clear_ints()

    if needs_draw and not show_info:
        menu.draw()

    # 2. Zone buttons (room detectors / flows)
    for c in button_mcps:
        if not c["int"].value:
            for i in range(c["count"]):
                is_low = not c["mcp"].get_pin(i).value

                if is_low and not c["pressed"][i]:
                    if (now - c["last_edge"][i]) > 0.05:
                        c["pressed"][i]   = True
                        c["last_edge"][i] = now

                        physical = i + c["offset"]
                        logical  = translation[physical]

                        if training_mode:
                            # Cycle: NORMAL → ALARM → TROUBLE → NORMAL
                            button_states[logical] = (button_states[logical] + 1) % 3

                        state_str = STATE_NAMES[button_states[logical]]
                        print(f"ZONE PRESSED  phys={physical} logical={logical}"
                              f" state={state_str} room={room_csv[logical] or 'unnamed'}")

                        show_info  = True
                        info_drawn = False
                        show_start = now
                        show_btn   = logical
                        show_state = button_states[logical]
                        update_leds()

                elif not is_low and c["pressed"][i]:
                    if (now - c["last_edge"][i]) > 0.05:
                        c["pressed"][i]   = False
                        c["last_edge"][i] = now
                        physical = i + c["offset"]
                        logical  = translation[physical]
                        print(f"ZONE RELEASED phys={physical} logical={logical}")

            c["mcp"].clear_ints()

    # 3. Info popup (shown for 3 s after a zone button press)
    if show_info:
        if now - show_start >= 3.0:
            show_info = False
            menu.draw()
        elif not info_drawn:
            display.fill(0)
            # "Room 60" = 7 chars = 56 px
            display.text(trunc(f"Room {show_btn + 1}"), 0, 0)
            if training_mode:
                display.text("TRN", 96, 0)
            display.text(room_label(show_btn), 0, 12)
            # "State: TROUBLE" = 14 chars = 112 px
            display.text(trunc(f"State: {STATE_NAMES[show_state]}"), 0, 32)
            display.show()
            info_drawn = True

    # 4. Buzzer
    pulse_buzzer(now)
