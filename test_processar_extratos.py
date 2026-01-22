"""
Script para testar o processamento de extratos em modo TESTE.

Facilita o teste do fluxo completo sem precisar usar curl ou Postman.
"""

import requests
import json
import time
from pathlib import Path

# Lê porta do .env
def get_port_from_env():
    """Le a porta do arquivo .env."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("PORT="):
                    return line.split("=")[1].strip()
    return "7777"

PORT = get_port_from_env()
BASE_URL = f"http://localhost:{PORT}"


def print_json(data):
    """Imprime JSON formatado."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def test_processar_pasta():
    """Testa processamento de toda a pasta."""
    print("\n" + "="*60)
    print("TESTE: Processar todos os PDFs da pasta")
    print("="*60)

    try:
        response = requests.post(f"{BASE_URL}/extratos/test/processar-pasta", timeout=300)

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ {data['processed']} arquivos processados!")
            print_json(data)
            return True
        else:
            print(f"❌ Erro {response.status_code}")
            print_json(response.json())
            return False

    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False


def test_upload_pdf(pdf_path: str):
    """Testa upload de um PDF específico."""
    print("\n" + "="*60)
    print(f"TESTE: Upload de PDF - {pdf_path}")
    print("="*60)

    pdf_file = Path(pdf_path)

    if not pdf_file.exists():
        print(f"❌ Arquivo não encontrado: {pdf_path}")
        return False

    try:
        with open(pdf_file, 'rb') as f:
            files = {'file': (pdf_file.name, f, 'application/pdf')}
            response = requests.post(
                f"{BASE_URL}/extratos/test/processar",
                files=files,
                timeout=30
            )

        if response.status_code == 200:
            data = response.json()
            print("✅ Arquivo enviado para processamento!")
            print_json(data)

            job_id = data.get('job_id')
            if job_id:
                return check_job_status(job_id)

        else:
            print(f"❌ Erro {response.status_code}")
            print_json(response.json())
            return False

    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False


def check_job_status(job_id: str, max_wait: int = 60):
    """Verifica status de um job até completar ou atingir timeout."""
    print(f"\n🔄 Aguardando conclusão do job {job_id}...")

    for i in range(max_wait):
        try:
            response = requests.get(f"{BASE_URL}/extratos/test/job/{job_id}", timeout=5)

            if response.status_code == 200:
                data = response.json()
                status = data.get('status')

                if status == 'completed':
                    print("\n✅ Processamento concluído!")
                    print_json(data)
                    return True
                elif status == 'error':
                    print("\n❌ Erro no processamento!")
                    print_json(data)
                    return False
                else:
                    print(f"⏳ Aguardando... ({i+1}s)")
                    time.sleep(1)

        except Exception as e:
            print(f"❌ Erro ao verificar status: {e}")
            return False

    print(f"\n⚠️ Timeout após {max_wait}s")
    return False


def test_list_logs():
    """Lista os logs de teste."""
    print("\n" + "="*60)
    print("TESTE: Listar logs de teste")
    print("="*60)

    try:
        response = requests.get(f"{BASE_URL}/extratos/test/logs?limit=10", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ {data['total']} logs encontrados")
            print_json(data)
            return True
        else:
            print(f"❌ Erro {response.status_code}")
            print_json(response.json())
            return False

    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False


def test_clear_logs():
    """Limpa todos os logs de teste."""
    print("\n" + "="*60)
    print("TESTE: Limpar todos os logs de teste")
    print("="*60)

    confirm = input("⚠️ Isso vai DELETAR todos os logs de teste. Confirma? (s/N): ")
    if confirm.lower() != 's':
        print("Cancelado.")
        return False

    try:
        response = requests.delete(f"{BASE_URL}/extratos/test/logs", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ {data['count']} logs removidos")
            print_json(data)
            return True
        else:
            print(f"❌ Erro {response.status_code}")
            print_json(response.json())
            return False

    except Exception as e:
        print(f"❌ ERRO: {e}")
        return False


def main():
    """Menu principal."""
    print("\n" + "🧪 TESTE DE PROCESSAMENTO DE EXTRATOS ".center(60, "="))
    print(f"\n🌐 Porta: {PORT}")
    print(f"📡 URL: {BASE_URL}\n")

    while True:
        print("\n" + "="*60)
        print("OPÇÕES:")
        print("="*60)
        print("1. Processar todos os PDFs da pasta configurada")
        print("2. Upload de PDF específico")
        print("3. Listar logs de teste")
        print("4. Limpar todos os logs de teste")
        print("5. Verificar servidor")
        print("0. Sair")
        print("="*60)

        opcao = input("\nEscolha uma opção: ").strip()

        if opcao == "1":
            test_processar_pasta()

        elif opcao == "2":
            pdf_path = input("Caminho do PDF: ").strip()
            test_upload_pdf(pdf_path)

        elif opcao == "3":
            test_list_logs()

        elif opcao == "4":
            test_clear_logs()

        elif opcao == "5":
            try:
                response = requests.get(f"{BASE_URL}/health", timeout=5)
                if response.status_code == 200:
                    print("\n✅ Servidor online!")
                    print_json(response.json())
                else:
                    print(f"\n⚠️ Servidor retornou status {response.status_code}")
            except:
                print("\n❌ Servidor não está respondendo!")
                print(f"   Certifique-se de que está rodando em {BASE_URL}")

        elif opcao == "0":
            print("\n👋 Até logo!")
            break

        else:
            print("\n❌ Opção inválida!")


if __name__ == "__main__":
    main()
