"""매크로/설정 파일 저장소 (모두 로컬 JSON 파일)."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import tempfile

FORMAT = "browser-layout-macro"
VERSION = 1

DEFAULT_SETTINGS = {
    "speed": 1.0,
    "extra_delay": 0.0,
    "max_gap": 10.0,
    "restore_layout": True,
    "keyboard_mode": "all",
    "browser_processes": ["chrome.exe"],
    "sensitive_keywords": None,   # None = 기본 키워드 사용
}


def app_dir():
    if getattr(sys, "frozen", False):          # PyInstaller EXE
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_data_dir():
    """프로그램 폴더의 data\\ 를 사용, 쓰기 불가하면 %LOCALAPPDATA% 사용."""
    candidate = os.path.join(app_dir(), "data")
    try:
        os.makedirs(candidate, exist_ok=True)
        probe = os.path.join(candidate, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return candidate
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "BrowserLayoutMacro")
        os.makedirs(path, exist_ok=True)
        return path


def now_str():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name.strip())
    name = name.rstrip(". ")
    if not name or name.upper() in {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} \
            | {f"LPT{i}" for i in range(1, 10)}:
        name = "_" + name
    return name[:80]


def _atomic_write_json(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class MacroStore:
    def __init__(self, data_dir=None):
        self.data_dir = data_dir or default_data_dir()
        self.macro_dir = os.path.join(self.data_dir, "macros")
        os.makedirs(self.macro_dir, exist_ok=True)
        self.settings_path = os.path.join(self.data_dir, "settings.json")

    # ---------------- 매크로
    def path_for(self, name):
        return os.path.join(self.macro_dir, safe_filename(name) + ".json")

    def exists(self, name):
        return os.path.exists(self.path_for(name))

    def list_macros(self):
        """[(name, path, summary)] - 손상된 파일은 summary['error'] 로 표시."""
        items = []
        for fn in sorted(os.listdir(self.macro_dir)):
            if not fn.lower().endswith(".json"):
                continue
            path = os.path.join(self.macro_dir, fn)
            try:
                m = self.load_path(path)
                events = m.get("events", [])
                items.append((m.get("name") or fn[:-5], path, {
                    "events": len(events),
                    "windows": len(m.get("layout") or []),
                    "duration": round(events[-1]["t"], 1) if events else 0.0,
                    "updated": m.get("updated", ""),
                }))
            except Exception as exc:
                items.append((fn[:-5], path, {"error": str(exc)}))
        items.sort(key=lambda it: it[0].lower())
        return items

    def load_path(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("이 프로그램의 매크로 파일이 아닙니다.")
        if int(data.get("version", 0)) > VERSION:
            raise ValueError("더 새로운 버전에서 만든 파일입니다.")
        if not isinstance(data.get("events"), list):
            raise ValueError("events 항목이 없습니다.")
        for ev in data["events"]:
            if not isinstance(ev, dict) or "t" not in ev or "type" not in ev:
                raise ValueError("잘못된 동작 데이터가 있습니다.")
        return data

    def load(self, name):
        return self.load_path(self.path_for(name))

    def save(self, name, events, layout=None, monitors=None, extra=None, overwrite=False):
        path = self.path_for(name)
        if os.path.exists(path) and not overwrite:
            raise FileExistsError(name)
        created = now_str()
        if os.path.exists(path):
            try:
                created = self.load_path(path).get("created", created)
            except Exception:
                pass
        data = {"format": FORMAT, "version": VERSION, "name": name.strip(),
                "created": created, "updated": now_str(),
                "monitors": monitors or [], "layout": layout or [], "events": events}
        if extra:
            data.update(extra)
        _atomic_write_json(path, data)
        return path

    def update(self, name, data):
        data["updated"] = now_str()
        _atomic_write_json(self.path_for(name), data)

    def delete(self, name):
        os.remove(self.path_for(name))

    def rename(self, old, new):
        new = new.strip()
        if not new:
            raise ValueError("새 이름이 비어 있습니다.")
        old_path, new_path = self.path_for(old), self.path_for(new)
        if os.path.normcase(old_path) != os.path.normcase(new_path) and os.path.exists(new_path):
            raise FileExistsError(new)
        data = self.load_path(old_path)
        data["name"] = new
        data["updated"] = now_str()
        _atomic_write_json(new_path, data)
        if os.path.normcase(old_path) != os.path.normcase(new_path):
            os.remove(old_path)
        return new_path

    # ---------------- 설정
    def load_settings(self):
        settings = dict(DEFAULT_SETTINGS)
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError):
            pass
        return settings

    def save_settings(self, settings):
        _atomic_write_json(self.settings_path,
                           {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()})
