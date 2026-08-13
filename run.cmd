@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

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

if not defined PY (
  set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  echo [warn] PYTHON_EXE not found in .env, using default
)

if not exist "!PY!" (
  echo [FAIL] interpreter not found: !PY!
  exit /b 1
)

if "%~1"=="" (
  echo === environment check ===
  echo interpreter: !PY!
  "!PY!" "pipeline\doctor.py"
  exit /b %errorlevel%
)

"!PY!" %*
exit /b %errorlevel%