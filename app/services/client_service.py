"""
Serviço de leitura da planilha de clientes.

Carrega e mantém em cache a lista de clientes da planilha Excel.
"""

import logging
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
            
            if not self.settings.clients_excel_path.exists():
                raise FileNotFoundError(
                    f"Planilha de clientes não encontrada: {self.settings.clients_excel_path}"
                )
            
            try:
                df = pd.read_excel(
                    self.settings.clients_excel_path,
                    dtype=str,  # Lê tudo como string para evitar conversões
                    engine="openpyxl",
                )
            except Exception as e:
                raise ValueError(f"Erro ao ler planilha de clientes: {e}")
            
            # Normaliza nomes das colunas (remove espaços, uppercase)
            df.columns = df.columns.str.strip().str.upper()
            
            # Verifica colunas obrigatórias
            required_columns = {"COD", "NOME"}
            missing = required_columns - set(df.columns)
            if missing:
                raise ValueError(
                    f"Colunas obrigatórias faltando na planilha: {missing}"
                )
            
            # Converte para lista de ClientInfo
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
                    conta=self._clean_value(row.get("Nº CONTA") or row.get("CONTA")),
                )
                
                clients.append(client)
            
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

        if not excel_path.exists():
            raise FileNotFoundError(f"Planilha de clientes nao encontrada: {excel_path}")

        try:
            df = pd.read_excel(
                excel_path,
                dtype=str,
                engine="openpyxl",
            )
        except Exception as e:
            raise ValueError(f"Erro ao ler planilha de clientes: {e}")

        df.columns = df.columns.str.strip().str.upper()

        required_columns = {"COD", "NOME"}
        missing = required_columns - set(df.columns)
        if missing:
            raise ValueError(f"Colunas obrigatorias faltando na planilha: {missing}")

        clients: list[ClientInfo] = []

        for _, row in df.iterrows():
            cod = str(row.get("COD", "")).strip()
            nome = str(row.get("NOME", "")).strip()

            if not cod or not nome or cod.lower() == "nan":
                continue

            cod = cod.zfill(3)

            client = ClientInfo(
                cod=cod,
                nome=nome,
                cnpj=self._clean_value(row.get("CNPJ")),
                banco=self._clean_value(row.get("BANCO")),
                agencia=self._clean_value(row.get("AGENCIA")),
                conta=self._clean_value(row.get("NÂº CONTA") or row.get("CONTA")),
            )

            clients.append(client)

        logger.info(f"Carregados {len(clients)} clientes da planilha (override)")
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
