@echo off
chcp 65001 > nul
set PYTHONUTF8=1
echo ============================================
echo Iniciando Servidor de Extratos Contabeis
echo ============================================

cd /d "%~dp0"

REM Le a porta do arquivo .env
set PORT=8888
if exist .env (
    for /f "tokens=1,2 delims==" %%a in ('findstr /r "^PORT=" .env') do (
        set PORT=%%b
    )
)

echo Porta configurada: %PORT%
echo.

REM Ativa o ambiente virtual
call venv\Scripts\activate.bat

REM Inicia o servidor usando a porta do .env
echo Iniciando servidor na porta %PORT%...
python -m uvicorn app.main:app --host 0.0.0.0 --port %PORT% --reload

pause
