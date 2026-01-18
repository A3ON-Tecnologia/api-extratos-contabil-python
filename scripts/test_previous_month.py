"""
Script para testar a função de mês anterior.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from app.services.storage_service import StorageService

service = StorageService()
ano, mes = service._get_previous_month()

now = datetime.now()
print(f"Data atual: {now.strftime('%d/%m/%Y %H:%M')}")
print(f"Mes atual: {now.month}/{now.year}")
print()
print(f"==> Mes anterior calculado: {mes}/{ano}")
print()
print(f"Os extratos serao salvos na pasta: {ano}/{str(mes).zfill(2)}")
print()

# Simula diferentes cenários
print("=" * 50)
print("SIMULACAO DE CENARIOS:")
print("=" * 50)

scenarios = [
    (2026, 1),   # Janeiro -> deve retornar Dezembro/2025
    (2026, 2),   # Fevereiro -> deve retornar Janeiro/2026
    (2026, 12),  # Dezembro -> deve retornar Novembro/2026
]

for year, month in scenarios:
    # Calcula mês anterior manualmente para teste
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    
    print(f"  Se estivermos em {month:02d}/{year} -> salvaria em {prev_month:02d}/{prev_year}")
