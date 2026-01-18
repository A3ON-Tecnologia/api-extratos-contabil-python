"""
Script para criar/atualizar tabelas no banco de dados.
"""

import sys
import os

# Adiciona o diretório raiz ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, engine
from app.models.extrato_log import ExtratoLog

def main():
    print("=" * 50)
    print("Inicializando banco de dados...")
    print("=" * 50)
    
    try:
        # Testa conexão
        with engine.connect() as conn:
            print("✓ Conexão com o banco estabelecida")
        
        # Cria tabelas
        init_db()
        print("✓ Tabelas criadas/atualizadas com sucesso!")
        
        # Lista tabelas criadas
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"\nTabelas no banco de dados:")
        for table in tables:
            print(f"  - {table}")
        
        print("\n" + "=" * 50)
        print("Migração concluída com sucesso!")
        print("=" * 50)
        
    except Exception as e:
        print(f"\n❌ Erro ao criar tabelas: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
