@echo off
rem One-click state check: output goes to logs\state_check.txt
cd /d "%~dp0"
"C:\Users\Evgeny.Rybakov\AppData\Local\Programs\Python\Python312\python.exe" pipeline\check_state.py > logs\state_check.txt 2>&1
