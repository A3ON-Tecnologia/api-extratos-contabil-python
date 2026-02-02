"""
Configurações do sistema via variáveis de ambiente.

Utiliza Pydantic Settings para carregar e validar as configurações
a partir de um arquivo .env ou variáveis de ambiente do sistema.
"""

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações do sistema carregadas do .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ==================== OpenAI / LLM ====================
    openai_api_key: str
    llm_model: str = "gpt-4o-mini"

    # ==================== Caminhos do Sistema ====================
    base_path: Path
    clients_excel_path: Path
    log_excel_path: Path

    # ==================== Pasta de Extratos ====================
    extratos_excel_path: Path
    watch_folder_path: Path

    # ==================== Matching ====================
    similarity_threshold: int = 85

    # ==================== Servidor ====================
    port: int = 8888

    # ==================== Banco de Dados MySQL ====================
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    
    @property
    def unidentified_path(self) -> Path:
        """Caminho raiz para arquivos não identificados."""
        return self.base_path / "000 - AUTOMAÇÕES" / "000 - NAO_IDENTIFICADOS"

    def _unidentified_module_base(self, module: str | None) -> Path:
        """Base de pasta de não identificados por módulo."""
        module_key = (module or "").strip().lower()
        if module_key in {"make", "main", "principal"}:
            return self.unidentified_path / "NÃO IDENTIFICADOS MAKE"
        if module_key in {"extratos", "extratos_baixados", "extratos-baixados"}:
            return self.unidentified_path / "NÃO IDENTIFICADOS EXTRATOS BAIXADOS"
        return self.unidentified_path

    def get_unidentified_path(self, module: str | None, test_mode: bool = False) -> Path:
        """Retorna o caminho de não identificados por módulo."""
        return self._unidentified_module_base(module)

    @property
    def database_url(self) -> str:
        """URL de conexão com o banco de dados MySQL."""
        from urllib.parse import quote_plus
        password_escaped = quote_plus(self.db_password)
        return f"mysql+pymysql://{self.db_user}:{password_escaped}@{self.db_host}:{self.db_port}/{self.db_name}"

    def validate_paths(self) -> dict[str, bool]:
        """
        Valida se os caminhos essenciais existem.

        Returns:
            Dicionário com status de cada caminho.
        """
        return {
            "base_path": self.base_path.exists(),
            "clients_excel_path": self.clients_excel_path.exists(),
            "log_excel_path": self.log_excel_path.exists(),
            "extratos_excel_path": self.extratos_excel_path.exists(),
            "watch_folder_path": self.watch_folder_path.exists(),
            "unidentified_path": self.unidentified_path.exists(),
        }

    def validate_database_connection(self) -> dict[str, any]:
        """
        Testa a conexão com o banco de dados MySQL.

        Returns:
            Dicionário com status da conexão e mensagem.
        """
        try:
            from sqlalchemy import create_engine, text

            # Cria engine temporário para teste
            test_engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 5}
            )

            # Tenta executar query simples
            with test_engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            test_engine.dispose()

            return {
                "status": "success",
                "connected": True,
                "message": "Conexão com banco de dados estabelecida com sucesso",
                "database": self.db_name,
                "host": self.db_host,
                "port": self.db_port,
            }
        except Exception as e:
            return {
                "status": "error",
                "connected": False,
                "message": f"Erro ao conectar ao banco de dados: {str(e)}",
                "database": self.db_name,
                "host": self.db_host,
                "port": self.db_port,
            }

    def get_summary(self) -> dict:
        """
        Retorna um resumo das configurações (sem dados sensíveis).

        Returns:
            Dicionário com resumo das configurações.
        """
        return {
            "llm_model": self.llm_model,
            "base_path": str(self.base_path),
            "clients_excel_path": str(self.clients_excel_path),
            "log_excel_path": str(self.log_excel_path),
            "extratos_excel_path": str(self.extratos_excel_path),
            "watch_folder_path": str(self.watch_folder_path),
            "similarity_threshold": self.similarity_threshold,
            "port": self.port,
            "database": {
                "host": self.db_host,
                "port": self.db_port,
                "user": self.db_user,
                "name": self.db_name,
            },
        }


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
