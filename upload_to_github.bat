@echo off
echo ========================================================
echo   Uploading MITS CAMPUS WEB to GitHub (RakeshReddy369)
echo ========================================================
echo.
cd /d "%~dp0"
git push -u origin main
echo.
echo ========================================================
if %ERRORLEVEL% equ 0 (
    echo   SUCCESS: Your code is now uploaded to GitHub!
    echo   Next step: Go to https://vercel.com/new and click Deploy!
) else (
    echo   If it failed, make sure you created the repo at:
    echo   https://github.com/new with name: mits-campus-web
)
echo ========================================================
echo.
pause
