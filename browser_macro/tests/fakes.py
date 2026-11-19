"""테스트용 가짜 객체 (Windows API, 입력 백엔드, pynput 키, 시계)."""
import copy


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def advance(self, s):
        self.now += s


class KeyCode:
    def __init__(self, char=None, vk=None):
        self.char, self.vk = char, vk


class Key:
    """pynput.keyboard.Key 멤버 흉내: .name / .value(KeyCode)."""

    def __init__(self, name, vk, char=None):
        self.name = name
        self.value = KeyCode(char, vk)


K = {n: Key(n, vk) for n, vk in [("enter", 13), ("tab", 9), ("backspace", 8), ("shift", 160),
                                   ("ctrl_l", 162), ("f9", 120), ("f10", 121), ("f12", 123),
                                   ("space", 32), ("esc", 27)]}


def ch(c):
    return KeyCode(c, ord(c.upper()))


class FakeWinApi:
    supported = True

    def __init__(self, windows=None, monitors=None, own_pid=999):
        self.windows = windows or []
        self.monitors = monitors or [{"device": "D1", "rect": [0, 0, 1920, 1080],
                                      "work": [0, 0, 1920, 1040], "primary": True}]
        self.applied = []
        self.fg_hwnd = self.windows[0]["hwnd"] if self.windows else None
        self.point_map = {}  # (x,y) -> hwnd

    def add(self, hwnd, title, rect, pid=100, process="chrome.exe", state="normal", cls="Chrome_WidgetWin_1"):
        self.windows.append({"hwnd": hwnd, "title": title, "class": cls, "pid": pid,
                             "process": process, "rect": list(rect), "normal_rect": list(rect),
                             "state": state})
        if self.fg_hwnd is None:
            self.fg_hwnd = hwnd
        return self

    def _get(self, hwnd):
        return next((w for w in self.windows if w["hwnd"] == hwnd), None)

    def get_monitors(self):
        return copy.deepcopy(self.monitors)

    def list_windows(self, processes=None):
        return [copy.deepcopy(w) for w in self.windows
                if processes is None or w["process"] in processes]

    def window_info(self, hwnd):
        w = self._get(hwnd)
        return copy.deepcopy(w) if w else None

    def root_window_at(self, x, y):
        return self.window_info(self.point_map.get((x, y), self.fg_hwnd))

    def foreground_window(self):
        return self.window_info(self.fg_hwnd)

    def get_rect(self, hwnd):
        return self._get(hwnd)["rect"]

    def window_exists(self, hwnd):
        return self._get(hwnd) is not None

    def apply_placement(self, hwnd, rect, state, normal_rect=None, bring_to_top=False):
        w = self._get(hwnd)
        if not w:
            return False
        w["rect"], w["state"] = list(rect), state
        self.applied.append((hwnd, list(rect), state, bring_to_top))
        return True


class FakeBackend:
    def __init__(self, clock=None, fail_on=None):
        self.calls = []
        self.clock = clock
        self.fail_on = fail_on

    def _log(self, *c):
        if self.fail_on and c[0] == self.fail_on:
            raise RuntimeError("의도된 실패")
        self.calls.append(c + ((round(self.clock(), 3),) if self.clock else ()))

    def move(self, x, y):
        self._log("move", x, y)

    def press(self, b):
        self._log("press", b)

    def release(self, b):
        self._log("release", b)

    def scroll(self, dx, dy):
        self._log("scroll", dx, dy)

    def key_down(self, kd):
        self._log("key_down", kd.get("name") or kd.get("char"))

    def key_up(self, kd):
        self._log("key_up", kd.get("name") or kd.get("char"))
