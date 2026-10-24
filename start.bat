@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
title OCR VBHC - Khoi dong

echo.
echo  ================================================================
echo    OCR VBHC Portable
echo  ================================================================
echo.

rem --- 1. Locate embedded Python runtime ---
set "PYTHON=%~dp0runtime\python.exe"
if not exist "%PYTHON%" goto no_runtime
set "PYTHONPATH=%~dp0"
echo  [1/4] Python: runtime\python.exe (3.9.13 embedded)

rem --- 2. Add Poppler to PATH for pdf2image ---
set "POPPLER=%~dp0tools\poppler\Library\bin"
if exist "%POPPLER%" set "PATH=%POPPLER%;%PATH%"
if exist "%POPPLER%" echo  [2/4] Poppler: OK
if not exist "%POPPLER%" echo  [2/4] Poppler: [!] khong tim thay

rem --- 3. GPU detection ---
rem _detect_gpu.py writes "cuda" or "cpu" to _gpu_result.txt
"%PYTHON%" "%~dp0_detect_gpu.py" >nul 2>&1
set "OCR_DEVICE=cpu"
if exist "%~dp0_gpu_result.txt" set /p OCR_DEVICE=<"%~dp0_gpu_result.txt"
if exist "%~dp0_gpu_result.txt" del "%~dp0_gpu_result.txt" >nul 2>&1

rem --- 4. Pick llama.cpp backend + prepend DLL folder to PATH ---
if /i "%OCR_DEVICE%"=="cuda" goto use_cuda
goto use_cpu

:use_cuda
set "LLAMA_BIN=%~dp0bin\cuda"
echo  [3/4] Backend: GPU (CUDA)
goto backend_done

:use_cpu
set "LLAMA_BIN=%~dp0bin\cpu"
echo  [3/4] Backend: CPU (xu ly se cham hon 5-10x)
goto backend_done

:backend_done
if exist "%LLAMA_BIN%" set "PATH=%LLAMA_BIN%;%PATH%"

rem --- 5. Default OCR model ---
if not defined OCR_MODEL set "OCR_MODEL=chandra-q4"
echo  [4/4] OCR model: %OCR_MODEL%

echo.
echo  Dang khoi dong ung dung...
echo  Luu y: lan chay dau tien mat 30-60 giay de load model.
echo.

"%PYTHON%" "%~dp0main.py"
set "RC=%errorlevel%"

if not "%RC%"=="0" goto app_error
goto end

:app_error
echo.
echo  ================================================================
echo   [LOI] Ung dung dong voi ma loi %RC%
echo   Xem chi tiet tai: logs\ocr_app.log
echo  ================================================================
echo.
pause
goto end

:no_runtime
echo  [LOI] Khong tim thay runtime\python.exe
echo        Thu muc cai dat co the bi loi hoac chua copy day du.
echo.
pause
goto end

:end
endlocal
