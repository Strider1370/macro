"""Windows API 래퍼 (ctypes 만 사용 → pywin32 설치 불필요, 관리자 권한 불필요).

Windows 가 아닌 환경(개발/테스트용 Linux 등)에서는 NullWinApi 가 사용되며,
창 관련 기능은 '지원 안 함'으로 동작합니다.

좌표는 모두 '물리 픽셀' 기준 가상 화면 좌표입니다.
(다중 모니터에서 주 모니터 왼쪽/위쪽 모니터는 음수 좌표를 가집니다.)
"""
from __future__ import annotations

import ctypes
import os
import sys

IS_WINDOWS = sys.platform == "win32"


def set_dpi_awareness() -> str:
    """프로세스를 DPI 인식 모드로 설정한다.

    배율(125%, 150% 등)이 다른 모니터가 섞여 있어도 기록/재생 좌표가
    어긋나지 않도록 반드시 다른 모듈(pynput, tkinter)보다 먼저 호출해야 한다.
    일반 사용자 권한으로 가능한 호출이며 시스템 설정은 바꾸지 않는다(이 프로세스에만 적용).
    """
    if not IS_WINDOWS:
        return "n/a"
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4 (Windows 10 1703+)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "unaware"


class NullWinApi:
    """Windows 가 아닌 환경용 더미 구현."""

    supported = False

    def get_monitors(self):
        return []

    def list_windows(self, processes=None):
        return []

    def window_info(self, hwnd):
        return None

    def root_window_at(self, x, y):
        return None

    def foreground_window(self):
        return None

    def get_rect(self, hwnd):
        return None

    def apply_placement(self, hwnd, rect, state, normal_rect=None, bring_to_top=False):
        return False

    def window_exists(self, hwnd):
        return False


if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:  # pragma: no cover
        dwmapi = None

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                    ("showCmd", wintypes.UINT), ("ptMinPosition", POINT),
                    ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                         ctypes.POINTER(RECT), wintypes.LPARAM)

    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(RECT),
                                           MONITORENUMPROC, wintypes.LPARAM]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
    user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    GA_ROOT = 2
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    DWMWA_CLOAKED = 14
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SW_SHOWNORMAL = 1
    SW_SHOWMINIMIZED = 2
    SW_SHOWMAXIMIZED = 3
    SW_SHOWMINNOACTIVE = 7
    SW_RESTORE = 9
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    HWND_TOP = 0

    def _h(value):
        """HWND(c_void_p) 반환값을 int 로 정규화."""
        return int(value) if value else 0

    class WinApi:
        supported = True

        def __init__(self):
            self._proc_cache = {}

        # ---------- 모니터 ----------
        def get_monitors(self):
            monitors = []

            def _cb(hmon, hdc, lprect, lparam):
                info = MONITORINFOEXW()
                info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                    r, w = info.rcMonitor, info.rcWork
                    monitors.append({
                        "device": info.szDevice,
                        "rect": [r.left, r.top, r.right, r.bottom],
                        "work": [w.left, w.top, w.right, w.bottom],
                        "primary": bool(info.dwFlags & 1),
                    })
                return True

            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
            monitors.sort(key=lambda m: (m["rect"][0], m["rect"][1]))
            return monitors

        # ---------- 창 정보 ----------
        def _process_name(self, pid):
            if pid in self._proc_cache:
                return self._proc_cache[pid]
            name = ""
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:
                    size = wintypes.DWORD(1024)
                    buf = ctypes.create_unicode_buffer(1024)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                        name = os.path.basename(buf.value).lower()
                finally:
                    kernel32.CloseHandle(handle)
            if len(self._proc_cache) > 500:
                self._proc_cache.clear()
            self._proc_cache[pid] = name
            return name

        def get_rect(self, hwnd):
            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None
            return [r.left, r.top, r.right, r.bottom]

        def _placement(self, hwnd):
            wp = WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(WINDOWPLACEMENT)
            if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
                return None
            n = wp.rcNormalPosition
            return [n.left, n.top, n.right, n.bottom]

        def _is_cloaked(self, hwnd):
            if dwmapi is None:
                return False
            val = ctypes.c_int(0)
            try:
                dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED,
                                             ctypes.byref(val), ctypes.sizeof(val))
            except Exception:
                return False
            return val.value != 0

        def window_info(self, hwnd):
            hwnd = _h(hwnd)
            if not hwnd or not user32.IsWindow(hwnd):
                return None
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if user32.IsIconic(hwnd):
                state = "minimized"
            elif user32.IsZoomed(hwnd):
                state = "maximized"
            else:
                state = "normal"
            return {
                "hwnd": hwnd,
                "title": buf.value,
                "class": cls.value,
                "pid": pid.value,
                "process": self._process_name(pid.value),
                "rect": self.get_rect(hwnd),
                "normal_rect": self._placement(hwnd),
                "state": state,
            }

        def list_windows(self, processes=None):
            """화면에 보이는 최상위 창 목록 (Z-order 위쪽부터)."""
            hwnds = []

            def _cb(hwnd, lparam):
                hwnds.append(_h(hwnd))
                return True

            user32.EnumWindows(WNDENUMPROC(_cb), 0)
            procs = {p.lower() for p in processes} if processes else None
            result = []
            for hwnd in hwnds:
                if not user32.IsWindowVisible(hwnd):
                    continue
                if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                    continue
                if self._is_cloaked(hwnd):
                    continue
                info = self.window_info(hwnd)
                if not info or not info["title"]:
                    continue
                if procs is not None and info["process"] not in procs:
                    continue
                r = info["rect"]
                if info["state"] != "minimized" and (not r or r[2] - r[0] < 50 or r[3] - r[1] < 50):
                    continue
                result.append(info)
            return result

        def root_window_at(self, x, y):
            hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
            if not hwnd:
                return None
            root = user32.GetAncestor(hwnd, GA_ROOT)
            return self.window_info(root or hwnd)

        def foreground_window(self):
            return self.window_info(user32.GetForegroundWindow())

        def window_exists(self, hwnd):
            return bool(hwnd) and bool(user32.IsWindow(hwnd))

        # ---------- 창 배치 ----------
        def apply_placement(self, hwnd, rect, state, normal_rect=None, bring_to_top=False):
            """창을 지정 위치/크기/상태로 만든다. 성공 시 True."""
            if not self.window_exists(hwnd):
                return False
            if state in ("maximized", "minimized") and normal_rect:
                wp = WINDOWPLACEMENT()
                wp.length = ctypes.sizeof(WINDOWPLACEMENT)
                user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
                # 먼저 일반 상태로 되돌린 뒤 '복원 위치'를 대상 모니터로 옮기고 최대화/최소화
                if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                l, t, r, b = normal_rect
                wp.rcNormalPosition = RECT(l, t, r, b)
                wp.showCmd = SW_SHOWNORMAL
                user32.SetWindowPlacement(hwnd, ctypes.byref(wp))
                wp.showCmd = SW_SHOWMAXIMIZED if state == "maximized" else SW_SHOWMINNOACTIVE
                ok = bool(user32.SetWindowPlacement(hwnd, ctypes.byref(wp)))
            else:
                if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                l, t, r, b = rect
                flags = SWP_NOACTIVATE | (0 if bring_to_top else SWP_NOZORDER)
                ok = bool(user32.SetWindowPos(hwnd, HWND_TOP, l, t, r - l, b - t, flags))
                if ok and state == "maximized":
                    user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
            if bring_to_top and state != "minimized":
                user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0,
                                    SWP_NOACTIVATE | 0x0001 | 0x0002)  # NOSIZE|NOMOVE
            return ok


def get_api():
    if IS_WINDOWS:
        return WinApi()
    return NullWinApi()
