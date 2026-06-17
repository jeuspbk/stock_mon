@echo off
cd /d "%~dp0"

set "PY=D:\MyProg\anaconda3\python.exe"
if not exist "%PY%" set "PY=python"

REM Skip Streamlit first-run email prompt
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  >"%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >>"%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

echo [stock_mon] Freeing port 8501 if in use...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1

echo [stock_mon] Starting dashboard at http://localhost:8501
"%PY%" -m streamlit run app.py

pause
