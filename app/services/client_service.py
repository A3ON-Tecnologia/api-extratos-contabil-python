"""
Serviço de leitura da planilha de clientes.

Carrega e mantém em cache a lista de clientes da planilha Excel.
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd

from app.config import get_settings
from app.schemas.client import ClientInfo
from app.utils.text import extract_numbers

logger = logging.getLogger(__name__)


class ClientService:
    """Serviço para gerenciamento de clientes a partir da planilha Excel."""
    
    # Cache compartilhado entre instâncias
    _cache: list[ClientInfo] | None = None
    _cache_time: datetime | None = None
    _cache_lock = Lock()
    
    # Tempo de cache em minutos
    CACHE_DURATION_MINUTES = 5
    
    def __init__(self):
        """Inicializa o serviço."""
        self.settings = get_settings()
    
    def _pick_conta(self, row: dict) -> str | None:
        """Obtem a conta a partir de possiveis colunas."""
        import re

        normalized_map: dict[str, str] = {}
        for key in row.keys():
            normalized = re.sub(r"[^A-Z0-9]", "", str(key).upper())
            if normalized and normalized not in normalized_map:
                normalized_map[normalized] = key

        candidates = [
            "NCONTA",
            "NUMEROCONTA",
            "NUMERODACONTA",
            "CONTA",
        ]
        for key in candidates:
            original = normalized_map.get(key)
            if original is not None:
                return self._clean_value(row.get(original))
        return None

    def _pick_tipo_documento(self, row: dict) -> str | None:
        """Obtem o tipo de documento a partir de possiveis colunas."""
        candidates = [
            "TIPO_DOCUMENTO",
            "TIPO DOCUMENTO",
            "TIPO_EXTRATO",
            "TIPO EXTRATO",
            "TIPO",
        ]
        for key in candidates:
            value = row.get(key)
            if value is not None:
                return self._clean_value(value)
        return None

    def _read_dataframe(self, path: Path) -> "pd.DataFrame":
        """
        Lê uma planilha (Excel ou CSV) e retorna um DataFrame com colunas normalizadas.

        Raises:
            FileNotFoundError: Se o arquivo não existir
            ValueError: Se não for possível ler o arquivo ou faltar colunas obrigatórias
        """
        if not path.exists():
            raise FileNotFoundError(f"Planilha de clientes não encontrada: {path}")

        try:
            suffix = path.suffix.lower()
            if suffix == ".csv":
                df = pd.read_csv(path, dtype=str, sep=None, engine="python")
            else:
                df = pd.read_excel(path, dtype=str, engine="openpyxl")
        except Exception as e:
            raise ValueError(f"Erro ao ler planilha de clientes: {e}")

        # Normaliza nomes das colunas (remove espaços, uppercase)
        df.columns = df.columns.str.strip().str.upper()

        # Verifica colunas obrigatórias
        required_columns = {"COD", "NOME"}
        missing = required_columns - set(df.columns)
        if missing:
            raise ValueError(f"Colunas obrigatórias faltando na planilha: {missing}")

        return df

    def _load_clients_from_dataframe(self, df: "pd.DataFrame") -> list[ClientInfo]:
        """
        Converte um DataFrame (já com colunas normalizadas) em lista de ClientInfo.

        Linhas sem código ou nome válidos são ignoradas.
        """
        clients: list[ClientInfo] = []

        for _, row in df.iterrows():
            # Pula linhas sem código ou nome
            cod = str(row.get("COD", "")).strip()
            nome = str(row.get("NOME", "")).strip()

            if not cod or not nome or cod.lower() == "nan":
                continue

            # Padroniza o código com zeros à esquerda (3 dígitos)
            cod = cod.zfill(3)

            client = ClientInfo(
                cod=cod,
                nome=nome,
                cnpj=self._clean_value(row.get("CNPJ")),
                banco=self._clean_value(row.get("BANCO")),
                agencia=self._clean_value(row.get("AGENCIA")),
                conta=self._pick_conta(row),
                tipo_documento=self._pick_tipo_documento(row),
            )

            clients.append(client)

        return clients

    def load_clients(self, force_reload: bool = False) -> list[ClientInfo]:
        """
        Carrega a lista de clientes da planilha Excel.

        Utiliza cache para evitar leituras repetidas do arquivo.

        Args:
            force_reload: Se True, força recarregamento ignorando cache

        Returns:
            Lista de clientes

        Raises:
            FileNotFoundError: Se a planilha não existir
            ValueError: Se a planilha não tiver as colunas obrigatórias
        """
        with self._cache_lock:
            # Verifica se o cache é válido
            if not force_reload and self._is_cache_valid():
                logger.debug("Usando cache de clientes")
                return self._cache

            # Carrega a planilha
            logger.info(f"Carregando planilha de clientes: {self.settings.clients_excel_path}")

            df = self._read_dataframe(self.settings.clients_excel_path)
            clients = self._load_clients_from_dataframe(df)

            logger.info(f"Carregados {len(clients)} clientes da planilha")

            # Atualiza cache
            ClientService._cache = clients
            ClientService._cache_time = datetime.now()

            return clients

    def load_clients_from_path(self, excel_path: Path, force_reload: bool = False) -> list[ClientInfo]:
        """
        Carrega clientes a partir de um caminho de planilha informado.

        Usa o cache padrao apenas quando o caminho for o mesmo de clients_excel_path.
        """
        if excel_path == self.settings.clients_excel_path:
            return self.load_clients(force_reload=force_reload)

        logger.info(f"Carregando planilha de clientes (override): {excel_path}")

        df = self._read_dataframe(excel_path)
        clients = self._load_clients_from_dataframe(df)

        logger.info(f"Carregados {len(clients)} clientes da planilha (override)")
        return clients

    def list_client_folders(self) -> list[ClientInfo]:
        """
        Lista clientes a partir das pastas existentes no BASE_PATH.

        Considera pastas no formato "COD - NOME".
        """
        base_path = self.settings.base_path
        if not base_path.exists() or not base_path.is_dir():
            raise FileNotFoundError(f"Base path nao encontrado: {base_path}")

        clients: list[ClientInfo] = []
        pattern = re.compile(r"^\s*(\d{1,3})\s*-\s*(.+)$")

        try:
            for entry in base_path.iterdir():
                if not entry.is_dir():
                    continue
                name = entry.name.strip()
                match = pattern.match(name)
                if not match:
                    continue
                cod = match.group(1).zfill(3)
                nome = match.group(2).strip()
                if not nome:
                    continue
                clients.append(
                    ClientInfo(
                        cod=cod,
                        nome=nome,
                        cnpj=None,
                        banco=None,
                        agencia=None,
                        conta=None,
                        tipo_documento=None,
                    )
                )
        except OSError as e:
            raise OSError(f"Erro ao listar pastas de clientes: {e}")

        # Ordena por codigo e nome para estabilidade
        clients.sort(key=lambda c: (c.cod, c.nome))
        return clients

    def _is_cache_valid(self) -> bool:
        """Verifica se o cache ainda é válido."""
        if self._cache is None or self._cache_time is None:
            return False
        
        expiry = self._cache_time + timedelta(minutes=self.CACHE_DURATION_MINUTES)
        return datetime.now() < expiry
    
    def _clean_value(self, value) -> str | None:
        """Limpa e normaliza um valor da planilha."""
        if pd.isna(value) or value is None:
            return None
        
        value = str(value).strip()
        if not value or value.lower() == "nan":
            return None
        
        return value
    
    def get_client_by_cod(self, cod: str) -> ClientInfo | None:
        """
        Busca um cliente pelo código.
        
        Args:
            cod: Código do cliente (ex: "098")
            
        Returns:
            Cliente encontrado ou None
        """
        clients = self.load_clients()
        cod = cod.zfill(3)
        
        for client in clients:
            if client.cod == cod:
                return client
        
        return None
    
    def get_client_by_cnpj(self, cnpj: str) -> ClientInfo | None:
        """
        Busca um cliente pelo CNPJ.
        
        Args:
            cnpj: CNPJ (com ou sem formatação)
            
        Returns:
            Cliente encontrado ou None
        """
        clients = self.load_clients()
        cnpj_numbers = extract_numbers(cnpj)
        
        for client in clients:
            if client.cnpj:
                client_cnpj = extract_numbers(client.cnpj)
                if client_cnpj == cnpj_numbers:
                    return client
        
        return None
    
    def invalidate_cache(self):
        """Invalida o cache forçando reload na próxima chamada."""
        with self._cache_lock:
            ClientService._cache = None
            ClientService._cache_time = None
