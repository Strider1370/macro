@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements-dev.txt || goto :err
python -m PyInstaller --noconfirm --onefile --windowed --name BrowserLayoutMacro ^
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
  main.py || goto :err
echo.
echo 완료: dist\BrowserLayoutMacro.exe
echo (매크로는 EXE 옆 data\macros 폴더에 저장됩니다)
pause
exit /b 0
:err
echo 빌드 실패
pause
exit /b 1
