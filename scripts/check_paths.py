
from pathlib import Path
import os

target_dir = Path(r"J:\JP Digital\000 - AUTOMAÇÕES")

print(f"Verificando diretório: {target_dir}")

if not target_dir.exists():
    print("ERRO: O diretório não existe!")
else:
    print("Diretório existe. Listando arquivos:")
    try:
        for f in target_dir.iterdir():
            if f.is_file() and f.suffix == '.xlsx':
                print(f"ENCONTRADO: '{f.name}'")
                # Imprime representação repr para ver caracteres ocultos
                print(f"   REPR: {repr(f.name)}")
    except Exception as e:
        print(f"Erro ao listar diretório: {e}")
