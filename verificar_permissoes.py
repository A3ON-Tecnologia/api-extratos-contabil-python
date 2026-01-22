"""
Script para verificar permissões de acesso à pasta de extratos.
"""

import os
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path
sys.path.insert(0, str(Path(__file__).parent))

from app.config import get_settings


def check_permissions():
    """Verifica permissões de acesso à pasta de extratos."""
    print("="*60)
    print("VERIFICAÇÃO DE PERMISSÕES - PASTA DE EXTRATOS")
    print("="*60)

    settings = get_settings()
    watch_path = settings.watch_folder_path

    print(f"\n📂 Pasta configurada: {watch_path}")
    print(f"   Absoluto: {watch_path.resolve() if watch_path.exists() else 'N/A'}")

    # Verifica se existe
    if not watch_path.exists():
        print("\n❌ ERRO: Pasta não existe!")
        return False

    print("✅ Pasta existe")

    # Verifica se é diretório
    if not watch_path.is_dir():
        print("\n❌ ERRO: Caminho não é um diretório!")
        return False

    print("✅ É um diretório")

    # Verifica permissão de leitura
    try:
        files = list(watch_path.iterdir())
        print(f"✅ Permissão de leitura OK ({len(files)} itens encontrados)")
    except PermissionError:
        print("❌ ERRO: Sem permissão de leitura na pasta!")
        print("\n💡 Soluções:")
        print("   1. Execute este script como administrador")
        print("   2. Verifique as permissões da pasta no Windows Explorer")
        print("   3. Certifique-se de que a pasta não está bloqueada")
        return False
    except Exception as e:
        print(f"❌ ERRO ao acessar pasta: {e}")
        return False

    # Lista arquivos PDF
    pdf_files = []
    errors = []

    for file_path in watch_path.iterdir():
        try:
            if file_path.is_file() and file_path.suffix.lower() == '.pdf':
                stat = file_path.stat()
                size_mb = stat.st_size / (1024 * 1024)
                pdf_files.append((file_path.name, size_mb))
        except PermissionError:
            errors.append(f"{file_path.name} (sem permissão)")
        except Exception as e:
            errors.append(f"{file_path.name} ({str(e)})")

    print(f"\n📄 PDFs encontrados: {len(pdf_files)}")

    if pdf_files:
        print("\nLista de arquivos PDF:")
        for nome, tamanho in sorted(pdf_files, key=lambda x: x[0]):
            print(f"   • {nome} ({tamanho:.2f} MB)")

    if errors:
        print(f"\n⚠️ Arquivos com erro de leitura: {len(errors)}")
        for error in errors:
            print(f"   • {error}")

    # Verifica permissão de escrita (teste)
    test_file = watch_path / "_test_permissions.tmp"
    try:
        test_file.write_text("test")
        test_file.unlink()
        print("\n✅ Permissão de escrita OK")
    except PermissionError:
        print("\n⚠️ Sem permissão de escrita (necessário apenas para processamento)")
    except Exception as e:
        print(f"\n⚠️ Erro ao testar escrita: {e}")

    print("\n" + "="*60)
    print("VERIFICAÇÃO CONCLUÍDA")
    print("="*60)

    return True


if __name__ == "__main__":
    try:
        success = check_permissions()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERRO CRÍTICO: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
