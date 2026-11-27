@echo off
cd /d "%~dp0"
python -m venv .venv || goto :fail
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
if not exist .env copy .env.example .env
echo.
echo Setup ho gaya! .env mein API keys daalo, phir run.bat chalao.
pause
exit /b 0
:fail
echo Setup fail hua. Upar ka error dekho.
pause
exit /b 1
