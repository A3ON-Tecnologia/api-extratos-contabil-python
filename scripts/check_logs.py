"""
Script para verificar logs recentes do banco de dados.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from app.database import SessionLocal
from app.models.extrato_log import ExtratoLog
from sqlalchemy import desc

db = SessionLocal()

# Estatísticas gerais
total = db.query(ExtratoLog).count()
sucesso = db.query(ExtratoLog).filter(ExtratoLog.status == "SUCESSO").count()
nao_id = db.query(ExtratoLog).filter(ExtratoLog.status == "NAO_IDENTIFICADO").count()
falha = db.query(ExtratoLog).filter(ExtratoLog.status == "FALHA").count()

print("=" * 60)
print("ESTATISTICAS DO BANCO DE DADOS (PRODUCAO)")
print("=" * 60)
print(f"Total de registros: {total}")
print(f"Sucesso: {sucesso}")
print(f"Nao Identificado: {nao_id}")
print(f"Falha: {falha}")
print()

# Logs das ultimas 24 horas
hoje = datetime.now()
ontem = hoje - timedelta(hours=24)

logs_recentes = db.query(ExtratoLog).filter(
    ExtratoLog.processado_em >= ontem
).order_by(desc(ExtratoLog.processado_em)).all()

print(f"Logs das ultimas 24h: {len(logs_recentes)}")
print("-" * 60)

for log in logs_recentes[:20]:
    cliente = log.cliente_nome[:35] if log.cliente_nome else "N/A"
    hora = log.processado_em.strftime("%H:%M:%S") if log.processado_em else "--"
    print(f"{log.id:3} | {hora} | {log.status:15} | {cliente}")

db.close()
