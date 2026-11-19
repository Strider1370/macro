"""업무 화면 자동 배치 매크로 - 실행 파일.

실행:  python main.py
"""
import sys

# DPI 인식 설정은 다른 GUI/입력 라이브러리보다 반드시 먼저 해야 한다.
from macro_app.winapi import get_api, set_dpi_awareness

DPI_MODE = set_dpi_awareness()


def main():
    from macro_app.storage import MacroStore
    try:
        import pynput  # noqa: F401
    except ImportError:
        print("pynput 라이브러리가 필요합니다:  pip install -r requirements.txt")
        return 1
    from macro_app.gui import run_app
    run_app(MacroStore(), get_api(), DPI_MODE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
