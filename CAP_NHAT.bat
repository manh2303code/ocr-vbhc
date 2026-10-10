@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Thin wrapper. All decisions live in app_updater.py so they are testable;
REM this file only collects the patch folder and reports the outcome.

set PYTHON="%~dp0runtime\python.exe"

if "%~1"=="" (
    echo.
    echo  Cach dung: CAP_NHAT.bat "duong dan den thu muc ban va"
    echo.
    echo  Thu muc ban va la thu muc da giai nen tu USB, ben trong co
    echo  BUILD_MANIFEST.json va SHA256SUMS.
    echo.
    pause
    exit /b 2
)

if not exist "runtime\python.exe" (
    echo  [X] Khong tim thay runtime\python.exe - chay tu dung thu muc cai dat.
    pause
    exit /b 3
)

echo.
echo  Dang kiem tra va ap dung ban va...
echo.

%PYTHON% -c "import sys; from pathlib import Path; import app_updater; r = app_updater.apply_patch(Path(sys.argv[1]), Path('.')); print(('[OK] Da cap nhat.' if r.applied else '[LOI] Khong ap dung duoc:')); [print('   - ' + p) for p in r.problems]; print('   Ban cu duoc giu tai: ' + str(r.backup)) if r.backup else None; sys.exit(0 if r.applied else 1)" %1

set RESULT=%errorlevel%

echo.
if %RESULT% equ 0 (
    echo  Chay verify.bat de kiem tra lai goi sau khi cap nhat.
) else (
    echo  He thong KHONG bi thay doi. Xu ly cac muc tren roi thu lai.
)
echo.
pause
exit /b %RESULT%
