"""
Script de teste para verificar o acesso à planilha de clientes.

Executa verificações do caminho configurado no .env e tenta ler a planilha.
"""

import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar os módulos do app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
import pandas as pd


def test_client_path():
    """Testa o acesso à planilha de clientes."""
    print("=" * 60)
    print("TESTE DE ACESSO A PLANILHA DE CLIENTES")
    print("=" * 60)
    
    # 1. Carrega as configurações
    print("\n[1] Carregando configuracoes do .env...")
    settings = get_settings()
    
    path = settings.clients_excel_path
    print(f"    Caminho configurado: {path}")
    
    # 2. Verifica se o arquivo existe
    print("\n[2] Verificando existencia do arquivo...")
    if path.exists():
        print(f"    [OK] SUCESSO: Arquivo encontrado!")
        print(f"    Caminho absoluto: {path.resolve()}")
    else:
        print(f"    [ERRO] Arquivo NAO encontrado!")
        print(f"    Caminho tentado: {path}")
        
        # Tenta verificar o diretório pai
        parent = path.parent
        if parent.exists():
            print(f"\n    O diretorio pai existe: {parent}")
            print("    Arquivos no diretorio:")
            for f in parent.iterdir():
                print(f"      - {f.name}")
        else:
            print(f"\n    O diretorio pai tambem NAO existe: {parent}")
        
        return False
    
    # 3. Tenta ler o arquivo
    print("\n[3] Tentando ler o arquivo Excel...")
    try:
        df = pd.read_excel(path, dtype=str, engine="openpyxl")
        print(f"    [OK] SUCESSO: Arquivo lido com sucesso!")
        print(f"    Linhas: {len(df)}")
        print(f"    Colunas: {list(df.columns)}")
    except Exception as e:
        print(f"    [ERRO] ao ler arquivo: {e}")
        return False
    
    # 4. Verifica colunas obrigatórias
    print("\n[4] Verificando colunas obrigatorias...")
    df.columns = df.columns.str.strip().str.upper()
    required = {"COD", "NOME"}
    found = set(df.columns)
    missing = required - found
    
    if missing:
        print(f"    [ERRO] Colunas faltando: {missing}")
        return False
    else:
        print(f"    [OK] SUCESSO: Todas as colunas obrigatorias presentes!")
    
    # 5. Mostra alguns clientes de exemplo
    print("\n[5] Amostra de clientes carregados:")
    count = 0
    for i, row in df.iterrows():
        cod = str(row.get("COD", "")).strip()
        nome = str(row.get("NOME", "")).strip()
        if cod and nome and cod.lower() != "nan":
            cod = cod.zfill(3)
            print(f"    {cod} - {nome}")
            count += 1
            if count >= 5:
                break
    
    print("\n" + "=" * 60)
    print("[OK] TESTE CONCLUIDO COM SUCESSO!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = test_client_path()
    sys.exit(0 if success else 1)
