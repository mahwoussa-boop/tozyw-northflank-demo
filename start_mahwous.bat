@echo off
rem مشغّل «مهووس» — يبدأ خادم Streamlit على الشبكة (المنفذ 8501)
cd /d "%~dp0"
set "DATA_DIR=%~dp0data"
.venv\Scripts\python.exe -m streamlit run app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true
