# ============================================
# Script para Configurar Servico API Extratos
# EXECUTE COMO ADMINISTRADOR!
# ============================================

param(
    [string]$Usuario = "",
    [string]$Senha = ""
)

$NomeServico = "api_extratos_contabil_python"
$NssmPath = "C:\Users\azo3\Desktop\nssm-2.24\win64\nssm.exe"
$AppDir = Get-Location
$PythonPath = Join-Path $AppDir "venv\Scripts\python.exe"

# Tenta ler a porta do .env
$Port = "8888"
if (Test-Path ".env") {
    $envContent = Get-Content ".env"
    foreach ($line in $envContent) {
        if ($line -match "^PORT=(\d+)") {
            $Port = $matches[1]
            break
        }
    }
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Configurando Servico: $NomeServico" -ForegroundColor Cyan
Write-Host "Diretorio: $AppDir" -ForegroundColor Gray
Write-Host "Porta: $Port" -ForegroundColor Gray
Write-Host "============================================" -ForegroundColor Cyan

# Verifica se o terminal é admin
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "❌ ERRO: Este script PRECISA ser executado como ADMINISTRADOR!" -ForegroundColor Red
    exit
}

# Passo 1: Parar servico existente (se houver)
Write-Host "`n[1/5] Parando servico existente..." -ForegroundColor Yellow
Stop-Service -Name $NomeServico -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# Passo 2: Remover servico existente (se houver)
Write-Host "[2/5] Removendo servico existente..." -ForegroundColor Yellow
& $NssmPath remove $NomeServico confirm 2>$null

# Passo 3: Instalar novo servico
Write-Host "[3/5] Instalando novo servico..." -ForegroundColor Yellow
& $NssmPath install $NomeServico $PythonPath

# Passo 4: Configurar parametros
Write-Host "[4/5] Configurando parametros..." -ForegroundColor Yellow
& $NssmPath set $NomeServico AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmPath set $NomeServico AppDirectory $AppDir
& $NssmPath set $NomeServico DisplayName "A3ON - API Extratos Contabil"
& $NssmPath set $NomeServico Description "Servico de processamento automatico de extratos bancarios"
& $NssmPath set $NomeServico Start SERVICE_AUTO_START
& $NssmPath set $NomeServico AppStdout "$AppDir\logs\service_out.log"
& $NssmPath set $NomeServico AppStderr "$AppDir\logs\service_err.log"

# Configura login se fornecido (necessario para mapeamentos de rede como J:)
if ($Usuario -ne "" -and $Senha -ne "") {
    Write-Host "Configurando login do servico para: $Usuario" -ForegroundColor Cyan
    & $NssmPath set $NomeServico ObjectName $Usuario $Senha
}

# Cria pasta de logs
if (-not (Test-Path "logs")) { New-Item -Path "logs" -ItemType Directory }

# Passo 5: Iniciar servico
Write-Host "[5/5] Iniciando servico..." -ForegroundColor Yellow
Start-Service -Name $NomeServico

# Verificar status
Start-Sleep -Seconds 3
$status = Get-Service -Name $NomeServico

Write-Host "`n============================================" -ForegroundColor Green
Write-Host "Servico configurado com sucesso!" -ForegroundColor Green
Write-Host "Status: $($status.Status)" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

# Testar conexao
Write-Host "`nTestando conexao locally (Porta $Port)..." -ForegroundColor Yellow
Start-Sleep -Seconds 5
try {
    $response = Invoke-RestMethod -Uri "http://localhost:$Port/health" -Method GET -TimeoutSec 10
    Write-Host "✅ Servidor respondendo: $($response.status)" -ForegroundColor Green
} catch {
    Write-Host "⚠️ Servidor ainda iniciando ou inacessivel. Verifique os logs em .\logs\" -ForegroundColor Yellow
}
