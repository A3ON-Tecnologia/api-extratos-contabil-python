@echo off
echo ============================================
echo Iniciando Servidor de Extratos Contabeis
echo ============================================

cd /d "C:\Users\azo3\Desktop\extratos-contabil-python"

REM Ativa o ambiente virtual
call venv\Scripts\activate.bat

REM Inicia o servidor
python -m uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload

pause
