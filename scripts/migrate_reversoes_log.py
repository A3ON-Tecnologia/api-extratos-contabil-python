"""
Migration: Criar tabela de logs de reversões.

Data: 2026-01-21
Descrição: Cria a tabela 'reversoes_log' para armazenar histórico de todas
           as reversões realizadas no sistema.
"""

import sys
import os

# Adiciona o diretório raiz ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text
from app.database import engine, Base
from app.models.reversao_log import ReversaoLog


def table_exists(table_name: str) -> bool:
    """Verifica se uma tabela já existe no banco."""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()


def create_table():
    """Cria a tabela reversoes_log se não existir."""
    table_name = "reversoes_log"
    
    if table_exists(table_name):
        print(f"⚠️  Tabela '{table_name}' já existe. Pulando criação.")
        return False
    
    # Cria apenas a tabela ReversaoLog
    ReversaoLog.__table__.create(engine)
    print(f"✅ Tabela '{table_name}' criada com sucesso!")
    return True


def show_table_structure():
    """Mostra a estrutura da tabela criada."""
    inspector = inspect(engine)
    columns = inspector.get_columns("reversoes_log")
    
    print("\n📋 Estrutura da tabela 'reversoes_log':")
    print("-" * 50)
    for col in columns:
        nullable = "NULL" if col["nullable"] else "NOT NULL"
        print(f"  {col['name']:25} {str(col['type']):20} {nullable}")


def main():
    print("=" * 60)
    print("Migration: Criar tabela de logs de reversões")
    print("=" * 60)
    
    try:
        # Testa conexão
        with engine.connect() as conn:
            print("✅ Conexão com o banco estabelecida")
        
        # Cria a tabela
        created = create_table()
        
        if created:
            show_table_structure()
        
        # Lista todas as tabelas
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"\n📊 Tabelas no banco de dados ({len(tables)}):")
        for table in sorted(tables):
            print(f"  • {table}")
        
        print("\n" + "=" * 60)
        print("✅ Migration concluída com sucesso!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Erro durante a migration: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
