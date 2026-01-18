"""
Script para limpar cache e verificar configuracoes.

Execute este script para forcar a limpeza do cache e ver as configuracoes atuais.
"""

import sys
from pathlib import Path

# Adiciona o diretorio raiz ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings, clear_settings_cache


def main():
    print("=" * 60)
    print("LIMPEZA DE CACHE E VERIFICACAO DE CONFIGURACOES")
    print("=" * 60)
    
    # Mostra configuracoes ANTES de limpar cache
    print("\n[1] Configuracoes ANTES de limpar cache:")
    settings = get_settings()
    print(f"    clients_excel_path: {settings.clients_excel_path}")
    print(f"    base_path: {settings.base_path}")
    print(f"    log_excel_path: {settings.log_excel_path}")
    
    # Limpa o cache
    print("\n[2] Limpando cache...")
    new_settings = clear_settings_cache()
    print("    [OK] Cache limpo!")
    
    # Mostra configuracoes DEPOIS de limpar cache
    print("\n[3] Configuracoes DEPOIS de limpar cache:")
    print(f"    clients_excel_path: {new_settings.clients_excel_path}")
    print(f"    base_path: {new_settings.base_path}")
    print(f"    log_excel_path: {new_settings.log_excel_path}")
    
    # Verifica se o arquivo existe
    print("\n[4] Verificando existencia dos arquivos:")
    if new_settings.clients_excel_path.exists():
        print(f"    [OK] Planilha de clientes: EXISTE")
    else:
        print(f"    [ERRO] Planilha de clientes: NAO EXISTE")
        print(f"           Caminho: {new_settings.clients_excel_path}")
    
    if new_settings.base_path.exists():
        print(f"    [OK] Base path: EXISTE")
    else:
        print(f"    [ERRO] Base path: NAO EXISTE")
    
    print("\n" + "=" * 60)
    print("CONCLUIDO! Agora reinicie o servidor para aplicar.")
    print("=" * 60)


if __name__ == "__main__":
    main()
