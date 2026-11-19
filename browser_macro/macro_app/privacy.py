"""민감정보(비밀번호, 인증번호 등) 기록 방지 도구.

방어는 여러 겹으로 이루어집니다.
 1) 기록 중: 전경 창 제목에 '로그인/비밀번호/인증' 등 키워드가 있으면 키 입력을 아예 기록하지 않음
 2) 기록 중: F9 로 기록 일시정지 → 그 사이 입력은 메모리에도 남지 않음
 3) 기록 종료 후 저장 전: 입력된 문자열을 검토 화면에 보여 주고,
    비밀번호/인증번호처럼 보이는 문자열은 '삭제'가 기본 선택됨
 4) 파일에는 사용자가 검토를 마친 결과만 저장됨
"""
from __future__ import annotations

import re

DEFAULT_SENSITIVE_KEYWORDS = [
    "로그인", "비밀번호", "패스워드", "암호", "인증", "본인확인", "보안카드", "otp",
    "login", "log in", "sign in", "signin", "password", "passcode", "verification",
    "2-step", "two-factor", "2fa", "mfa", "certificate",
]

MODIFIER_NAMES = {"shift", "shift_l", "shift_r", "ctrl", "ctrl_l", "ctrl_r",
                  "alt", "alt_l", "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r"}
COMMAND_MODIFIERS = {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr",
                     "cmd", "cmd_l", "cmd_r"}
# '단축키만' 모드에서 단독으로도 기록하는 특수키
SAFE_SPECIAL_KEYS = {"enter", "tab", "esc", "up", "down", "left", "right", "page_up",
                     "page_down", "home", "end", "f1", "f2", "f3", "f4", "f5", "f6",
                     "f7", "f8", "f11", "delete", "backspace", "space"}
SEGMENT_BREAK_KEYS = {"enter", "tab", "esc", "up", "down", "page_up", "page_down"}


def is_sensitive_title(title: str, keywords=None) -> bool:
    t = (title or "").lower()
    return any(k.lower() in t for k in (keywords or DEFAULT_SENSITIVE_KEYWORDS))


def suspicious_reason(text: str):
    """문자열이 비밀번호/인증번호처럼 보이면 이유를, 아니면 None 을 반환."""
    s = text.strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4,8}", s):
        return "인증번호(숫자 4~8자리)로 보임"
    if re.fullmatch(r"\d{6}[- ]?\d{7}", s):
        return "주민등록번호 형식"
    if re.fullmatch(r"(\d{4}[- ]?){3}\d{4}", s):
        return "카드번호 형식"
    looks_like_url = bool(re.search(r"(://|^www\.|\.[a-z]{2,}(/|$)|^[\w.-]+/)", s, re.I))
    if " " not in s and len(s) >= 6 and not looks_like_url:
        classes = sum(bool(re.search(p, s)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]"))
        if classes >= 3:
            return "비밀번호처럼 보임(문자/숫자/기호 혼합)"
        if classes >= 2 and re.search(r"\d", s) and re.search(r"[^\w\s]", s):
            return "비밀번호처럼 보임(숫자+기호)"
    return None


def mask_text(text: str) -> str:
    if len(text) <= 2:
        return "*" * len(text)
    return text[0] + "*" * (len(text) - 2) + text[-1]


def _key_id(ev):
    return ev.get("name") or ev.get("vk") or ev.get("char")


def extract_text_segments(events):
    """키 입력 이벤트에서 '타이핑한 문자열' 구간을 추출한다.

    반환: [{"text", "indices"(구간을 지울 때 함께 지울 이벤트 인덱스), "reason"}]
    Ctrl/Alt 를 누른 상태의 키(단축키)는 문자열로 보지 않는다.
    """
    segments = []
    cur = None
    held_cmd = set()
    char_downs = {}  # key id -> segment (해당 키의 key_up 도 같이 지우기 위함)

    def close():
        nonlocal cur
        if cur and cur["indices"]:
            cur["reason"] = suspicious_reason(cur["text"])
            segments.append(cur)
        cur = None

    for idx, ev in enumerate(events):
        typ = ev.get("type")
        if typ in ("mouse_down", "privacy_gap", "masked"):
            close()
            continue
        if typ not in ("key_down", "key_up"):
            continue
        name = ev.get("name")
        kid = _key_id(ev)
        if typ == "key_up":
            if name in COMMAND_MODIFIERS:
                held_cmd.discard(name)
            seg = char_downs.pop(kid, None)
            if seg is not None:
                seg["indices"].append(idx)
            continue
        # key_down
        if name in COMMAND_MODIFIERS:
            held_cmd.add(name)
            continue
        if name in MODIFIER_NAMES:
            continue
        if held_cmd:  # Ctrl+C 같은 단축키
            close()
            continue
        if name in SEGMENT_BREAK_KEYS:
            close()
            continue
        if name == "backspace":
            if cur:
                cur["text"] = cur["text"][:-1]
                cur["indices"].append(idx)
                char_downs[kid] = cur
            continue
        ch = " " if name == "space" else ev.get("char")
        if ch and ch.isprintable():
            if cur is None:
                cur = {"text": "", "indices": []}
            cur["text"] += ch
            cur["indices"].append(idx)
            char_downs[kid] = cur
        else:
            close()
    close()
    return segments


def remove_segments(events, segments):
    """선택된 구간의 이벤트를 제거하고, 제거 위치에 'redacted' 표시를 남긴다."""
    drop = set()
    marks = {}
    for seg in segments:
        drop.update(seg["indices"])
        if seg["indices"]:
            marks[min(seg["indices"])] = len(seg["text"])
    out = []
    for idx, ev in enumerate(events):
        if idx in marks:
            out.append({"t": ev["t"], "type": "redacted", "chars": marks[idx]})
        if idx not in drop:
            out.append(ev)
    return out


def strip_all_text(events):
    return remove_segments(events, extract_text_segments(events))


def is_shortcut_allowed(name, char, held_modifiers):
    """'단축키만' 모드 판정: 일반 문자 타이핑은 기록하지 않는다."""
    if name in MODIFIER_NAMES:
        return True
    if held_modifiers & COMMAND_MODIFIERS:
        return True
    if name in SAFE_SPECIAL_KEYS:
        return True
    return False
