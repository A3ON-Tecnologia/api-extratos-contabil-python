"""
Script para reverter processamentos recentes.
Lista todos os arquivos que seriam deletados antes de executar.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from app.database import SessionLocal
from app.models.extrato_log import ExtratoLog
from sqlalchemy import desc

def listar_ultimos_processamentos(quantidade=68):
    """Lista os últimos N processamentos."""
    db = SessionLocal()
    
    logs = db.query(ExtratoLog).order_by(desc(ExtratoLog.id)).limit(quantidade).all()
    
    print(f"=" * 70)
    print(f"ULTIMOS {len(logs)} PROCESSAMENTOS")
    print(f"=" * 70)
    
    arquivos_para_deletar = []
    
    for log in logs:
        arquivo = log.arquivo_salvo or "N/A"
        cliente = log.cliente_nome[:40] if log.cliente_nome else "N/A"
        status = log.status
        
        print(f"ID {log.id:3} | {status:15} | {cliente}")
        print(f"         Arquivo: {arquivo}")
        
        if log.arquivo_salvo:
            path = Path(log.arquivo_salvo)
            if path.exists():
                arquivos_para_deletar.append(path)
                print(f"         [EXISTE NO DISCO]")
            else:
                print(f"         [NAO EXISTE NO DISCO]")
        print()
    
    print(f"=" * 70)
    print(f"RESUMO:")
    print(f"  - Registros no banco: {len(logs)}")
    print(f"  - Arquivos que existem no disco: {len(arquivos_para_deletar)}")
    print(f"=" * 70)
    
    db.close()
    
    return logs, arquivos_para_deletar


def reverter_processamentos(quantidade=68, deletar_arquivos=True):
    """
    Reverte os últimos N processamentos.
    - Deleta os registros do banco
    - Opcionalmente deleta os arquivos do disco
    """
    db = SessionLocal()
    
    logs = db.query(ExtratoLog).order_by(desc(ExtratoLog.id)).limit(quantidade).all()
    
    arquivos_deletados = 0
    registros_deletados = 0
    
    for log in logs:
        # Deleta arquivo do disco
        if deletar_arquivos and log.arquivo_salvo:
            path = Path(log.arquivo_salvo)
            if path.exists():
                try:
                    path.unlink()
                    arquivos_deletados += 1
                    print(f"Arquivo deletado: {path}")
                except Exception as e:
                    print(f"Erro ao deletar {path}: {e}")
        
        # Deleta registro do banco
        db.delete(log)
        registros_deletados += 1
    
    db.commit()
    db.close()
    
    print(f"\n{'=' * 70}")
    print(f"REVERSAO CONCLUIDA:")
    print(f"  - Registros deletados do banco: {registros_deletados}")
    print(f"  - Arquivos deletados do disco: {arquivos_deletados}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Reverte processamentos recentes")
    parser.add_argument("--quantidade", "-n", type=int, default=68, help="Quantidade de processamentos para reverter")
    parser.add_argument("--executar", action="store_true", help="Executa a reversao (sem essa flag, apenas lista)")
    parser.add_argument("--sem-arquivos", action="store_true", help="Nao deleta os arquivos do disco")
    
    args = parser.parse_args()
    
    if args.executar:
        print("ATENCAO: Executando reversao!")
        print()
        reverter_processamentos(args.quantidade, deletar_arquivos=not args.sem_arquivos)
    else:
        print("Modo LISTAGEM (use --executar para reverter)")
        print()
        listar_ultimos_processamentos(args.quantidade)
        print()
        print("Para executar a reversao, rode:")
        print(f"  python scripts/reverter_processamentos.py -n {args.quantidade} --executar")
