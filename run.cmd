@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY="

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    set "K=%%A"
    set "K=!K: =!"
    if /i "!K!"=="PYTHON_EXE" (
      set "V=%%B"
      set "V=!V:"=!"
      set "V=!V:'=!"
      for /f "tokens=* delims= " %%C in ("!V!") do set "PY=%%C"
    )
  )
)

if not defined PY set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not exist "!PY!" (
  echo [FAIL] interpreter not found: !PY!
  exit /b 1
)

set "A1=%~1"
if "!A1!"=="" goto :doctor
if /i "!A1!"=="--out" goto :tofile

"!PY!" %*
exit /b %errorlevel%

:doctor
echo === environment check ===
echo interpreter: !PY!
"!PY!" "pipeline\doctor.py"
exit /b %errorlevel%

:tofile
set "OUTFILE=%~2"
shift
shift
"!PY!" %1 %2 %3 %4 %5 %6 %7 %8 %9 > "!OUTFILE!" 2>&1
set "RC=!errorlevel!"
echo output written to !OUTFILE!
exit /b !RC!