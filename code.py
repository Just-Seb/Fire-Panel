import board
import busio
import digitalio
import time
import neopixel
from adafruit_mcp230xx.mcp23s17 import MCP23S17
from DispDriver import LCD12864

# Button translation
translation = [
    8, 7, 6, 2, 1, 0, 29, 5, 5, 4, 11, 10, 17, 16, 9, 3, 25, 26, 18, 19, 20, 13, 14, 15,
    21, 22, 23, 27, 28, 3, 38, 37, 36, 32, 31, 30, 59, 35, 35, 34, 41, 40, 47, 46, 39,
    33, 55, 56, 48, 49, 50, 43, 44, 45, 51, 52, 53, 57, 58, 33
]

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

# State Tracking
button_states = [0] * 60  # 0=normal, 1=alarm, 2=trouble
state_names = {0: "NORMAL", 1: "ALARM", 2: "TROUBLE"}

# SPI Bus
spi = busio.SPI(board.GP18, MOSI=board.GP19, MISO=board.GP16)

# MCP Helper function to reduce boilerplate initialization
def setup_mcp(cs_pin, int_pin, addr, start_pin, count, interrupt_enable):
    cs = digitalio.DigitalInOut(cs_pin)
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

# Setup MCPs (Main Buttons)
mcp_configs = [
    (board.GP17, board.GP15, 0x00, 16, 0xFFFF), # MCP 1
    (board.GP28, board.GP14, 0x01, 14, 0x3FFF), # MCP 2
    (board.GP7,  board.GP9,  0x00, 16, 0xFFFF), # MCP 3
    (board.GP12, board.GP8,  0x01, 14, 0x3FFF)  # MCP 4
]

button_mcps = []
offset = 0
for cs_pin, int_pin, addr, count, inten in mcp_configs:
    mcp, ipin = setup_mcp(cs_pin, int_pin, addr, 0, count, inten)
    button_mcps.append({
        "mcp": mcp, 
        "int": ipin, 
        "count": count, 
        "offset": offset, 
        "pressed": [False] * count,     # Track physical state per pin
        "last_edge": [0.0] * count      # Track debounce per pin
    })
    offset += count

# Setup MCP (Control)
mcp_control, int_pin_control = setup_mcp(board.GP20, board.GP21, 0x00, 8, 8, 0xFF00)

print("Monitoring 60 buttons with interrupts...")

# NeoPixel Setup
main_pixels = neopixel.NeoPixel(board.GP2, 12, brightness=.1, auto_write=False, pixel_order=neopixel.GRB)
alarm_pixels = neopixel.NeoPixel(board.GP0, 60, brightness=.1, auto_write=False, pixel_order=neopixel.GRB)
trouble_pixels = neopixel.NeoPixel(board.GP1, 60, brightness=.1, auto_write=False, pixel_order=neopixel.GRB)

def test_pixels(pixels, color, count):
    for i in range(count): pixels[i] = color
    pixels.show()
    time.sleep(1)
    pixels.fill((0, 0, 0))

print("Testing NeoPixels...")
test_pixels(main_pixels, (0, 0, 255), 12)
test_pixels(alarm_pixels, (255, 0, 0), 60)
test_pixels(trouble_pixels, (255, 165, 0), 60)
main_pixels.show(); alarm_pixels.show(); trouble_pixels.show()
print("NeoPixel test complete!")

def update_leds():
    """Optimized LED mapping without slow list splicing"""
    for i in range(60):
        # Target index calculation replaces [mid:] + [:mid] reversed splicing
        target_idx = 29 - i if i < 30 else 89 - i
        state = button_states[i]
        
        alarm_pixels[target_idx] = (255, 0, 0) if state == 1 else (0, 0, 0)
        trouble_pixels[target_idx] = (255, 165, 0) if state == 2 else (0, 0, 0)
        
    alarm_pixels.show()
    trouble_pixels.show()

# Buzzer Setup
buzzer = digitalio.DigitalInOut(board.GP26)
buzzer.direction = digitalio.Direction.OUTPUT
buzzer.value = False

last_buzzer_toggle = 0
buzzer_silenced = False
alarms_cleared = True
training_mode = False

def pulse_buzzer(current_time):
    global last_buzzer_toggle, buzzer_silenced, alarms_cleared
    if training_mode:
        buzzer.value = False
        return
        
    any_alarm = any(state != 0 for state in button_states)
    
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

# Display Setup
display = LCD12864(cs_pin=board.GP13)
display.fill(0)
display.show()

class MenuSystem:
    def __init__(self, display):
        self.display = display
        self.mode = "main"
        self.idx = 0
        
    def _get_list(self):
        if self.mode == "alarms": return [i for i, s in enumerate(button_states) if s == 1]
        if self.mode == "troubles": return [i for i, s in enumerate(button_states) if s == 2]
        return []

    def max_items(self):
        if self.mode == "main": return 3
        if self.mode in ("alarms", "troubles"): return max(0, len(self._get_list()) - 1)
        if self.mode == "buttons": return 59
        if self.mode == "clear": return 1
        return 0

    def scroll_up(self):
        m = self.max_items()
        self.idx = self.idx - 1 if self.idx > 0 else m
        return True

    def scroll_down(self):
        m = self.max_items()
        self.idx = self.idx + 1 if self.idx < m else 0
        return True

    def back_to_main(self):
        self.mode = "main"
        self.idx = 0

    def enter_mode(self):
        if self.mode == "main":
            modes = ["alarms", "troubles", "buttons", "clear"]
            self.mode = modes[self.idx]
            self.idx = 0

    def display_m(self):
        self.display.fill(0)
        if self.mode == "main":
            self.display.text("ALARM PANEL", 0, 0)
            items = ["Alarm Rooms", "Trouble Rooms", "All Rooms", "Clear All"]
            for i, item in enumerate(items):
                self.display.text(f"> {item}" if i == self.idx else f"  {item}", 0, 8 + (i * 10), color=0 if i==self.idx else 1, bg=1 if i==self.idx else 0)
        
        elif self.mode in ("alarms", "troubles"):
            lst = self._get_list()
            title = "ALL ALARMS" if self.mode == "alarms" else "ALL TROUBLES"
            s_name = "ALARM" if self.mode == "alarms" else "TROUBLE"
            self.display.text(title, 0, 0)
            
            if not lst: self.display.text(f"NO {title.split()[1]}", 0, 35)
            else:
                self.display.text(f"Room {lst[self.idx] + 1}", 0, 16)
                self.display.text(f"State: {s_name}", 0, 35)
                self.display.text(f"{self.idx + 1}/{len(lst)}", 0, 48)
                
        elif self.mode == "buttons":
            self.display.text("Room STATUS", 0, 0)
            self.display.text(f"Room {self.idx + 1}", 0, 16)
            self.display.text(f"State: {state_names[button_states[self.idx]]}", 0, 35)
            self.display.text(f"{self.idx + 1}/60", 0, 48)

        elif self.mode == "clear":
            self.display.text("CLEAR ALL", 0, 0)
            self.display.text(f"Alarms: {button_states.count(1)}", 0, 16)
            self.display.text(f"Troubles: {button_states.count(2)}", 0, 24)
            self.display.text("> YES" if self.idx == 0 else "  YES", 0, 45, color=0 if self.idx==0 else 1, bg=1 if self.idx==0 else 0)
            self.display.text("> NO" if self.idx == 1 else "  NO", 48, 45, color=0 if self.idx==1 else 1, bg=1 if self.idx==1 else 0)

        if self.idx > 0 and self.mode != "main": self.display.text("^", 120, 0)
        if self.idx < self.max_items() and self.mode != "main": self.display.text("v", 120, 56)
        if training_mode: self.display.text("TRAINING", 70, 0)
        
        self.display.text("Press to select" if self.mode == "main" else "Hold: Menu", 0, 56)
        self.display.show()

menu = MenuSystem(display)
menu.display_m()

# Navigation & Control Setup
ctrl_pins = [8, 9, 10, 11, 12, 13, 15]
ctrl_pressed = {p: False for p in ctrl_pins}

print("Syncing hardware state...")
# Flush SPI garbage from NeoPixel test and lock in baseline states
for _ in range(2):
    for pin in ctrl_pins:
        ctrl_pressed[pin] = not mcp_control.get_pin(pin).value
    mcp_control.clear_ints()
    
    for c in button_mcps:
        for i in range(c["count"]):
            c["pressed"][i] = not c["mcp"].get_pin(i).value
        c["mcp"].clear_ints()
    time.sleep(0.05)

# Initialize timers to current time to enforce a power-on debounce block
startup_time = time.monotonic()
ctrl_last_edge = {p: startup_time for p in ctrl_pins}
for c in button_mcps:
    c["last_edge"] = [startup_time] * c["count"]
nav_enter_time = 0

show_info = False
info_drawn = False
show_start = 0
show_btn = show_state = -1

print("System Ready!")

while True:
    current = time.monotonic()
    
    # 1. Check Control Buttons (Using Per-Pin State Machine)
    if not int_pin_control.value:
        for pin in ctrl_pins:
            is_low = not mcp_control.get_pin(pin).value
            
            # Transition to PRESSED
            if is_low and not ctrl_pressed[pin]:
                if (current - ctrl_last_edge[pin]) > 0.2:  # 200ms debounce for navigation buttons
                    ctrl_pressed[pin] = True
                    ctrl_last_edge[pin] = current
                    
                    if pin == 8: 
                        print("CONTROL: LEFT Pressed")
                        if menu.mode == "clear": 
                            menu.scroll_up()
                            show_info = False
                            menu.display_m()
                    elif pin == 9: 
                        print("CONTROL: RIGHT Pressed")
                        if menu.mode == "clear": 
                            menu.scroll_down()
                            show_info = False
                            menu.display_m()
                    elif pin == 11: 
                        print("CONTROL: SILENCE Pressed")
                        buzzer_silenced = not buzzer_silenced
                        if buzzer_silenced: 
                            buzzer.value = False
                            print("Buzzer silenced")
                        else:
                            print("Buzzer re-enabled")
                    elif pin == 13: 
                        print("CONTROL: UP Pressed")
                        menu.scroll_up()
                        show_info = False
                        menu.display_m()
                    elif pin == 12: 
                        print("CONTROL: DOWN Pressed")
                        menu.scroll_down()
                        show_info = False
                        menu.display_m()
                    elif pin == 10: 
                        print("CONTROL: ENTER Pressed")
                        nav_enter_time = current  # Start hold timer accurately
                    elif pin == 15:
                        print("CONTROL: EYE Pressed (Toggle Training)")
                        training_mode = not training_mode
                        menu.display_m()
                        
            # Transition to RELEASED
            elif not is_low and ctrl_pressed[pin]:
                if (current - ctrl_last_edge[pin]) > 0.2:  # 200ms debounce for release
                    ctrl_pressed[pin] = False
                    ctrl_last_edge[pin] = current
                    
                    # --- DEBUG PRINTS FOR RELEASES ---
                    if pin == 8: print("CONTROL: LEFT Released")
                    elif pin == 9: print("CONTROL: RIGHT Released")
                    elif pin == 11: print("CONTROL: SILENCE Released")
                    elif pin == 13: print("CONTROL: UP Released")
                    elif pin == 12: print("CONTROL: DOWN Released")
                    elif pin == 10: print("CONTROL: ENTER Released")
                    elif pin == 15: print("CONTROL: EYE Released")
                    # ---------------------------------
                    
                    # Execute Enter behavior ONLY on release
                    if pin == 10 and nav_enter_time > 0:
                        duration = current - nav_enter_time
                        if duration >= 1.0 and menu.mode != "main":
                            menu.back_to_main()
                            show_info = False
                            menu.display_m()
                            print("Back to main menu")
                        elif duration < 1.0:
                            show_info = False
                            if menu.mode == "clear":
                                if menu.idx == 0:
                                    for i in range(60): button_states[i] = 0
                                    update_leds()
                                    print("All states cleared!")
                                    display.fill(0); display.text("ALL CLEARED!", 0, 25); display.show()
                                    time.sleep(1.5)
                                else:
                                    print("Clear cancelled")
                                menu.back_to_main()
                            else:
                                menu.enter_mode()
                                print(f"Entered mode: {menu.mode}")
                            menu.display_m()
                        nav_enter_time = 0
                        
        mcp_control.clear_ints()

    # 2. Check Input MCPs (Using Per-Pin State Machine)
    for c in button_mcps:
        if not c["int"].value:
            for i in range(c["count"]):
                is_low = not c["mcp"].get_pin(i).value
                
                # Transition to PRESSED
                if is_low and not c["pressed"][i]:
                    if (current - c["last_edge"][i]) > 0.05:  # 50ms quick debounce for alarm buttons
                        c["pressed"][i] = True
                        c["last_edge"][i] = current
                        
                        physical = i + c["offset"]
                        logical = translation[physical]
                        print(f"Physical button {physical} (Logical button {logical}) pressed!")
                        
                        if training_mode:
                            button_states[logical] = 0 if button_states[logical] == 2 else button_states[logical] + 1
                            print(f"Logical button {logical} -> {state_names[button_states[logical]]}")
                        else:
                            print(f"Showing info for logical button {logical} (state: {state_names[button_states[logical]]})")
                        
                        show_info, info_drawn = True, False
                        show_start, show_btn, show_state = current, logical, button_states[logical]
                        update_leds()
                        
                # Transition to RELEASED
                elif not is_low and c["pressed"][i]:
                    if (current - c["last_edge"][i]) > 0.05:
                        c["pressed"][i] = False
                        c["last_edge"][i] = current
                        
                        # --- DEBUG PRINT FOR MAIN BUTTON RELEASES ---
                        physical = i + c["offset"]
                        logical = translation[physical]
                        print(f"Physical button {physical} (Logical button {logical}) released!")
                        # --------------------------------------------
                        
            c["mcp"].clear_ints()

    # 3. Handle Temporary Info Display
    if show_info:
        if current - show_start >= 3.0:
            show_info = False
            menu.display_m()
        elif not info_drawn:
            display.fill(0)
            r_name = room_csv[show_btn] or f"Unnamed Room {show_btn + 1}"
            display.text(r_name, 0, 20)
            display.text(f"State: {state_names[show_state]}", 0, 40)
            display.show()
            info_drawn = True

    pulse_buzzer(current)