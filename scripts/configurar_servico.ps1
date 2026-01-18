# ============================================
# Script para Configurar Servico API Extratos
# EXECUTE COMO ADMINISTRADOR!
# ============================================

param(
    [Parameter(Mandatory=$true)]
    [string]$SenhaUsuario
)

$NomeServico = "api_extratos_contabil_python"
$NssmPath = "C:\Users\azo3\Desktop\nssm-2.24\win64\nssm.exe"
$PythonPath = "C:\Users\azo3\Desktop\extratos-contabil-python\venv\Scripts\python.exe"
$AppDir = "C:\Users\azo3\Desktop\extratos-contabil-python"
$Usuario = ".\azo3"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Configurando Servico: $NomeServico" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# Passo 1: Parar servico existente (se houver)
Write-Host "`n[1/6] Parando servico existente..." -ForegroundColor Yellow
Stop-Service -Name $NomeServico -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# Passo 2: Remover servico existente (se houver)
Write-Host "[2/6] Removendo servico existente..." -ForegroundColor Yellow
& $NssmPath remove $NomeServico confirm 2>$null

# Passo 3: Instalar novo servico
Write-Host "[3/6] Instalando novo servico..." -ForegroundColor Yellow
& $NssmPath install $NomeServico $PythonPath

# Passo 4: Configurar parametros
Write-Host "[4/6] Configurando parametros..." -ForegroundColor Yellow
& $NssmPath set $NomeServico AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port 8888"
& $NssmPath set $NomeServico AppDirectory $AppDir
& $NssmPath set $NomeServico DisplayName "API Extratos Contabil Python"
& $NssmPath set $NomeServico Description "Servico de processamento automatico de extratos bancarios"
& $NssmPath set $NomeServico Start SERVICE_AUTO_START

# Passo 5: Configurar usuario
Write-Host "[5/6] Configurando usuario ($Usuario)..." -ForegroundColor Yellow
& $NssmPath set $NomeServico ObjectName $Usuario $SenhaUsuario

# Passo 6: Iniciar servico
Write-Host "[6/6] Iniciando servico..." -ForegroundColor Yellow
Start-Service -Name $NomeServico

# Verificar status
Start-Sleep -Seconds 3
$status = Get-Service -Name $NomeServico

Write-Host "`n============================================" -ForegroundColor Green
Write-Host "Servico configurado com sucesso!" -ForegroundColor Green
Write-Host "Status: $($status.Status)" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

# Testar conexao
Write-Host "`nTestando conexao com o servidor..." -ForegroundColor Yellow
Start-Sleep -Seconds 5
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8888/health" -Method GET -TimeoutSec 10
    Write-Host "Servidor respondendo: $($response.status)" -ForegroundColor Green
} catch {
    Write-Host "Aguarde alguns segundos e tente acessar http://localhost:8888/monitor" -ForegroundColor Yellow
}
