@echo off
cd /d "%~dp0"
title Mahwous Pricing v2

set "DATA_DIR=%~dp0data"

echo ============================================
echo    Mahwous - Smart Pricing v2
echo    Starting... Browser will open automatically.
echo    Do NOT close this window.
echo ============================================
echo.

python -m streamlit run app.py --server.headless=false --server.port=8501
if errorlevel 1 (
  echo.
  echo Trying py launcher...
  py -m streamlit run app.py --server.headless=false --server.port=8501
  if errorlevel 1 (
    echo.
    echo ERROR: Could not start the app.
    echo Make sure Python and Streamlit are installed.
    echo Run: pip install streamlit
  )
)

echo.
echo App stopped. Press any key to close.
pause >nul
