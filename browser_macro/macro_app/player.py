"""매크로 재생기.

- 기록된 시간 간격을 '절대 일정표' 방식으로 재현해 오차가 누적되지 않는다.
- 재생 속도(배속), 동작 전 추가 대기, 긴 대기 상한을 조절할 수 있다.
- stop() 또는 F12(GUI 에서 연결) 로 즉시 중단되며, 중단/오류 시
  눌려 있는 키·버튼을 모두 놓아 '키 눌림 고착'을 방지한다.
"""
from __future__ import annotations

import threading
import time

from . import layout as layout_mod

ACTION_START_TYPES = {"mouse_down", "key_down", "scroll"}
SKIP_TYPES = {"privacy_gap", "masked", "redacted", "marker"}


class PynputBackend:
    """실제 입력을 보내는 백엔드 (Windows 에서는 SendInput 사용)."""

    def __init__(self):
        from pynput import keyboard, mouse
        self._kb_mod = keyboard
        self._mouse_mod = mouse
        self.mouse = mouse.Controller()
        self.keyboard = keyboard.Controller()

    def _button(self, name):
        return getattr(self._mouse_mod.Button, name, self._mouse_mod.Button.left)

    def _key(self, kd):
        name = kd.get("name")
        if name and hasattr(self._kb_mod.Key, name):
            return getattr(self._kb_mod.Key, name)
        if kd.get("vk") is not None:
            return self._kb_mod.KeyCode.from_vk(kd["vk"])
        if kd.get("char"):
            return self._kb_mod.KeyCode.from_char(kd["char"])
        raise ValueError(f"알 수 없는 키: {kd}")

    def move(self, x, y):
        self.mouse.position = (x, y)

    def press(self, button):
        self.mouse.press(self._button(button))

    def release(self, button):
        self.mouse.release(self._button(button))

    def scroll(self, dx, dy):
        self.mouse.scroll(dx, dy)

    def key_down(self, kd):
        self.keyboard.press(self._key(kd))

    def key_up(self, kd):
        self.keyboard.release(self._key(kd))


class Player:
    def __init__(self, macro, backend, api, speed=1.0, extra_delay=0.0, max_gap=0.0,
                 restore_layout=True, processes=("chrome.exe",), countdown=0,
                 on_status=None, on_progress=None, clock=time.perf_counter,
                 sleep=time.sleep, settle_delay=1.0):
        self.macro = macro
        self.events = [e for e in macro.get("events", [])]
        self.backend = backend
        self.api = api
        self.speed = max(float(speed), 0.05)
        self.extra_delay = max(float(extra_delay), 0.0)
        self.max_gap = max(float(max_gap or 0.0), 0.0)
        self.restore_layout = restore_layout
        self.processes = list(processes)
        self.countdown = countdown
        self.on_status = on_status or (lambda msg: None)
        self.on_progress = on_progress or (lambda done, total: None)
        self.clock = clock
        self.sleep = sleep
        self.settle_delay = settle_delay
        self._stop = threading.Event()
        self._held_buttons = set()
        self._held_keys = {}
        self._last_pos = None

    # ------------------------------------------------------------
    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()

    def _wait_until(self, deadline):
        while not self._stop.is_set():
            remaining = deadline - self.clock()
            if remaining <= 0:
                return True
            self.sleep(min(remaining, 0.02))
        return False

    def schedule(self):
        """각 이벤트의 실행 예정 시각(초, 시작 기준)을 계산한다."""
        times, virtual, prev = [], 0.0, 0.0
        for ev in self.events:
            gap = max(ev["t"] - prev, 0.0)
            prev = ev["t"]
            if self.max_gap and gap > self.max_gap:
                gap = self.max_gap
            virtual += gap / self.speed
            if ev["type"] in ACTION_START_TYPES:
                virtual += self.extra_delay
            times.append(virtual)
        return times

    def estimated_duration(self):
        times = self.schedule()
        return times[-1] if times else 0.0

    # ------------------------------------------------------------
    def run(self):
        result = {"ok": False, "stopped": False, "error": None, "done": 0,
                  "total": len(self.events), "layout_report": None, "window_events": 0}
        try:
            for n in range(int(self.countdown), 0, -1):
                self.on_status(f"{n}초 후 시작합니다... (F12: 중단)")
                if not self._wait_until(self.clock() + 1.0):
                    break
            if not self._stop.is_set():
                self.on_status("재생 중... (F12: 긴급 중단)")
                times = self.schedule()
                base = self.clock()
                total = len(self.events)
                last_report = 0.0
                for i, (ev, at) in enumerate(zip(self.events, times)):
                    if not self._wait_until(base + at):
                        break
                    self._execute(ev, result)
                    result["done"] = i + 1
                    now = self.clock()
                    if now - last_report > 0.1 or i + 1 == total:
                        self.on_progress(i + 1, total)
                        last_report = now
            if not self._stop.is_set() and self.restore_layout and self.macro.get("layout"):
                self.on_status("창 배치 복원 중...")
                if self._wait_until(self.clock() + self.settle_delay):
                    result["layout_report"] = layout_mod.restore_layout(
                        self.api, self.macro["layout"], self.processes,
                        sleep=self.sleep, should_stop=lambda: self._stop.is_set())
            result["stopped"] = self._stop.is_set()
            result["ok"] = not result["stopped"]
        except Exception as exc:  # 오류 시에도 반드시 입력 해제
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            self._release_all()
        return result

    def _move(self, x, y):
        # 같은 위치로의 이동은 생략 (버튼을 누른 상태에서 불필요한 '드래그'가 생기지 않도록)
        if self._last_pos != (x, y):
            self.backend.move(x, y)
            self._last_pos = (x, y)

    def _execute(self, ev, result):
        typ = ev["type"]
        b = self.backend
        if typ in SKIP_TYPES:
            return
        if typ == "mouse_down":
            self._move(ev["x"], ev["y"])
            b.press(ev["button"])
            self._held_buttons.add(ev["button"])
        elif typ == "mouse_up":
            if ev.get("x") is not None:
                self._move(ev["x"], ev["y"])
            b.release(ev["button"])
            self._held_buttons.discard(ev["button"])
        elif typ == "move":
            self._move(ev["x"], ev["y"])
        elif typ == "scroll":
            self._move(ev["x"], ev["y"])
            b.scroll(ev.get("dx", 0), ev.get("dy", 0))
        elif typ == "key_down":
            b.key_down(ev)
            self._held_keys[ev.get("name") or ev.get("vk") or ev.get("char")] = ev
        elif typ == "key_up":
            b.key_up(ev)
            self._held_keys.pop(ev.get("name") or ev.get("vk") or ev.get("char"), None)
        elif typ == "window_rect":
            if layout_mod.apply_window_event(self.api, ev, self.processes):
                result["window_events"] += 1
        # 알 수 없는 유형은 무시 (상위 버전 파일 호환)

    def _release_all(self):
        for kd in list(self._held_keys.values()):
            try:
                self.backend.key_up(kd)
            except Exception:
                pass
        self._held_keys.clear()
        for button in list(self._held_buttons):
            try:
                self.backend.release(button)
            except Exception:
                pass
        self._held_buttons.clear()
