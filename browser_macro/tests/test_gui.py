"""GUI 전체 흐름 테스트 (화면이 있는 환경에서만 실행: Windows 또는 Linux+Xvfb)."""
import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("화면(DISPLAY) 없음", allow_module_level=True)

from macro_app import gui  # noqa: E402
from macro_app.storage import MacroStore  # noqa: E402

from fakes import FakeBackend, FakeWinApi, ch  # noqa: E402


class FakeListener:
    started = stopped = 0

    def __init__(self, cb):
        self.cb = cb

    def start(self):
        FakeListener.started += 1

    def stop(self):
        FakeListener.stopped += 1


def pump(root, cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_full_flow(tmp_path, monkeypatch):
    answers = {"askstring": ["근무 화면"], "askyesno": []}
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: answers["askstring"].pop(0))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    shown = []
    for fn in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(gui.messagebox, fn, lambda *a, _fn=fn, **k: shown.append((_fn, a)))

    # 검토 화면은 의심 항목(1234)이 자동 체크되었는지 확인하고 확인을 누름
    reviewed = {}

    class AutoReview(gui.ReviewDialog):
        def __init__(self, master, segments):
            super().__init__(master, segments)
            reviewed["texts"] = [(s["text"], v.get()) for s, v in zip(segments, self.vars)]
            self.after(50, self._ok)

    monkeypatch.setattr(gui, "ReviewDialog", AutoReview)

    api = FakeWinApi().add(1, "업무 포털 - Google Chrome", [0, 0, 800, 600])
    api.add(2, "알림 팝업 - Google Chrome", [800, 0, 1200, 300])
    store = MacroStore(str(tmp_path))
    backend = FakeBackend()
    root = tk.Tk()
    app = gui.App(root, store, api, backend_factory=lambda: backend,
                  hotkey_listener_factory=FakeListener, recorder_listeners=False)
    root.update()

    # ---------------- 기록
    app.start_recording()
    assert app.state == "recording"
    rec = app.recorder
    rec.handle_click(100, 100, "left", True)
    rec.handle_click(100, 100, "left", False)
    for c in "intra":
        rec.handle_key(ch(c), True)
        rec.handle_key(ch(c), False)
    rec.handle_click(300, 300, "left", True)
    for c in "1234":
        rec.handle_key(ch(c), True)
        rec.handle_key(ch(c), False)
    rec.handle_click(300, 300, "left", False)
    app.stop_recording()

    assert reviewed["texts"] == [("intra", False), ("1234", True)]
    names = [n for n, _, _ in store.list_macros()]
    assert names == ["근무 화면"]
    m = store.load("근무 화면")
    chars = "".join(e.get("char", "") for e in m["events"] if e["type"] == "key_down")
    assert chars == "intra"                       # 인증번호 1234 는 저장되지 않음
    assert len(m["layout"]) == 2 and m["monitors"]

    # ---------------- 재생 (창 배치를 흐트러뜨린 뒤 복원되는지)
    api.windows[0]["rect"] = [50, 50, 300, 300]
    api.windows[1]["rect"] = [0, 0, 100, 100]
    app.speed_var.set("4")
    app.play_selected()
    assert app.state == "countdown" and FakeListener.started == 1
    assert pump(root, lambda: app.state == "idle", timeout=15), app.status_var.get()
    assert "완료" in app.status_var.get()
    assert api.windows[0]["rect"] == [0, 0, 800, 600] and api.windows[1]["rect"] == [800, 0, 1200, 300]
    assert [c[0] for c in backend.calls][:4] == ["move", "press", "release", "key_down"]
    assert FakeListener.stopped == 1

    # ---------------- 카운트다운 중 취소
    app.play_selected()
    app.stop_playback()
    assert app.state == "idle" and "취소" in app.status_var.get()

    # ---------------- 모니터 변경 경고 → '아니오'면 실행 안 함
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    api.monitors.append({"rect": [1920, 0, 3840, 1080]})
    app.play_selected()
    assert app.state == "idle"
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)

    # ---------------- 이름 변경 / 삭제
    answers["askstring"] = ["야간 화면"]
    app.rename_selected()
    assert [n for n, _, _ in store.list_macros()] == ["야간 화면"]
    assert "야간" in app.diagnostics_text() or "모니터" in app.diagnostics_text()
    app.delete_selected()
    assert store.list_macros() == []
    assert not [s for s in shown if s[0] == "showerror"], shown
    root.destroy()
