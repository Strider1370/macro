"""브라우저 창 배치 저장/복원 및 모니터 구성 비교."""
from __future__ import annotations

import difflib
import re
import time

TITLE_SUFFIXES = re.compile(r"\s+[-–—]\s+(Google Chrome|Chrome|Chromium|Microsoft​? Edge)\s*$",
                            re.IGNORECASE)


def normalize_title(title: str) -> str:
    title = TITLE_SUFFIXES.sub("", title or "")
    # 알림 개수 "(3) 받은편지함" 처럼 자주 바뀌는 접두어 제거
    title = re.sub(r"^\(\d+\)\s*", "", title)
    return title.strip().lower()


def title_similarity(a: str, b: str) -> float:
    a, b = normalize_title(a), normalize_title(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- 저장
def capture_layout(api, processes):
    """현재 보이는 브라우저 창들의 위치/크기/상태 (Z-order 위쪽부터)."""
    layout = []
    for w in api.list_windows(processes):
        layout.append({
            "title": w["title"],
            "class": w["class"],
            "process": w["process"],
            "rect": w["rect"],
            "normal_rect": w.get("normal_rect"),
            "state": w["state"],
        })
    return layout


# ---------------------------------------------------------------- 매칭
def match_windows(saved, current, min_ratio=0.6, fallback_by_order=True):
    """저장된 창 목록과 현재 창 목록을 짝짓는다.

    1) 제목 유사도가 높은 순서대로 1:1 매칭 (같은 클래스/프로세스끼리만)
    2) 남은 창은 원래 순서대로 남은 현재 창과 매칭 (제목이 바뀌는 페이지 대비)

    반환: [(saved_idx, current_idx, method, score)], 매칭 안 된 saved_idx 목록
    """
    pairs = []
    for i, s in enumerate(saved):
        for j, c in enumerate(current):
            if s.get("process") and c.get("process") and s["process"] != c["process"]:
                continue
            score = title_similarity(s.get("title", ""), c.get("title", ""))
            if score >= min_ratio:
                pairs.append((score, i, j))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))

    used_s, used_c, result = set(), set(), []
    for score, i, j in pairs:
        if i in used_s or j in used_c:
            continue
        used_s.add(i)
        used_c.add(j)
        result.append((i, j, "title", round(score, 3)))

    if fallback_by_order:
        rest_c = [j for j in range(len(current)) if j not in used_c]
        for i in range(len(saved)):
            if i in used_s or not rest_c:
                continue
            # 같은 프로세스의 남은 창 중 가장 앞의 것
            for k, j in enumerate(rest_c):
                if saved[i].get("process") in (None, "", current[j].get("process")):
                    used_s.add(i)
                    result.append((i, j, "order", 0.0))
                    rest_c.pop(k)
                    break

    result.sort(key=lambda r: r[0])
    missing = [i for i in range(len(saved)) if i not in used_s]
    return result, missing


# ---------------------------------------------------------------- 복원
def restore_layout(api, saved, processes, fallback_by_order=True, passes=2,
                   sleep=time.sleep, should_stop=lambda: False):
    """저장된 창 배치를 현재 창들에 적용한다.

    DPI 가 다른 모니터로 창을 옮기면 Chrome 이 크기를 다시 계산하는 경우가 있어
    같은 배치를 2회 적용한다.
    """
    report = {"supported": getattr(api, "supported", False), "total": len(saved),
              "moved": 0, "missing": [], "details": []}
    if not report["supported"]:
        report["error"] = "이 운영체제에서는 창 배치 기능을 지원하지 않습니다(Windows 전용)."
        return report
    current = api.list_windows(processes)
    matches, missing = match_windows(saved, current, fallback_by_order=fallback_by_order)
    report["missing"] = [saved[i]["title"] for i in missing]

    # Z-order 아래쪽 창부터 적용 → 마지막에 맨 위 창이 위로 오도록
    ordered = sorted(matches, key=lambda m: -m[0])
    for p in range(passes):
        if should_stop():
            break
        for i, j, method, score in ordered:
            s, c = saved[i], current[j]
            ok = api.apply_placement(c["hwnd"], s["rect"], s["state"], s.get("normal_rect"),
                                     bring_to_top=(p == passes - 1))
            if p == passes - 1:
                report["details"].append({"saved": s["title"], "window": c["title"],
                                          "method": method, "score": score, "ok": ok})
                if ok:
                    report["moved"] += 1
        if p < passes - 1:
            sleep(0.3)
    return report


def apply_window_event(api, ev, processes=None):
    """재생 중 '창 이동/크기 변경' 이벤트 적용 (방금 조작한 창 = 전경 창)."""
    if not getattr(api, "supported", False):
        return False
    fg = api.foreground_window()
    target = None
    if fg and fg.get("class") == ev.get("class") and fg.get("process") == ev.get("process"):
        target = fg
    else:
        best = 0.0
        for w in api.list_windows([ev["process"]] if ev.get("process") else processes):
            sc = title_similarity(w["title"], ev.get("title", ""))
            if sc > best and sc >= 0.8:
                best, target = sc, w
    if not target:
        return False
    if target["rect"] == ev["rect"] and target["state"] == ev["state"]:
        return True
    return api.apply_placement(target["hwnd"], ev["rect"], ev["state"], ev.get("normal_rect"))


# ---------------------------------------------------------------- 모니터
def compare_monitors(saved, current):
    """모니터 구성이 다르면 사람이 읽을 수 있는 차이 설명 목록을 반환."""
    if not saved:
        return []
    diffs = []
    if len(saved) != len(current):
        diffs.append(f"모니터 개수가 다릅니다 (기록 당시 {len(saved)}대 → 현재 {len(current)}대).")
    s_rects = sorted(tuple(m["rect"]) for m in saved)
    c_rects = sorted(tuple(m["rect"]) for m in current)
    if len(saved) == len(current) and s_rects != c_rects:
        diffs.append("모니터 해상도 또는 배치(위치)가 기록 당시와 다릅니다.")
    for name, mons in (("기록 당시", saved), ("현재", current)):
        desc = ", ".join(f"{m['rect'][2]-m['rect'][0]}x{m['rect'][3]-m['rect'][1]}"
                         f"@({m['rect'][0]},{m['rect'][1]})" + ("*" if m.get("primary") else "")
                         for m in mons) or "알 수 없음"
        if diffs:
            diffs.append(f"  {name}: {desc}")
    return diffs
