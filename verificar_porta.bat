@echo off
echo ============================================
echo Verificando Porta 8888
echo ============================================
echo.

echo Verificando processos usando a porta 8888...
netstat -ano | findstr :8888

echo.
echo ============================================
echo Se aparecer algo acima, a porta esta em uso
echo Procure o PID (ultimo numero) e use:
echo    taskkill /PID [numero] /F
echo ============================================
pause
