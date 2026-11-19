"""tkinter GUI."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import layout as layout_mod
from . import privacy
from .player import Player
from .recorder import HOTKEY_PAUSE, HOTKEY_STOP_PLAY, HOTKEY_STOP_RECORD, Recorder

APP_TITLE = "업무 화면 자동 배치 매크로 (프로토타입)"
KEYBOARD_MODES = [
    ("all", "전체 기록 (주소 입력 등) - 저장 전 검토"),
    ("shortcuts", "단축키·특수키만 (일반 글자 기록 안 함)"),
    ("off", "키보드 기록 안 함"),
]


def default_hotkey_listener_factory(callback):
    """재생 중 F12 감지용 전역 키 리스너 (pynput)."""
    from pynput import keyboard

    def on_press(key, *args):
        if getattr(key, "name", None) == HOTKEY_STOP_PLAY:
            callback()

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    return listener


class ReviewDialog(tk.Toplevel):
    """저장 전 '입력한 문자열' 검토 화면."""

    def __init__(self, master, segments):
        super().__init__(master)
        self.title("저장 전 키보드 입력 검토")
        self.transient(master)
        self.resizable(True, True)
        self.result = None
        self.segments = segments
        self.vars = []
        self.show_var = tk.BooleanVar(value=False)

        ttk.Label(self, wraplength=520, justify="left", text=(
            "기록 중 입력한 문자열입니다. 비밀번호·인증번호 등 민감한 정보가 있으면 반드시 '삭제'를 체크하세요.\n"
            "의심되는 항목은 자동으로 체크되어 있습니다. 체크된 항목은 파일에 저장되지 않습니다.")
        ).pack(fill="x", padx=12, pady=(12, 6))

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12)
        canvas = tk.Canvas(frame, height=min(40 * len(segments) + 10, 320), highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.text_labels = []
        for i, seg in enumerate(segments):
            var = tk.BooleanVar(value=bool(seg.get("reason")))
            self.vars.append(var)
            ttk.Checkbutton(self.inner, text="삭제", variable=var).grid(row=i, column=0, sticky="w", pady=3)
            lbl = ttk.Label(self.inner, width=36)
            lbl.grid(row=i, column=1, sticky="w", padx=8)
            self.text_labels.append(lbl)
            ttk.Label(self.inner, text=seg.get("reason") or "", foreground="#b00020").grid(
                row=i, column=2, sticky="w")
        self._refresh_texts()

        opts = ttk.Frame(self)
        opts.pack(fill="x", padx=12, pady=6)
        ttk.Checkbutton(opts, text="의심 항목 내용 보기", variable=self.show_var,
                        command=self._refresh_texts).pack(side="left")
        ttk.Button(opts, text="모두 삭제 체크", command=lambda: [v.set(True) for v in self.vars]).pack(side="right")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="확인 (체크 항목 삭제 후 계속)", command=self._ok).pack(side="right")
        ttk.Button(btns, text="기록 버리기", command=self._cancel).pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()

    def _refresh_texts(self):
        for seg, lbl in zip(self.segments, self.text_labels):
            text = seg["text"]
            if seg.get("reason") and not self.show_var.get():
                text = privacy.mask_text(text)
            lbl.configure(text=f"'{text}' ({len(seg['text'])}자)")

    def _ok(self):
        self.result = [seg for seg, var in zip(self.segments, self.vars) if var.get()]
        self.destroy()

    def _cancel(self):
        if messagebox.askyesno("확인", "이번 기록을 저장하지 않고 버릴까요?", parent=self):
            self.result = None
            self.destroy()


class App:
    def __init__(self, root, store, api, backend_factory=None, hotkey_listener_factory=None,
                 dpi_mode="n/a", recorder_listeners=True):
        self.root = root
        self.store = store
        self.api = api
        self.backend_factory = backend_factory
        self.hotkey_listener_factory = hotkey_listener_factory or default_hotkey_listener_factory
        self.dpi_mode = dpi_mode
        self.recorder_listeners = recorder_listeners
        self.settings = store.load_settings()
        self.queue = queue.Queue()
        self.recorder = None
        self.player = None
        self.play_thread = None
        self.hotkey_listener = None
        self.pending = None      # 저장되지 않은 기록
        self.countdown_job = None
        self.state = "idle"      # idle | recording | countdown | playing

        root.title(APP_TITLE)
        root.minsize(620, 560)
        self._build()
        self.refresh_list()
        self._set_state("idle")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self._poll_queue)

    # ================================================================ UI 구성
    def _build(self):
        style = ttk.Style(self.root)
        style.configure("Big.TLabel", font=("Malgun Gothic", 12, "bold"))
        style.configure("Big.TButton", font=("Malgun Gothic", 11, "bold"), padding=6)
        pad = {"padx": 10, "pady": 4}

        self.status_var = tk.StringVar(value="준비")
        ttk.Label(self.root, textvariable=self.status_var, style="Big.TLabel",
                  anchor="center").pack(fill="x", padx=10, pady=(10, 4))

        # ---- 기록
        rec = ttk.LabelFrame(self.root, text="1. 동작 기록")
        rec.pack(fill="x", **pad)
        row = ttk.Frame(rec)
        row.pack(fill="x", padx=8, pady=6)
        self.btn_rec = ttk.Button(row, text="● 기록 시작", style="Big.TButton", command=self.start_recording)
        self.btn_rec.pack(side="left")
        self.btn_rec_stop = ttk.Button(row, text="■ 기록 종료 (F10)", style="Big.TButton",
                                       command=self.stop_recording)
        self.btn_rec_stop.pack(side="left", padx=6)
        self.btn_save = ttk.Button(row, text="💾 매크로 저장", command=self.save_pending)
        self.btn_save.pack(side="left", padx=6)

        row2 = ttk.Frame(rec)
        row2.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(row2, text="키보드:").pack(side="left")
        self.kb_var = tk.StringVar()
        labels = [lbl for _, lbl in KEYBOARD_MODES]
        self.kb_combo = ttk.Combobox(row2, values=labels, textvariable=self.kb_var, state="readonly", width=40)
        mode = self.settings.get("keyboard_mode", "all")
        self.kb_var.set(dict(KEYBOARD_MODES).get(mode, labels[0]))
        self.kb_combo.pack(side="left", padx=4)
        ttk.Label(rec, foreground="#555", wraplength=580, justify="left", text=(
            f"기록 중 {HOTKEY_PAUSE.upper()}: 일시정지/재개 (비밀번호 입력 전 누르세요)   "
            f"{HOTKEY_STOP_RECORD.upper()}: 기록 종료\n"
            "로그인은 매크로 실행 전에 직접 하세요. 제목에 '로그인/비밀번호/인증' 등이 있는 창의 키 입력은 자동으로 기록되지 않습니다.")
        ).pack(fill="x", padx=8, pady=(0, 6))

        # ---- 목록
        lst = ttk.LabelFrame(self.root, text="2. 저장된 매크로")
        lst.pack(fill="both", expand=True, **pad)
        cols = ("events", "windows", "duration", "updated")
        self.tree = ttk.Treeview(lst, columns=cols, height=7, selectmode="browse")
        self.tree.heading("#0", text="이름")
        for c, t, w in zip(cols, ("동작 수", "창 수", "길이(초)", "수정 시각"), (70, 60, 70, 150)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center", stretch=False)
        self.tree.column("#0", width=200)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        self.tree.bind("<Double-1>", lambda e: self.play_selected())
        sb = ttk.Scrollbar(lst, orient="vertical", command=self.tree.yview)
        sb.pack(side="left", fill="y", pady=6)
        self.tree.configure(yscrollcommand=sb.set)
        side = ttk.Frame(lst)
        side.pack(side="left", fill="y", padx=8, pady=6)
        self.btn_play = ttk.Button(side, text="▶ 매크로 실행", style="Big.TButton", command=self.play_selected)
        self.btn_play.pack(fill="x")
        self.btn_layout = ttk.Button(side, text="창 배치만 복원", command=self.restore_layout_only)
        self.btn_layout.pack(fill="x", pady=(8, 2))
        self.btn_update_layout = ttk.Button(side, text="현재 창 배치로 갱신", command=self.update_layout)
        self.btn_update_layout.pack(fill="x", pady=2)
        self.btn_rename = ttk.Button(side, text="이름 변경", command=self.rename_selected)
        self.btn_rename.pack(fill="x", pady=(8, 2))
        self.btn_delete = ttk.Button(side, text="삭제", command=self.delete_selected)
        self.btn_delete.pack(fill="x", pady=2)
        ttk.Button(side, text="저장 폴더 열기", command=self.open_folder).pack(fill="x", pady=(8, 2))
        ttk.Button(side, text="환경 진단", command=self.show_diagnostics).pack(fill="x", pady=2)

        # ---- 설정
        st = ttk.LabelFrame(self.root, text="3. 재생 설정")
        st.pack(fill="x", **pad)
        g = ttk.Frame(st)
        g.pack(fill="x", padx=8, pady=6)
        self.speed_var = tk.StringVar(value=str(self.settings["speed"]))
        self.delay_var = tk.StringVar(value=str(self.settings["extra_delay"]))
        self.gap_var = tk.StringVar(value=str(self.settings["max_gap"]))
        self.restore_var = tk.BooleanVar(value=bool(self.settings["restore_layout"]))
        ttk.Label(g, text="재생 속도(배)").grid(row=0, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.speed_var, width=6,
                     values=["0.5", "0.75", "1.0", "1.5", "2.0", "3.0"]).grid(row=0, column=1, padx=4)
        ttk.Label(g, text="동작마다 추가 대기(초)").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Spinbox(g, textvariable=self.delay_var, from_=0, to=10, increment=0.1, width=6).grid(row=0, column=3, padx=4)
        ttk.Label(g, text="긴 대기 최대(초, 0=제한없음)").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Spinbox(g, textvariable=self.gap_var, from_=0, to=120, increment=1, width=6).grid(
            row=1, column=2, sticky="w", padx=4, pady=(6, 0))
        ttk.Checkbutton(g, text="재생 후 저장된 창 배치 복원", variable=self.restore_var).grid(
            row=1, column=3, columnspan=2, sticky="w", pady=(6, 0))

        # ---- 진행
        pr = ttk.Frame(self.root)
        pr.pack(fill="x", padx=10, pady=(4, 10))
        self.progress = ttk.Progressbar(pr, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.btn_stop = ttk.Button(pr, text="⏹ 실행 중지 (F12)", style="Big.TButton", command=self.stop_playback)
        self.btn_stop.pack(side="left", padx=(8, 0))

    # ================================================================ 공통
    def _set_state(self, state):
        self.state = state
        idle = state == "idle"

        def en(widget, on):
            widget.configure(state="normal" if on else "disabled")

        en(self.btn_rec, idle)
        en(self.btn_rec_stop, state == "recording")
        en(self.btn_save, idle and self.pending is not None)
        for b in (self.btn_play, self.btn_layout, self.btn_update_layout, self.btn_rename, self.btn_delete):
            en(b, idle)
        en(self.btn_stop, state in ("countdown", "playing"))
        self.kb_combo.configure(state="readonly" if idle else "disabled")

    def _status(self, text):
        self.status_var.set(text)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "status":
                    self._status(payload)
                elif kind == "progress":
                    done, total = payload
                    self.progress.configure(maximum=max(total, 1), value=done)
                elif kind == "hotkey":
                    self._on_record_hotkey(payload)
                elif kind == "play_done":
                    self._on_play_done(payload)
                elif kind == "stop_play":
                    self.stop_playback()
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _read_settings(self):
        def num(var, default, lo, hi):
            try:
                v = float(var.get())
            except (TypeError, ValueError):
                v = default
            return min(max(v, lo), hi)

        self.settings["speed"] = num(self.speed_var, 1.0, 0.1, 10.0)
        self.settings["extra_delay"] = num(self.delay_var, 0.0, 0.0, 30.0)
        self.settings["max_gap"] = num(self.gap_var, 10.0, 0.0, 600.0)
        self.settings["restore_layout"] = bool(self.restore_var.get())
        label = self.kb_var.get()
        self.settings["keyboard_mode"] = next((k for k, l in KEYBOARD_MODES if l == label), "all")
        for var, key in ((self.speed_var, "speed"), (self.delay_var, "extra_delay"), (self.gap_var, "max_gap")):
            var.set(str(self.settings[key]))
        try:
            self.store.save_settings(self.settings)
        except OSError:
            pass
        return self.settings

    def selected_name(self):
        sel = self.tree.selection()
        return self.tree.item(sel[0], "text") if sel else None

    def refresh_list(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for name, path, info in self.store.list_macros():
            if "error" in info:
                vals = ("오류", "-", "-", info["error"][:30])
            else:
                vals = (info["events"], info["windows"], info["duration"], info["updated"])
            iid = self.tree.insert("", "end", text=name, values=vals)
            if name == select:
                self.tree.selection_set(iid)
                self.tree.see(iid)
        if not self.tree.selection() and self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])

    def _need_selection(self):
        name = self.selected_name()
        if not name:
            messagebox.showinfo("안내", "목록에서 매크로를 먼저 선택하세요.", parent=self.root)
        return name

    # ================================================================ 기록
    def start_recording(self):
        if self.state != "idle":
            return
        if self.pending is not None and not messagebox.askyesno(
                "확인", "저장하지 않은 기록이 있습니다. 버리고 새로 기록할까요?", parent=self.root):
            return
        s = self._read_settings()
        self.pending = None
        self.recorder = Recorder(self.api, keyboard_mode=s["keyboard_mode"],
                                 sensitive_keywords=s.get("sensitive_keywords"),
                                 on_hotkey=lambda name: self.queue.put(("hotkey", name)))
        try:
            self.recorder.start(use_listeners=self.recorder_listeners)
        except Exception as exc:
            self.recorder = None
            messagebox.showerror("오류", f"기록을 시작할 수 없습니다.\n{exc}", parent=self.root)
            return
        self.root.attributes("-topmost", True)  # 작은 창이 항상 보이도록 (이 창의 클릭은 기록 안 됨)
        self._set_state("recording")
        self._status("● 기록 중... 평소처럼 작업하세요  (F9 일시정지 / F10 종료)")

    def _on_record_hotkey(self, name):
        if self.state != "recording" or not self.recorder:
            return
        if name == HOTKEY_STOP_RECORD:
            self.stop_recording()
        elif name == HOTKEY_PAUSE:
            if self.recorder.paused:
                self._status("⏸ 기록 일시정지 중 - 입력이 기록되지 않습니다 (F9: 재개)")
            else:
                self._status("● 기록 중... (F9 일시정지 / F10 종료)")

    def stop_recording(self):
        if self.state != "recording" or not self.recorder:
            return
        events = self.recorder.stop()
        stats = self.recorder.stats
        self.recorder = None
        self.root.attributes("-topmost", False)
        self.root.deiconify()
        procs = self.settings["browser_processes"]
        layout = layout_mod.capture_layout(self.api, procs) if self.api.supported else []
        monitors = self.api.get_monitors()
        self._set_state("idle")

        if not [e for e in events if e["type"] not in ("privacy_gap", "masked")] and not layout:
            self._status("기록된 동작이 없습니다.")
            messagebox.showinfo("안내", "기록된 동작이 없습니다.", parent=self.root)
            return

        segments = privacy.extract_text_segments(events)
        if segments:
            dlg = ReviewDialog(self.root, segments)
            self.root.wait_window(dlg)
            if dlg.result is None:
                self._status("기록을 버렸습니다.")
                return
            events = privacy.remove_segments(events, dlg.result)

        self.pending = {"events": events, "layout": layout, "monitors": monitors,
                        "extra": {"recorded_with": {"keyboard_mode": self.settings["keyboard_mode"],
                                                    "dpi_mode": self.dpi_mode,
                                                    "masked_keys": stats["masked_keys"]}}}
        msg = f"기록 완료: 동작 {len(events)}개, 창 {len(layout)}개"
        if stats["masked_keys"]:
            msg += f" (민감 페이지 키 입력 {stats['masked_keys']}개 제외)"
        self._status(msg)
        self._set_state("idle")
        self.save_pending()

    def save_pending(self):
        if self.pending is None:
            return
        while True:
            name = simpledialog.askstring("매크로 저장", "매크로 이름을 입력하세요 (예: 주간근무 화면):",
                                          parent=self.root)
            if name is None:
                self._status("저장하지 않았습니다. '매크로 저장' 버튼으로 나중에 저장할 수 있습니다.")
                self._set_state("idle")
                return
            name = name.strip()
            if not name:
                continue
            overwrite = False
            if self.store.exists(name):
                if not messagebox.askyesno("확인", f"'{name}' 이(가) 이미 있습니다. 덮어쓸까요?", parent=self.root):
                    continue
                overwrite = True
            try:
                p = self.pending
                self.store.save(name, p["events"], p["layout"], p["monitors"], p["extra"], overwrite=overwrite)
            except OSError as exc:
                messagebox.showerror("저장 실패", str(exc), parent=self.root)
                return
            break
        self.pending = None
        self.refresh_list(select=name)
        self._status(f"'{name}' 저장 완료")
        self._set_state("idle")

    # ================================================================ 재생
    def _load_selected(self):
        name = self._need_selection()
        if not name:
            return None, None
        try:
            return name, self.store.load(name)
        except Exception as exc:
            messagebox.showerror("불러오기 실패", f"매크로 파일을 읽을 수 없습니다.\n{exc}", parent=self.root)
            return None, None

    def _check_monitors(self, macro):
        diffs = layout_mod.compare_monitors(macro.get("monitors"), self.api.get_monitors())
        if diffs:
            return messagebox.askyesno(
                "모니터 구성 경고",
                "화면 해상도 또는 모니터 구성이 기록 당시와 다릅니다.\n\n" + "\n".join(diffs) +
                "\n\n클릭 위치가 어긋날 수 있습니다. 그래도 실행할까요?", icon="warning", parent=self.root)
        return True

    def play_selected(self):
        if self.state != "idle":
            return
        name, macro = self._load_selected()
        if not macro or not self._check_monitors(macro):
            return
        s = self._read_settings()
        try:
            backend = self.backend_factory() if self.backend_factory else None
        except Exception as exc:
            messagebox.showerror("오류", f"입력 장치를 제어할 수 없습니다.\n{exc}", parent=self.root)
            return
        self.player = Player(macro, backend, self.api, speed=s["speed"], extra_delay=s["extra_delay"],
                             max_gap=s["max_gap"], restore_layout=s["restore_layout"],
                             processes=s["browser_processes"], countdown=0,
                             on_status=lambda m: self.queue.put(("status", m)),
                             on_progress=lambda d, t: self.queue.put(("progress", (d, t))))
        self.progress.configure(value=0, maximum=max(len(self.player.events), 1))
        self.playing_name = name
        self._start_hotkey_listener()
        self._set_state("countdown")
        self._countdown(3)

    def _countdown(self, n):
        if self.state != "countdown":
            return
        if n > 0:
            est = self.player.estimated_duration()
            self._status(f"{n}초 후 '{self.playing_name}' 실행 (예상 {est:.0f}초)  -  F12 / 중지 버튼으로 취소")
            self.countdown_job = self.root.after(1000, self._countdown, n - 1)
            return
        self.countdown_job = None
        self._set_state("playing")
        self.root.iconify()  # 재생 클릭이 이 창에 가로막히지 않도록 최소화
        self.root.after(400, self._launch_player)

    def _launch_player(self):
        if self.state != "playing" or not self.player:
            return
        player = self.player

        def work():
            result = player.run()
            self.queue.put(("play_done", result))

        self.play_thread = threading.Thread(target=work, daemon=True)
        self.play_thread.start()

    def _start_hotkey_listener(self):
        try:
            self.hotkey_listener = self.hotkey_listener_factory(lambda: self.queue.put(("stop_play", None)))
            if self.hotkey_listener:
                self.hotkey_listener.start()
        except Exception:
            self.hotkey_listener = None  # 단축키 없이도 '중지' 버튼은 동작

    def _stop_hotkey_listener(self):
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
        self.hotkey_listener = None

    def stop_playback(self):
        if self.state == "countdown":
            if self.countdown_job:
                self.root.after_cancel(self.countdown_job)
                self.countdown_job = None
            self._stop_hotkey_listener()
            self.player = None
            self._set_state("idle")
            self._status("실행을 취소했습니다.")
        elif self.state == "playing" and self.player:
            self.player.stop()
            self._status("중지하는 중...")

    def _on_play_done(self, result):
        self._stop_hotkey_listener()
        self.player = None
        self.root.deiconify()
        self.root.lift()
        self._set_state("idle")
        lines = [f"동작 {result['done']}/{result['total']}개 실행"]
        if result.get("window_events"):
            lines.append(f"창 이동/크기 변경 {result['window_events']}건 적용")
        rep = result.get("layout_report")
        if rep:
            lines.append(self._layout_summary(rep))
        if result.get("error"):
            self._status("오류로 중단되었습니다.")
            messagebox.showerror("실행 오류", "\n".join(lines + ["", result["error"]]), parent=self.root)
        elif result.get("stopped"):
            self._status("사용자가 중지했습니다. (" + lines[0] + ")")
        else:
            self._status("실행 완료  -  " + " / ".join(lines[:2]))
            if rep and rep.get("missing"):
                messagebox.showwarning("창 배치", "\n".join(lines), parent=self.root)

    @staticmethod
    def _layout_summary(rep):
        if rep.get("error"):
            return rep["error"]
        s = f"창 배치 복원: {rep['moved']}/{rep['total']}개"
        if rep.get("missing"):
            s += "\n찾지 못한 창:\n - " + "\n - ".join(t[:60] for t in rep["missing"][:10])
        return s

    # ================================================================ 기타 기능
    def restore_layout_only(self):
        name, macro = self._load_selected()
        if not macro:
            return
        if not macro.get("layout"):
            messagebox.showinfo("안내", "이 매크로에는 저장된 창 배치가 없습니다.", parent=self.root)
            return
        if not self._check_monitors(macro):
            return
        rep = layout_mod.restore_layout(self.api, macro["layout"], self.settings["browser_processes"])
        msg = self._layout_summary(rep)
        self._status(msg.splitlines()[0])
        messagebox.showinfo("창 배치 복원", msg, parent=self.root)

    def update_layout(self):
        name, macro = self._load_selected()
        if not macro:
            return
        if not self.api.supported:
            messagebox.showinfo("안내", "창 배치 기능은 Windows 에서만 동작합니다.", parent=self.root)
            return
        layout = layout_mod.capture_layout(self.api, self.settings["browser_processes"])
        if not messagebox.askyesno("확인", f"현재 열린 브라우저 창 {len(layout)}개의 배치로 '{name}' 의 창 배치를 바꿀까요?",
                                   parent=self.root):
            return
        macro["layout"] = layout
        macro["monitors"] = self.api.get_monitors()
        self.store.update(name, macro)
        self.refresh_list(select=name)
        self._status(f"'{name}' 창 배치 갱신 ({len(layout)}개)")

    def rename_selected(self):
        name = self._need_selection()
        if not name:
            return
        new = simpledialog.askstring("이름 변경", "새 이름:", initialvalue=name, parent=self.root)
        if not new or not new.strip() or new.strip() == name:
            return
        try:
            self.store.rename(name, new)
        except FileExistsError:
            messagebox.showerror("오류", "같은 이름의 매크로가 이미 있습니다.", parent=self.root)
            return
        except Exception as exc:
            messagebox.showerror("오류", str(exc), parent=self.root)
            return
        self.refresh_list(select=new.strip())
        self._status(f"이름 변경: {name} → {new.strip()}")

    def delete_selected(self):
        name = self._need_selection()
        if not name:
            return
        if messagebox.askyesno("삭제 확인", f"'{name}' 매크로를 삭제할까요?\n(되돌릴 수 없습니다)",
                               icon="warning", parent=self.root):
            try:
                self.store.delete(name)
            except OSError as exc:
                messagebox.showerror("오류", str(exc), parent=self.root)
            self.refresh_list()
            self._status(f"'{name}' 삭제됨")

    def open_folder(self):
        path = self.store.macro_dir
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - 탐색기로 로컬 폴더 열기
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            messagebox.showinfo("저장 폴더", path, parent=self.root)

    def diagnostics_text(self):
        lines = [f"운영체제: {sys.platform}   Python {sys.version.split()[0]}",
                 f"DPI 모드: {self.dpi_mode}",
                 f"저장 폴더: {self.store.macro_dir}", ""]
        mons = self.api.get_monitors()
        lines.append(f"모니터 {len(mons)}대:")
        for m in mons:
            r = m["rect"]
            lines.append(f"  {m.get('device', '')} {r[2]-r[0]}x{r[3]-r[1]} 위치({r[0]},{r[1]})"
                         + (" [주 모니터]" if m.get("primary") else ""))
        wins = self.api.list_windows(self.settings["browser_processes"]) if self.api.supported else []
        lines.append("")
        lines.append(f"감지된 브라우저 창 {len(wins)}개 ({', '.join(self.settings['browser_processes'])}):")
        for w in wins[:30]:
            r = w["rect"]
            lines.append(f"  [{w['state']}] {r} {w['title'][:50]}")
        if not self.api.supported:
            lines.append("  (Windows 가 아니어서 창 기능을 사용할 수 없습니다)")
        return "\n".join(lines)

    def show_diagnostics(self):
        top = tk.Toplevel(self.root)
        top.title("환경 진단")
        txt = tk.Text(top, width=90, height=24)
        txt.insert("1.0", self.diagnostics_text())
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(top, text="닫기", command=top.destroy).pack(pady=(0, 8))

    def on_close(self):
        if self.state == "recording" and self.recorder:
            self.recorder.stop()
        if self.player:
            self.player.stop()
            if self.play_thread:
                self.play_thread.join(timeout=2.0)  # 눌린 키/버튼 해제가 끝날 때까지 대기
        self._stop_hotkey_listener()
        self.root.destroy()


def run_app(store, api, dpi_mode):
    root = tk.Tk()
    try:
        from .player import PynputBackend
        backend_factory = PynputBackend
    except Exception:  # pragma: no cover
        backend_factory = None
    App(root, store, api, backend_factory=backend_factory, dpi_mode=dpi_mode)
    root.mainloop()
