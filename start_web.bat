@echo off
title MyAgent Web
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found.
    echo Please run these commands first:
    echo   py -3.11 -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    pause
    exit /b 1
)

echo ==========================================================
echo   MyAgent Web - starting Streamlit UI ...
echo   After startup, browser opens automatically.
echo   Address: http://localhost:8501
echo   Stop with Ctrl+C in this window.
echo ==========================================================
.venv\Scripts\python.exe -m streamlit run web/app.py --server.fileWatcherType none
pause