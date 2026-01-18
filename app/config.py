"""
Configurações do sistema via variáveis de ambiente.

Utiliza Pydantic Settings para carregar e validar as configurações
a partir de um arquivo .env ou variáveis de ambiente do sistema.
"""

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações do sistema."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    # OpenAI
    openai_api_key: str
    llm_model: str = "gpt-4o-mini"
    
    # Caminhos
    base_path: Path = Path(r"\\JPDC2\Dados$\JP Digital")
    clients_excel_path: Path = Path(r"\\JPDC2\Dados$\JP Digital\000 - AUTOMAÇÕES\RELAÇÃO CLIENTES.xlsx")
    log_excel_path: Path = Path(r"\\JPDC2\Dados$\JP Digital\000 - AUTOMAÇÕES\LOGS SUCESSO _ FALHA.xlsx")
    
    # Matching
    similarity_threshold: int = 85
    
    # Servidor
    port: int = 8000
    
    @property
    def unidentified_path(self) -> Path:
        """Caminho para arquivos não identificados."""
        return self.base_path / "000 - NAO_IDENTIFICADOS"


@lru_cache
def get_settings() -> Settings:
    """
    Retorna instância cacheada das configurações.
    
    O cache evita recarregar o .env a cada chamada.
    """
    return Settings()


def clear_settings_cache() -> Settings:
    """
    Limpa o cache das configurações e retorna novas configurações.
    
    Use esta função após modificar o arquivo .env para forçar releitura.
    """
    get_settings.cache_clear()
    return get_settings()
