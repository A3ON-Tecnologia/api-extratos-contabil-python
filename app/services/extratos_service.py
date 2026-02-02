"""
Serviço de leitura da planilha RELAÇÃO EXTRATOS.

Carrega e gerencia a planilha que define quais extratos devem ser processados.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd

from app.config import get_settings

logger = logging.getLogger(__name__)


class ExtratosService:
    """Serviço para gerenciamento da planilha RELAÇÃO EXTRATOS."""

    # Cache compartilhado entre instâncias
    _cache: pd.DataFrame | None = None
    _cache_time: datetime | None = None
    _cache_lock = Lock()

    # Tempo de cache em minutos
    CACHE_DURATION_MINUTES = 5

    def __init__(self):
        """Inicializa o serviço."""
        self.settings = get_settings()

    def load_extratos(self, force_reload: bool = False) -> pd.DataFrame:
        """
        Carrega a planilha RELAÇÃO EXTRATOS.

        Utiliza cache para evitar leituras repetidas do arquivo.

        Args:
            force_reload: Se True, força recarregamento ignorando cache

        Returns:
            DataFrame com os extratos

        Raises:
            FileNotFoundError: Se a planilha não existir
            ValueError: Se a planilha não tiver as colunas obrigatórias
        """
        with self._cache_lock:
            # Verifica se pode usar cache
            if not force_reload and self._cache is not None and self._cache_time is not None:
                cache_age = datetime.now() - self._cache_time
                if cache_age < timedelta(minutes=self.CACHE_DURATION_MINUTES):
                    logger.debug("Retornando extratos do cache")
                    return self._cache.copy()

            # Carrega do arquivo
            logger.info("Carregando planilha RELAÇÃO EXTRATOS...")
            excel_path = self.settings.extratos_excel_path

            if not excel_path.exists():
                raise FileNotFoundError(f"Planilha não encontrada: {excel_path}")

            try:
                # Le a planilha (Excel ou CSV)
                suffix = excel_path.suffix.lower()
                if suffix == ".csv":
                    df = pd.read_csv(excel_path, sep=None, engine="python")
                else:
                    df = pd.read_excel(excel_path, engine="openpyxl")

                logger.info(f"Planilha carregada com {len(df)} registros")
                logger.info(f"Colunas encontradas: {list(df.columns)}")

                # Atualiza cache
                self._cache = df
                self._cache_time = datetime.now()

                return df.copy()

            except Exception as e:
                logger.error(f"Erro ao ler planilha RELAÇÃO EXTRATOS: {e}")
                raise ValueError(f"Erro ao processar planilha: {e}")

    def find_cliente_by_info(self, cnpj: str = None, nome: str = None, banco: str = None, conta: str = None, agencia: str = None) -> dict | None:
        """
        Busca cliente na planilha usando as informações extraídas.

        Args:
            cnpj: CNPJ do cliente
            nome: Nome do cliente
            banco: Nome do banco
            conta: Número da conta
            agencia: Número da agência

        Returns:
            Dicionário com informações do cliente encontrado ou None
        """
        df = self.load_extratos()

        # Normaliza os dados para busca
        if cnpj:
            cnpj_clean = ''.join(filter(str.isdigit, cnpj))
            if len(cnpj_clean) >= 8:  # Pelo menos 8 dígitos
                # Busca por CNPJ
                for idx, row in df.iterrows():
                    row_cnpj = str(row.get('CNPJ', ''))
                    row_cnpj_clean = ''.join(filter(str.isdigit, row_cnpj))

                    if cnpj_clean in row_cnpj_clean or row_cnpj_clean in cnpj_clean:
                        logger.info(f"Cliente encontrado por CNPJ: {row.get('NOME', 'N/A')}")
                        return {
                            'nome': row.get('NOME'),
                            'cnpj': row.get('CNPJ'),
                            'cod': row.get('COD'),
                            'pasta': row.get('PASTA'),
                            'metodo': 'CNPJ'
                        }

        # Busca por conta e agência (mais específico)
        if conta and agencia and banco:
            conta_clean = ''.join(filter(str.isdigit, conta))
            agencia_clean = ''.join(filter(str.isdigit, agencia))

            for idx, row in df.iterrows():
                row_banco = str(row.get('BANCO', '')).upper()
                row_conta = str(row.get('CONTA', ''))
                row_agencia = str(row.get('AGENCIA', ''))

                row_conta_clean = ''.join(filter(str.isdigit, row_conta))
                row_agencia_clean = ''.join(filter(str.isdigit, row_agencia))

                # Verifica se banco, conta e agência batem
                if banco.upper() in row_banco and conta_clean == row_conta_clean and agencia_clean == row_agencia_clean:
                    logger.info(f"Cliente encontrado por CONTA+AGENCIA: {row.get('NOME', 'N/A')}")
                    return {
                        'nome': row.get('NOME'),
                        'cnpj': row.get('CNPJ'),
                        'cod': row.get('COD'),
                        'pasta': row.get('PASTA'),
                        'metodo': 'CONTA_AGENCIA'
                    }

        # Busca por nome (menos preciso)
        if nome:
            nome_upper = nome.upper()
            for idx, row in df.iterrows():
                row_nome = str(row.get('NOME', '')).upper()

                if nome_upper in row_nome or row_nome in nome_upper:
                    logger.info(f"Cliente encontrado por NOME: {row.get('NOME', 'N/A')}")
                    return {
                        'nome': row.get('NOME'),
                        'cnpj': row.get('CNPJ'),
                        'cod': row.get('COD'),
                        'pasta': row.get('PASTA'),
                        'metodo': 'NOME'
                    }

        logger.warning("Cliente não encontrado na planilha RELAÇÃO EXTRATOS")
        return None

    def invalidate_cache(self):
        """Invalida o cache forçando recarga na próxima chamada."""
        with self._cache_lock:
            self._cache = None
            self._cache_time = None
            logger.info("Cache de extratos invalidado")

    def get_cache_info(self) -> dict:
        """
        Retorna informações sobre o cache.

        Returns:
            Dicionário com informações do cache
        """
        with self._cache_lock:
            if self._cache is None or self._cache_time is None:
                return {"cached": False}

            cache_age = datetime.now() - self._cache_time
            return {
                "cached": True,
                "total_records": len(self._cache),
                "cache_age_seconds": cache_age.total_seconds(),
                "cache_expires_in": (timedelta(minutes=self.CACHE_DURATION_MINUTES) - cache_age).total_seconds(),
            }
