@echo off
title MyAgent CLI
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
echo   MyAgent CLI - RAG Knowledge Base + MCP Tools Assistant
echo   Chat commands: /help /sessions /new /delete-session
echo                  /upload /documents /delete /tools /exit
echo ==========================================================
.venv\Scripts\python.exe main.py chat
echo.
pause