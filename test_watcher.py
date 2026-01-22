"""
Script de teste para o watcher de extratos.
Execute para verificar se o watcher está funcionando corretamente.
"""

import requests
import json
import time
import os
from pathlib import Path

# Tenta ler a porta do .env
def get_port_from_env():
    """Le a porta do arquivo .env."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("PORT="):
                    return line.split("=")[1].strip()
    return "7777"  # Porta padrão

PORT = get_port_from_env()
BASE_URL = f"http://localhost:{PORT}"

def print_json(data):
    """Imprime JSON formatado."""
    print(json.dumps(data, indent=2, ensure_ascii=False))

def test_server():
    """Testa se o servidor está respondendo."""
    print("\n" + "="*60)
    print("TESTE 1: Verificando se o servidor está online...")
    print("="*60)
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"✅ Servidor online! Status: {response.status_code}")
        print_json(response.json())
        return True
    except requests.exceptions.ConnectionError:
        print("❌ ERRO: Servidor não está rodando!")
        print("   Execute: python -m uvicorn app.main:app --reload --port 8888")
        return False
    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False

def test_config():
    """Testa as configurações."""
    print("\n" + "="*60)
    print("TESTE 2: Verificando configurações...")
    print("="*60)
    try:
        response = requests.get(f"{BASE_URL}/config/validate", timeout=5)
        data = response.json()
        print_json(data)

        if data.get("status") == "ok":
            print("✅ Todas as configurações OK!")
            return True
        else:
            print("⚠️ ATENÇÃO: Problemas nas configurações!")
            return False
    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False

def test_watcher_debug():
    """Testa debug do watcher."""
    print("\n" + "="*60)
    print("TESTE 3: Debug do watcher...")
    print("="*60)
    try:
        response = requests.get(f"{BASE_URL}/extratos/watch/debug", timeout=5)
        data = response.json()
        print_json(data)

        path_info = data.get("path", {})
        if path_info.get("exists") and path_info.get("is_directory"):
            print("✅ Pasta configurada existe e é um diretório!")
            return True
        else:
            print("❌ ERRO: Pasta não existe ou não é um diretório!")
            print(f"   Caminho configurado: {path_info.get('configured')}")
            print("   Verifique o arquivo .env e corrija WATCH_FOLDER_PATH")
            return False
    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False

def test_watcher_status():
    """Verifica status do watcher."""
    print("\n" + "="*60)
    print("TESTE 4: Status do watcher...")
    print("="*60)
    try:
        response = requests.get(f"{BASE_URL}/extratos/watch/status", timeout=5)
        data = response.json()
        print_json(data)

        if data.get("running"):
            print("✅ Watcher está ATIVO!")
            return True
        else:
            print("⚠️ Watcher está PARADO")
            return False
    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False

def test_start_watcher():
    """Tenta iniciar o watcher."""
    print("\n" + "="*60)
    print("TESTE 5: Iniciando o watcher...")
    print("="*60)
    try:
        response = requests.post(f"{BASE_URL}/extratos/watch/start", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print_json(data)
            print("✅ Watcher iniciado com sucesso!")
            return True
        else:
            print(f"❌ ERRO ao iniciar watcher! Status: {response.status_code}")
            print_json(response.json())
            return False
    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False

def test_stop_watcher():
    """Para o watcher."""
    print("\n" + "="*60)
    print("TESTE 6: Parando o watcher...")
    print("="*60)
    try:
        response = requests.post(f"{BASE_URL}/extratos/watch/stop", timeout=5)
        data = response.json()
        print_json(data)
        print("✅ Watcher parado!")
        return True
    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False

def main():
    """Executa todos os testes."""
    print("\n" + "🔍 TESTE DE DIAGNÓSTICO DO WATCHER DE EXTRATOS ".center(60, "="))
    print(f"\n🌐 Usando porta: {PORT}")
    print(f"📡 URL base: {BASE_URL}\n")

    # Teste 1: Servidor online
    if not test_server():
        print("\n❌ FALHA CRÍTICA: Servidor não está respondendo!")
        print("   Certifique-se de que a API está rodando antes de continuar.")
        return

    # Teste 2: Configurações
    test_config()

    # Teste 3: Debug do watcher
    path_ok = test_watcher_debug()

    if not path_ok:
        print("\n❌ FALHA CRÍTICA: Pasta do watcher não existe!")
        print("\n📝 SOLUÇÃO:")
        print("   1. Abra o arquivo .env")
        print("   2. Verifique a linha WATCH_FOLDER_PATH")
        print("   3. Certifique-se de que o caminho existe no sistema")
        print("   4. Execute este script novamente")
        return

    # Teste 4: Status atual
    is_running = test_watcher_status()

    # Teste 5: Se não estiver rodando, tenta iniciar
    if not is_running:
        if test_start_watcher():
            time.sleep(2)  # Aguarda 2 segundos
            test_watcher_status()  # Verifica novamente

            print("\n" + "="*60)
            print("🎉 TESTES CONCLUÍDOS COM SUCESSO!")
            print("="*60)
            print("\n✅ O watcher está ativo e monitorando a pasta!")
            print("   Agora você pode colocar arquivos PDF ou ZIP na pasta")
            print("   e eles serão processados automaticamente.\n")
        else:
            print("\n❌ Não foi possível iniciar o watcher!")
            print("   Verifique os logs acima para mais detalhes.")
    else:
        print("\n" + "="*60)
        print("🎉 TESTES CONCLUÍDOS!")
        print("="*60)
        print("\n✅ O watcher JÁ está ativo!")
        print("   Você pode colocar arquivos PDF ou ZIP na pasta agora.\n")

    # Teste 6: Demonstra como parar (opcional)
    print("\n💡 DICA: Para parar o watcher, execute:")
    print(f"   POST {BASE_URL}/extratos/watch/stop")

if __name__ == "__main__":
    main()
