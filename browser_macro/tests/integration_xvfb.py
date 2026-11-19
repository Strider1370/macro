"""실제 pynput 입력으로 기록→재생을 검증하는 통합 테스트 (Linux Xvfb 전용, 개발자용).

    xvfb-run -s "-screen 0 1280x800x24" python tests/integration_xvfb.py

1) 대상 Tk 창(별도 프로세스)이 받은 클릭/키 입력을 파일에 기록
2) Recorder 로 실제 입력(XTest 주입)을 기록
3) 대상 로그를 비운 뒤 Player 로 재생
4) 두 번의 로그가 같은지 비교
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from macro_app.player import Player, PynputBackend  # noqa: E402
from macro_app.recorder import Recorder  # noqa: E402
from macro_app.winapi import NullWinApi  # noqa: E402

TARGET = r'''
import sys, tkinter as tk
log = sys.argv[1]
root = tk.Tk(); root.geometry("600x400+0+0"); root.title("target")
c = tk.Canvas(root, width=600, height=300, bg="white"); c.pack()
e = tk.Entry(root); e.pack(fill="x"); e.focus_force()
def w(s):
    with open(log, "a") as f: f.write(s + "\n")
c.bind("<ButtonPress-1>", lambda ev: (w(f"down {ev.x} {ev.y}"), e.focus_set()))
c.bind("<ButtonRelease-1>", lambda ev: w(f"up {ev.x} {ev.y}"))
c.bind("<B1-Motion>", lambda ev: w("drag"))
e.bind("<Return>", lambda ev: w("text " + e.get()) or e.delete(0, "end"))
root.bind_all("<KeyPress>", lambda ev: w("key " + ev.keysym + " focus=" + str(root.focus_get())))
root.after(30000, root.destroy)
root.mainloop()
'''


def read_log(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]


def summarize(lines):
    # 드래그 이벤트 개수는 샘플링 때문에 다를 수 있으므로 '있음' 여부만 비교
    out = []
    for ln in lines:
        if ln == "drag":
            if not out or out[-1] != "drag":
                out.append("drag")
        else:
            out.append(ln)
    return out


def main():
    from pynput.keyboard import Controller as KC, Key
    from pynput.mouse import Button, Controller as MC

    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "log.txt")
    open(log, "w").close()
    script = os.path.join(tmp, "target.py")
    with open(script, "w") as f:
        f.write(TARGET)
    proc = subprocess.Popen([sys.executable, script, log])
    time.sleep(1.5)

    rec = Recorder(NullWinApi(), keyboard_mode="all")
    rec.start()
    mouse, kb = MC(), KC()
    time.sleep(0.3)

    # ----- 사용자가 하는 작업 흉내
    mouse.position = (100, 100); time.sleep(0.2)
    mouse.press(Button.left); time.sleep(0.1); mouse.release(Button.left); time.sleep(0.3)
    mouse.position = (200, 150); mouse.press(Button.left)
    for i in range(10):
        mouse.position = (200 + i * 15, 150 + i * 5); time.sleep(0.03)
    mouse.release(Button.left); time.sleep(0.3)
    for c in "hello":
        kb.press(c); time.sleep(0.03); kb.release(c); time.sleep(0.03)
    kb.press(Key.enter); kb.release(Key.enter); time.sleep(0.5)

    events = rec.stop()
    if os.environ.get("DUMP"): print(json.dumps(events, ensure_ascii=False))
    first = read_log(log)
    print("기록된 이벤트 수:", len(events))
    print("1차(실제 조작) 대상 로그:", summarize(first))
    open(log, "w").close()

    t0 = time.time()
    result = Player({"events": events, "layout": []}, PynputBackend(), NullWinApi()).run()
    time.sleep(0.8)
    second = read_log(log)
    print(f"재생 결과: {result}  ({time.time() - t0:.2f}초)")
    print("2차(재생) 대상 로그:", summarize(second))
    proc.terminate()

    with open(os.path.join(tmp, "macro.json"), "w") as f:
        json.dump(events, f, ensure_ascii=False)
    print("이벤트 파일 크기(bytes):", os.path.getsize(os.path.join(tmp, "macro.json")))

    ok = result["ok"] and summarize(first) == summarize(second) and "text hello" in second
    print("통합 테스트:", "성공" if ok else "실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
