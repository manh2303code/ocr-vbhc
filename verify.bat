@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  Kiem tra tinh toan ven cua package...
echo.

set PYTHON="%~dp0runtime\python.exe"
set ERRORS=0
set SKIPPED=0

rem A --profile runtime package leaves gguf\, bin\ and the model directories out
rem on purpose. Every gate starts at "check", so a package built before
rem BUILD_MANIFEST.json existed - or one whose manifest cannot be read - is
rem verified exactly as it always was.
set "PROFILE=full"
set "CHECK_GGUF=1"
set "CHECK_BIN=1"
set "CHECK_MODEL_EMBEDDING=1"
set "CHECK_MODEL_RERANKER=1"
set "CHECK_POPPLER=1"
set "NOTE_GGUF="
set "NOTE_BIN="
set "NOTE_MODEL_EMBEDDING="
set "NOTE_MODEL_RERANKER="
set "NOTE_POPPLER="

rem verify_plan.py reads BUILD_MANIFEST.json and answers CHECK or SKIP per
rem component. Relative paths only: the cd above already moved us into the
rem package, so an install path containing spaces needs no quoting in here.
for /f "usebackq tokens=1,2,* delims=|" %%a in (`runtime\python.exe verify_plan.py 2^>nul`) do (
    if /i "%%a"=="PROFILE" set "PROFILE=%%b"
    if /i "%%a"=="SKIP" set "CHECK_%%b=0"
    if /i "%%a"=="SKIP" set "NOTE_%%b=%%c"
    if /i "%%a"=="SKIP" set /a SKIPPED+=1
)

echo  Build profile: %PROFILE%
echo.

if not exist "runtime\python.exe" (
    echo [X] runtime\python.exe - KHONG TIM THAY
    set /a ERRORS+=1
) else (
    echo [OK] runtime\python.exe
)

for %%f in (main.py startup_controller.py landing_data.py landing_page.py font_loader.py ui_commands.py workspace_layout.py main_window_1.py editor_widgets.py ocr_pipeline.py document_repository.py background_workers.py chat_panel.py chat_widgets.py doc_resolver.py metadata_query.py answer_check.py logging_config.py torch_compat.py verify_plan.py tools\reocr_documents.py theme.qss theme_dark.qss frontend\landing\index.html frontend\landing\landing.css frontend\landing\landing.js assets\app_icon.ico assets\app_icon_256.png assets\fonts\BeVietnamPro-Regular.ttf assets\fonts\BeVietnamPro-Medium.ttf assets\fonts\BeVietnamPro-SemiBold.ttf assets\fonts\BeVietnamPro-Bold.ttf) do (
    if not exist "%%f" (
        echo [X] %%f - KHONG TIM THAY
        set /a ERRORS+=1
    ) else (
        echo [OK] %%f
    )
)

if "%CHECK_MODEL_EMBEDDING%"=="1" (
    if not exist "model_embedding\config.json" (
        echo [X] model_embedding\ - THIEU FILE MODEL
        set /a ERRORS+=1
    ) else (
        echo [OK] model_embedding\
    )
) else (
    echo [BO QUA] model_embedding - %NOTE_MODEL_EMBEDDING%
)

if "%CHECK_MODEL_RERANKER%"=="1" (
    if not exist "model_reranker\config.json" (
        echo [X] model_reranker\ - THIEU FILE MODEL
        set /a ERRORS+=1
    ) else (
        echo [OK] model_reranker\
    )
) else (
    echo [BO QUA] model_reranker - %NOTE_MODEL_RERANKER%
)

for %%f in (runtime\Lib\site-packages\PyQt5\Qt5\bin\QtWebEngineProcess.exe runtime\Lib\site-packages\PyQt5\Qt5\resources\qtwebengine_resources.pak runtime\Lib\site-packages\PyQt5\Qt5\resources\icudtl.dat runtime\Lib\site-packages\PyQt5\Qt5\translations\qtwebengine_locales\en-US.pak) do (
    if not exist "%%f" (
        echo [X] %%f - THIEU QT WEBENGINE RUNTIME
        set /a ERRORS+=1
    ) else (
        echo [OK] %%f
    )
)

if "%CHECK_GGUF%"=="1" (
    if not exist "gguf\chandra\chandra-ocr-2-q4_k_m.gguf" (
        echo [X] Chandra Q4 model - THIEU
        set /a ERRORS+=1
    ) else (
        echo [OK] Chandra Q4 model
    )
    if not exist "gguf\chandra\chandra-ocr-2.mmproj-q8_0.gguf" (
        echo [X] Chandra vision projector - THIEU
        set /a ERRORS+=1
    ) else (
        echo [OK] Chandra vision projector
    )
    if not exist "gguf\chat\Qwen3-4B-Instruct-2507-Q4_K_M.gguf" (
        echo [X] Qwen3 4B chat model - THIEU
        set /a ERRORS+=1
    ) else (
        echo [OK] Qwen3 4B chat model
    )
    if not exist "gguf\chat\Qwen3-8B-Q4_K_M.gguf" (
        echo [X] Qwen3 8B chat model - THIEU
        set /a ERRORS+=1
    ) else (
        echo [OK] Qwen3 8B chat model
    )
) else (
    echo [BO QUA] gguf - %NOTE_GGUF%
)

rem bin\ has never had a file-level check. Report it only when the manifest says
rem it was left out, so the operator learns why local inference will not start.
if not "%CHECK_BIN%"=="1" echo [BO QUA] bin - %NOTE_BIN%

if "%CHECK_POPPLER%"=="1" (
    if not exist "tools\poppler\Library\bin\pdftoppm.exe" (
        echo [X] tools\poppler - KHONG TIM THAY
        set /a ERRORS+=1
    ) else (
        echo [OK] tools\poppler
    )
) else (
    echo [BO QUA] poppler - %NOTE_POPPLER%
)

%PYTHON% -c "import torch; import sentence_transformers; import PyQt5; from PyQt5.QtWebEngineWidgets import QWebEngineView; import fitz; import numpy; import sqlite_vec; print('[OK] Python packages verified (Qt WebEngine + sqlite-vec included)')" 2>nul
if %errorlevel% neq 0 (
    echo [X] Python packages - THIEU THU VIEN
    set /a ERRORS+=1
)

echo.
rem The exit code has to separate a valid reduced package from a damaged one:
rem 0 complete, 10 valid but short of components it never claimed to carry,
rem 1 something the manifest declares present is missing. Damage uses a fixed
rem code because an error count of 10 would read as a clean runtime build.
set "RC=0"
if %SKIPPED% gtr 0 set "RC=10"
if %ERRORS% gtr 0 set "RC=1"

if %ERRORS% gtr 0 (
    echo  [LOI] Co %ERRORS% van de can xu ly.
    echo  Package co the khong chay dung.
) else if %SKIPPED% gtr 0 (
    echo  ========================================
    echo   Package HOP LE theo profile %PROFILE%
    echo   Thieu %SKIPPED% thanh phan - xem cac dong [BO QUA] o tren
    echo   Bo sung tu asset bundle truoc khi chay OCR hoac chat
    echo  ========================================
    type VERSION.txt 2>nul
) else (
    echo  ========================================
    echo   Package HOAN CHINH - san sang su dung
    echo  ========================================
    type VERSION.txt 2>nul
)
echo.
pause
exit /b %RC%
