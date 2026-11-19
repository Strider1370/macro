"""마우스/키보드 동작 기록기.

- 마우스 이동은 '버튼을 누른 채 움직일 때(드래그)'만 간격을 줄여 기록한다.
  (클릭 이벤트에 좌표가 들어 있으므로 일반 이동은 재생에 필요 없음)
- 마우스를 놓은 직후 해당 창의 위치/크기가 바뀌었으면 'window_rect' 이벤트를 추가해
  재생 시 창 이동/크기 변경을 정확히 재현한다.
- 이 프로그램 자신의 창에서 일어난 클릭은 기록하지 않는다.
- 단축키(기록에서 제외): F9 = 일시정지/재개, F10 = 기록 종료
"""
from __future__ import annotations

import os
import threading
import time

from . import privacy

HOTKEY_PAUSE = "f9"
HOTKEY_STOP_RECORD = "f10"
HOTKEY_STOP_PLAY = "f12"
HOTKEYS = {HOTKEY_PAUSE, HOTKEY_STOP_RECORD, HOTKEY_STOP_PLAY}

DRAG_MIN_INTERVAL = 0.03   # 초
DRAG_MIN_DISTANCE = 4      # 픽셀
WINDOW_CHECK_DELAY = 0.35  # 마우스를 놓은 뒤 창 위치를 확인할 때까지 대기(초)


def serialize_key(key):
    """pynput 키 객체 → JSON 저장용 dict."""
    if key is None:
        return {}
    name = getattr(key, "name", None)
    value = getattr(key, "value", None)
    if name is not None and value is not None:          # pynput.keyboard.Key (특수키)
        d = {"name": name}
        vk = getattr(value, "vk", None)
        if vk is not None:
            d["vk"] = vk
        ch = getattr(value, "char", None)
        if name == "space":
            d["char"] = " "
        elif ch:
            d["char"] = ch
        return d
    d = {}                                               # pynput.keyboard.KeyCode
    vk = getattr(key, "vk", None)
    ch = getattr(key, "char", None)
    if vk is not None:
        d["vk"] = vk
    if ch is not None:
        d["char"] = ch
    return d


def key_ident(d):
    return d.get("name") or (("vk", d["vk"]) if "vk" in d else ("ch", d.get("char")))


class Recorder:
    def __init__(self, api, keyboard_mode="all", sensitive_keywords=None,
                 on_hotkey=None, clock=time.perf_counter, own_pid=None,
                 window_check_delay=WINDOW_CHECK_DELAY):
        self.api = api
        self.keyboard_mode = keyboard_mode  # "all" | "shortcuts" | "off"
        self.sensitive_keywords = sensitive_keywords or privacy.DEFAULT_SENSITIVE_KEYWORDS
        self.on_hotkey = on_hotkey or (lambda name: None)
        self.clock = clock
        self.own_pid = own_pid if own_pid is not None else os.getpid()
        self.window_check_delay = window_check_delay
        self._lock = threading.RLock()
        self.events = []
        self.active = False
        self.paused = False
        self.stats = {"masked_keys": 0, "ignored_own_clicks": 0, "privacy_pauses": 0}
        self._t0 = 0.0
        self._offset = 0.0
        self._pause_started = None
        self._buttons = {}          # 기록 중인 눌린 버튼 → 눌렀을 때 창 정보
        self._ignored_buttons = set()
        self._keys_down = {}        # 기록 중인 눌린 키 ident → key dict
        self._held_modifiers = set()
        self._masking = False
        self._last_drag = None
        self._timers = []
        self._checked = set()
        self._last_scroll_t = -1.0
        self._listeners = []

    # ------------------------------------------------------------ 제어
    def start(self, use_listeners=True):
        with self._lock:
            self.events = []
            self._t0 = self.clock()
            self._offset = 0.0
            self.active = True
            self.paused = False
        if use_listeners:
            from pynput import keyboard, mouse  # 지연 import (테스트 환경 대비)
            ml = mouse.Listener(on_click=lambda x, y, b, p, *a: self.handle_click(x, y, b.name, p),
                                on_move=lambda x, y, *a: self.handle_move(x, y),
                                on_scroll=lambda x, y, dx, dy, *a: self.handle_scroll(x, y, dx, dy))
            kl = keyboard.Listener(on_press=lambda k, *a: self.handle_key(k, True),
                                   on_release=lambda k, *a: self.handle_key(k, False))
            self._listeners = [ml, kl]
            for lst in self._listeners:
                lst.start()
            for lst in self._listeners:
                lst.wait()

    def stop(self):
        """기록을 끝내고 정리된 이벤트 목록을 반환한다."""
        for lst in self._listeners:
            try:
                lst.stop()
            except Exception:
                pass
        self._listeners = []
        for timer in list(self._timers):
            timer.cancel()
            # 이미 실행 중인 확인 작업은 마무리하도록 짧게 대기
        self._run_pending_window_checks()
        with self._lock:
            if self.paused:
                self._resume()
            self.active = False
            t = self._now()
            # 눌린 채 끝난 키/버튼은 재생 때 '눌림 고착'이 생기지 않도록 해제 이벤트 추가
            for ident, kd in list(self._keys_down.items()):
                self._add(t, "key_up", **kd)
            self._keys_down.clear()
            for button in list(self._buttons):
                self._add(t, "mouse_up", x=None, y=None, button=button)
            self._buttons.clear()
            self.events.sort(key=lambda e: e["t"])
            return list(self.events)

    def toggle_pause(self):
        with self._lock:
            if not self.active:
                return self.paused
            if self.paused:
                self._resume()
            else:
                self.paused = True
                self._pause_started = self.clock()
                self.stats["privacy_pauses"] += 1
                t = self._now()
                for ident, kd in list(self._keys_down.items()):
                    self._add(t, "key_up", **kd)
                self._keys_down.clear()
            return self.paused

    def _resume(self):
        gap = self.clock() - self._pause_started
        self._offset += gap
        self.paused = False
        self._add(self._now(), "privacy_gap", duration=round(gap, 2))

    # ------------------------------------------------------------ 내부
    def _now(self):
        return self.clock() - self._t0 - self._offset

    def _add(self, t, typ, **data):
        ev = {"t": round(max(t, 0.0), 3), "type": typ}
        ev.update(data)
        self.events.append(ev)
        return ev

    def _is_own_window(self, info):
        return bool(info) and info.get("pid") == self.own_pid

    # ------------------------------------------------------------ 마우스
    def handle_click(self, x, y, button, pressed):
        with self._lock:
            if not self.active:
                return
            t = self._now()
            if pressed:
                info = self.api.root_window_at(x, y)
                if self.paused or self._is_own_window(info):
                    if not self.paused:
                        self.stats["ignored_own_clicks"] += 1
                    self._ignored_buttons.add(button)
                    return
                self._masking = False
                self._buttons[button] = info
                self._add(t, "mouse_down", x=int(x), y=int(y), button=button)
                self._last_drag = (t, x, y)
            else:
                if button in self._ignored_buttons:
                    self._ignored_buttons.discard(button)
                    return
                if button not in self._buttons:
                    return
                info = self._buttons.pop(button)
                self._add(t, "mouse_up", x=int(x), y=int(y), button=button)
                if info and info.get("hwnd") and self.api.supported:
                    self._schedule_window_check(t, info)

    def handle_move(self, x, y):
        with self._lock:
            if not self.active or self.paused or not self._buttons:
                return
            t = self._now()
            lt, lx, ly = self._last_drag or (0, x, y)
            if t - lt < DRAG_MIN_INTERVAL or (abs(x - lx) + abs(y - ly)) < DRAG_MIN_DISTANCE:
                return
            self._last_drag = (t, x, y)
            self._add(t, "move", x=int(x), y=int(y))

    def handle_scroll(self, x, y, dx, dy):
        with self._lock:
            if not self.active or self.paused:
                return
            info = self.api.root_window_at(x, y)
            if self._is_own_window(info):
                return
            t = self._now()
            last = self.events[-1] if self.events else None
            # 연속 스크롤은 한 이벤트로 합쳐 파일 크기를 줄임
            prev_t, self._last_scroll_t = self._last_scroll_t, t
            if (last and last["type"] == "scroll" and t - prev_t < 0.15
                    and abs(last["x"] - x) < 5 and abs(last["y"] - y) < 5):
                last["dx"] += int(dx)
                last["dy"] += int(dy)
                return
            self._add(t, "scroll", x=int(x), y=int(y), dx=int(dx), dy=int(dy))

    # ------------------------------------------------------------ 창 위치 변화
    def _schedule_window_check(self, t, info):
        holder = {}

        def check():
            self._check_window(t, info)
            with self._lock:
                if holder.get("timer") in self._timers:
                    self._timers.remove(holder["timer"])

        if self.window_check_delay <= 0:
            self._check_window(t, info)
            return
        timer = threading.Timer(self.window_check_delay, check)
        timer.daemon = True
        timer.info = (t, info)
        holder["timer"] = timer
        self._timers.append(timer)
        timer.start()

    def _run_pending_window_checks(self):
        pending = list(self._timers)
        self._timers.clear()
        for timer in pending:
            if timer.is_alive():
                timer.cancel()
                self._check_window(*timer.info)

    def _check_window(self, t, before):
        with self._lock:
            key = (t, before["hwnd"])
            if key in self._checked:
                return
            self._checked.add(key)
        after = self.api.window_info(before["hwnd"])
        if not after:
            return
        if after["rect"] == before["rect"] and after["state"] == before["state"]:
            return
        with self._lock:
            self._add(t + 0.05, "window_rect", title=after["title"], **{"class": after["class"]},
                      process=after["process"], rect=after["rect"],
                      normal_rect=after.get("normal_rect"), state=after["state"])

    # ------------------------------------------------------------ 키보드
    def handle_key(self, key, pressed):
        kd = serialize_key(key)
        name = kd.get("name")
        if name in privacy.MODIFIER_NAMES:
            (self._held_modifiers.add if pressed else self._held_modifiers.discard)(name)
        if name in HOTKEYS:
            if pressed:
                if name == HOTKEY_PAUSE:
                    self.toggle_pause()
                self.on_hotkey(name)
            return
        with self._lock:
            if not self.active:
                return
            ident = key_ident(kd)
            t = self._now()
            if not pressed:
                if ident in self._keys_down:
                    self._add(t, "key_up", **self._keys_down.pop(ident))
                return
            if self.paused or self.keyboard_mode == "off":
                return
            fg = self.api.foreground_window()
            if self._is_own_window(fg):
                return
            if fg and privacy.is_sensitive_title(fg.get("title"), self.sensitive_keywords):
                self.stats["masked_keys"] += 1
                if not self._masking:
                    self._masking = True
                    self._add(t, "masked", reason="민감 페이지(제목 키워드)에서의 키 입력은 기록하지 않음")
                return
            self._masking = False
            if self.keyboard_mode == "shortcuts" and not privacy.is_shortcut_allowed(
                    name, kd.get("char"), self._held_modifiers):
                return
            self._keys_down[ident] = kd
            self._add(t, "key_down", **kd)
