import json
import os

import pytest

from macro_app import layout, privacy, storage
from macro_app.player import Player
from macro_app.recorder import Recorder

from fakes import K, FakeBackend, FakeClock, FakeWinApi, ch


# ====================================================================== privacy
@pytest.mark.parametrize("text,flag", [
    ("123456", True), ("1234", True), ("Abcd1234!", True), ("pass12!@", True),
    ("900101-1234567", True), ("1234-5678-9012-3456", True),
    ("www.naver.com", False), ("https://intra.company.co.kr/board", False),
    ("hello world", False), ("근무일지", False), ("abc", False), ("12", False),
])
def test_suspicious_reason(text, flag):
    assert bool(privacy.suspicious_reason(text)) == flag


def test_title_keywords():
    assert privacy.is_sensitive_title("통합 로그인 - Google Chrome")
    assert privacy.is_sensitive_title("Sign in - Google Accounts")
    assert not privacy.is_sensitive_title("업무 대시보드 - Google Chrome")


def kd(t, typ, **k):
    d = {"t": t, "type": typ}
    d.update(k)
    return d


def test_extract_and_remove_segments():
    ev = [
        kd(0.0, "mouse_down", x=1, y=1, button="left"), kd(0.1, "mouse_up", x=1, y=1, button="left"),
        kd(0.2, "key_down", vk=65, char="a"), kd(0.25, "key_up", vk=65, char="a"),
        kd(0.3, "key_down", vk=66, char="b"), kd(0.35, "key_up", vk=66, char="b"),
        kd(0.4, "key_down", name="backspace", vk=8), kd(0.45, "key_up", name="backspace", vk=8),
        kd(0.5, "key_down", vk=67, char="c"), kd(0.55, "key_up", vk=67, char="c"),
        kd(0.6, "key_down", name="enter", vk=13), kd(0.65, "key_up", name="enter", vk=13),
        # Ctrl+T 는 문자열이 아님
        kd(0.7, "key_down", name="ctrl_l", vk=162), kd(0.75, "key_down", vk=84, char="\x14"),
        kd(0.8, "key_up", vk=84, char="\x14"), kd(0.85, "key_up", name="ctrl_l", vk=162),
        kd(0.9, "key_down", vk=49, char="1"), kd(0.95, "key_up", vk=49, char="1"),
        kd(1.0, "key_down", vk=50, char="2"), kd(1.05, "key_up", vk=50, char="2"),
        kd(1.1, "key_down", vk=51, char="3"), kd(1.15, "key_up", vk=51, char="3"),
        kd(1.2, "key_down", vk=52, char="4"), kd(1.25, "key_up", vk=52, char="4"),
    ]
    segs = privacy.extract_text_segments(ev)
    assert [s["text"] for s in segs] == ["ac", "1234"]
    assert segs[0]["reason"] is None and segs[1]["reason"]
    out = privacy.remove_segments(ev, [segs[1]])
    assert not any(e.get("char") in "1234" for e in out if e.get("char"))
    assert any(e["type"] == "redacted" and e["chars"] == 4 for e in out)
    # 'ac' 구간과 ctrl+t 는 그대로
    assert sum(1 for e in out if e.get("char") == "\x14") == 2
    all_removed = privacy.strip_all_text(ev)
    assert not [e for e in all_removed if e.get("char") in ("a", "b", "c")]


# ====================================================================== recorder
def make_recorder(mode="all", **kw):
    clock = FakeClock()
    api = FakeWinApi().add(1, "업무 포털 - Google Chrome", [0, 0, 800, 600])
    api.add(2, "매크로 프로그램", [1000, 0, 1400, 500], pid=999, process="python.exe", cls="TkTopLevel")
    api.point_map[(1100, 100)] = 2
    hot = []
    rec = Recorder(api, keyboard_mode=mode, clock=clock, own_pid=999, window_check_delay=0,
                   on_hotkey=hot.append, **kw)
    rec.start(use_listeners=False)
    return rec, api, clock, hot


def test_recorder_click_drag_and_window_move():
    rec, api, clock, _ = make_recorder()
    clock.advance(1.0)
    rec.handle_click(10, 10, "left", True)
    for i in range(1, 50):          # 1ms 간격의 이동 → 대부분 버려져야 함
        clock.advance(0.001)
        rec.handle_move(10 + i, 10)
    clock.advance(0.1)
    rec.handle_move(200, 150)
    api.windows[0]["rect"] = [190, 140, 990, 740]   # 창이 끌려서 이동했다고 가정
    rec.handle_click(200, 150, "left", False)
    rec.handle_move(500, 500)       # 버튼 안 누른 이동은 기록 안 함
    ev = rec.stop()
    types = [e["type"] for e in ev]
    assert types[0] == "mouse_down" and ev[0]["t"] == 1.0
    assert types.count("move") <= 3
    assert "mouse_up" in types
    win = [e for e in ev if e["type"] == "window_rect"]
    assert win and win[0]["rect"] == [190, 140, 990, 740] and win[0]["process"] == "chrome.exe"


def test_recorder_ignores_own_window_and_hotkeys():
    rec, api, clock, hot = make_recorder()
    rec.handle_click(1100, 100, "left", True)
    rec.handle_click(1100, 100, "left", False)
    rec.handle_key(K["f10"], True)
    rec.handle_key(K["f10"], False)
    ev = rec.stop()
    assert ev == []
    assert hot == ["f10"]
    assert rec.stats["ignored_own_clicks"] == 1


def test_recorder_masks_sensitive_window():
    rec, api, clock, _ = make_recorder()
    api.windows[0]["title"] = "통합 로그인 - Google Chrome"
    for c in "secret":
        rec.handle_key(ch(c), True)
        rec.handle_key(ch(c), False)
    api.windows[0]["title"] = "업무 포털 - Google Chrome"
    rec.handle_key(ch("x"), True)
    rec.handle_key(ch("x"), False)
    ev = rec.stop()
    assert [e["type"] for e in ev] == ["masked", "key_down", "key_up"]
    assert ev[1]["char"] == "x"
    assert rec.stats["masked_keys"] == 6
    assert "secret" not in json.dumps(ev)


def test_recorder_pause_removes_time_and_input():
    rec, api, clock, hot = make_recorder()
    clock.advance(1)
    rec.handle_key(ch("a"), True)
    rec.handle_key(ch("a"), False)
    rec.handle_key(K["f9"], True)        # 일시정지
    clock.advance(20)
    rec.handle_key(ch("p"), True)
    rec.handle_key(ch("p"), False)
    rec.handle_click(5, 5, "left", True)
    rec.handle_click(5, 5, "left", False)
    rec.handle_key(K["f9"], True)        # 재개
    clock.advance(1)
    rec.handle_key(ch("b"), True)
    ev = rec.stop()
    chars = [e.get("char") for e in ev if e["type"] == "key_down"]
    assert chars == ["a", "b"]
    gap = next(e for e in ev if e["type"] == "privacy_gap")
    assert gap["duration"] == 20
    assert ev[-1]["type"] == "key_up" and ev[-1]["char"] == "b"   # 고착 방지 해제
    assert max(e["t"] for e in ev) == pytest.approx(2.0, abs=0.01)  # 20초 공백 제외


def test_recorder_shortcuts_mode():
    rec, api, clock, _ = make_recorder(mode="shortcuts")
    rec.handle_key(ch("h"), True)
    rec.handle_key(ch("h"), False)
    rec.handle_key(K["ctrl_l"], True)
    rec.handle_key(ch("t"), True)
    rec.handle_key(ch("t"), False)
    rec.handle_key(K["ctrl_l"], False)
    rec.handle_key(K["enter"], True)
    rec.handle_key(K["enter"], False)
    ev = rec.stop()
    downs = [e.get("name") or e.get("char") for e in ev if e["type"] == "key_down"]
    assert downs == ["ctrl_l", "t", "enter"]


def test_recorder_scroll_merge():
    rec, api, clock, _ = make_recorder()
    for _ in range(5):
        clock.advance(0.05)
        rec.handle_scroll(300, 300, 0, -1)
    clock.advance(1)
    rec.handle_scroll(300, 300, 0, 1)
    ev = rec.stop()
    assert [(e["type"], e["dy"]) for e in ev] == [("scroll", -5), ("scroll", 1)]


# ====================================================================== player
def macro_with(events, layout_=None):
    return {"events": events, "layout": layout_ or []}


def test_player_schedule_and_order():
    clock = FakeClock()
    ev = [kd(1.0, "mouse_down", x=10, y=20, button="left"), kd(1.2, "move", x=30, y=20),
          kd(1.5, "mouse_up", x=30, y=20, button="left"), kd(31.5, "key_down", vk=13, name="enter"),
          kd(31.6, "key_up", vk=13, name="enter")]
    be = FakeBackend(clock)
    p = Player(macro_with(ev), be, FakeWinApi(), speed=2.0, extra_delay=0.5, max_gap=10,
               clock=clock, sleep=clock.sleep)
    assert p.schedule() == pytest.approx([1.0, 1.1, 1.25, 6.75, 6.8])
    r = p.run()
    assert r["ok"] and r["done"] == 5
    names = [c[0] for c in be.calls]
    assert names == ["move", "press", "move", "release", "key_down", "key_up"]
    assert be.calls[1][-1] == pytest.approx(1.0, abs=0.03)
    assert be.calls[-1][-1] == pytest.approx(6.8, abs=0.03)


def test_player_stop_releases_held_inputs():
    clock = FakeClock()
    ev = [kd(0.0, "key_down", name="shift", vk=160), kd(0.1, "mouse_down", x=1, y=1, button="left"),
          kd(100, "mouse_up", x=1, y=1, button="left")]
    be = FakeBackend()
    p = Player(macro_with(ev), be, FakeWinApi(), clock=clock, sleep=clock.sleep)
    orig = clock.sleep

    def sleep(s):
        orig(s)
        if clock.now > 5:
            p.stop()
    p.sleep = sleep
    r = p.run()
    assert r["stopped"] and not r["ok"] and r["done"] == 2
    assert ("release", "left") in be.calls and ("key_up", "shift") in be.calls


def test_player_error_releases():
    clock = FakeClock()
    ev = [kd(0.0, "mouse_down", x=1, y=1, button="left"), kd(0.1, "scroll", x=1, y=1, dx=0, dy=1)]
    be = FakeBackend(fail_on="scroll")
    r = Player(macro_with(ev), be, FakeWinApi(), clock=clock, sleep=clock.sleep).run()
    assert r["error"] and "의도된 실패" in r["error"]
    assert ("release", "left") in be.calls


def test_player_restores_layout_and_window_events():
    clock = FakeClock()
    api = FakeWinApi().add(1, "A - Google Chrome", [0, 0, 100, 100]).add(2, "B - Google Chrome", [0, 0, 100, 100])
    saved = [{"title": "B - Google Chrome", "class": "Chrome_WidgetWin_1", "process": "chrome.exe",
              "rect": [1920, 0, 2880, 1080], "normal_rect": None, "state": "normal"},
             {"title": "A - Google Chrome", "class": "Chrome_WidgetWin_1", "process": "chrome.exe",
              "rect": [-1280, 0, 0, 1024], "normal_rect": None, "state": "maximized"}]
    wev = kd(0.5, "window_rect", title="A - Google Chrome", process="chrome.exe", rect=[5, 5, 500, 500],
             state="normal", **{"class": "Chrome_WidgetWin_1"})
    r = Player(macro_with([wev], saved), FakeBackend(), api, clock=clock, sleep=clock.sleep).run()
    assert r["ok"] and r["window_events"] == 1
    rep = r["layout_report"]
    assert rep["moved"] == 2 and not rep["missing"]
    assert api._get(1)["rect"] == [-1280, 0, 0, 1024] and api._get(1)["state"] == "maximized"
    assert api._get(2)["rect"] == [1920, 0, 2880, 1080]
    # 마지막 패스: Z-order 아래(A) → 위(B) 순서로 맨 위로 올림
    last = [a for a in api.applied if a[3]]
    assert [a[0] for a in last] == [1, 2]


# ====================================================================== layout
def test_match_windows_title_and_order():
    saved = [{"title": "근무표 - Google Chrome", "process": "chrome.exe"},
             {"title": "알림 팝업", "process": "chrome.exe"},
             {"title": "(3) 메일함 - Google Chrome", "process": "chrome.exe"}]
    current = [{"title": "(5) 메일함 - Google Chrome", "process": "chrome.exe"},
               {"title": "근무표 - Google Chrome", "process": "chrome.exe"},
               {"title": "완전히 다른 제목", "process": "chrome.exe"},
               {"title": "메모장", "process": "notepad.exe"}]
    m, missing = layout.match_windows(saved, current)
    assert [(i, j, meth) for i, j, meth, _ in m] == [(0, 1, "title"), (1, 2, "order"), (2, 0, "title")]
    assert missing == []
    m2, missing2 = layout.match_windows(saved, current[:2], fallback_by_order=False)
    assert missing2 == [1]


def test_compare_monitors():
    a = [{"rect": [0, 0, 1920, 1080], "primary": True}, {"rect": [1920, 0, 3840, 1080]}]
    assert layout.compare_monitors(a, a) == []
    assert "개수" in layout.compare_monitors(a, a[:1])[0]
    b = [{"rect": [0, 0, 2560, 1440], "primary": True}, {"rect": [2560, 0, 4480, 1080]}]
    assert "해상도" in layout.compare_monitors(a, b)[0]
    assert layout.compare_monitors([], a) == []


def test_restore_layout_unsupported():
    from macro_app.winapi import NullWinApi
    rep = layout.restore_layout(NullWinApi(), [{"title": "x"}], ["chrome.exe"])
    assert rep["error"] and rep["moved"] == 0


# ====================================================================== storage
def test_storage_roundtrip(tmp_path):
    st = storage.MacroStore(str(tmp_path))
    ev = [kd(0.0, "mouse_down", x=-1200, y=50, button="left")]
    st.save("주간 근무", ev, layout=[{"title": "t"}], monitors=[{"rect": [0, 0, 1, 1]}])
    with pytest.raises(FileExistsError):
        st.save("주간 근무", ev)
    st.save("주간 근무", ev + ev, overwrite=True)
    m = st.load("주간 근무")
    assert m["name"] == "주간 근무" and len(m["events"]) == 2 and m["events"][0]["x"] == -1200
    st.save('야간:근무/2?', ev)
    names = [n for n, _, _ in st.list_macros()]
    assert names == ["야간:근무/2?", "주간 근무"]
    st.rename("주간 근무", "주간")
    with pytest.raises(FileExistsError):
        st.rename("주간", "야간:근무/2?")
    assert st.load("주간")["name"] == "주간" and not st.exists("주간 근무")
    st.delete("주간")
    # 손상된 파일은 목록에 오류로 표시되고 다른 파일에는 영향 없음
    with open(os.path.join(st.macro_dir, "broken.json"), "w") as f:
        f.write("{not json")
    items = {n: info for n, _, info in st.list_macros()}
    assert "error" in items["broken"] and items["야간:근무/2?"]["events"] == 1


def test_settings(tmp_path):
    st = storage.MacroStore(str(tmp_path))
    s = st.load_settings()
    assert s["speed"] == 1.0
    s["speed"] = 2.5
    s["unknown"] = 1
    st.save_settings(s)
    s2 = st.load_settings()
    assert s2["speed"] == 2.5 and "unknown" not in s2


def test_safe_filename():
    assert storage.safe_filename("a/b:c") == "a_b_c"
    assert storage.safe_filename("CON") == "_CON"
    assert storage.safe_filename("  ") == "_"
