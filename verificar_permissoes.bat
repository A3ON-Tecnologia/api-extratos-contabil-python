@echo off
chcp 65001 > nul
echo.
echo ========================================
echo   VERIFICAR PERMISSOES DA PASTA
echo ========================================
echo.

REM Ativa o ambiente virtual se existir
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

REM Executa o script de verificação
python verificar_permissoes.py

echo.
pause
