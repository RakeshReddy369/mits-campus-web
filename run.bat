@echo off
title Web Campus - College Attendance Portal
echo ===================================================
echo   Starting Web Campus Attendance Application...
echo ===================================================
echo.
cd /d "%~dp0"
python server.py
pause
