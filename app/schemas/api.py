"""
Schemas para requisicoes e respostas da API.

Define estruturas para comunicacao via HTTP.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .client import MatchMethod


class ProcessingStatus(str, Enum):
    """Status do processamento de um arquivo."""

    SUCESSO = "SUCESSO"
    NAO_IDENTIFICADO = "NAO_IDENTIFICADO"
    FALHA = "FALHA"
    DUPLICADO = "DUPLICADO"


class ProcessingResult(BaseModel):
    """
    Resultado do processamento de um unico arquivo PDF.

    Retornado para cada PDF processado, incluindo PDFs extraidos de ZIP.
    """

    nome_arquivo_original: str = Field(
        description="Nome original do arquivo recebido"
    )

    nome_arquivo_final: str | None = Field(
        default=None,
        description="Nome do arquivo salvo com caminho"
    )

    status: ProcessingStatus = Field(
        description="Status do processamento"
    )

    cliente_identificado: str | None = Field(
        default=None,
        description="Nome do cliente identificado"
    )

    metodo_identificacao: MatchMethod | None = Field(
        default=None,
        description="Metodo usado para identificar o cliente"
    )

    tipo_documento: str | None = Field(
        default=None,
        description="Tipo do documento identificado pela LLM"
    )

    ano: int | None = Field(default=None, description="Ano do documento")
    mes: int | None = Field(default=None, description="Mes do documento")

    erro: str | None = Field(
        default=None,
        description="Mensagem de erro, se houver"
    )

    hash_arquivo: str | None = Field(
        default=None,
        description="Hash SHA256 do arquivo para idempotencia"
    )

    log_id: int | None = Field(
        default=None,
        description="ID do log no banco, se registrado"
    )


class UploadResponse(BaseModel):
    """
    Resposta da API para um upload.

    Contem o resultado do processamento de todos os arquivos.
    """

    sucesso: bool = Field(
        description="True se pelo menos um arquivo foi processado com sucesso"
    )

    total_arquivos: int = Field(
        description="Total de arquivos processados"
    )

    arquivos_sucesso: int = Field(
        default=0,
        description="Quantidade de arquivos processados com sucesso"
    )

    arquivos_nao_identificados: int = Field(
        default=0,
        description="Quantidade de arquivos nao identificados"
    )

    arquivos_falha: int = Field(
        default=0,
        description="Quantidade de arquivos com falha"
    )

    resultados: list[ProcessingResult] = Field(
        default_factory=list,
        description="Resultado detalhado de cada arquivo"
    )

    auditoria: dict | None = Field(
        default=None,
        description="Resumo de auditoria do processamento em lote, por exemplo ZIP"
    )

    processado_em: datetime = Field(
        default_factory=datetime.now,
        description="Data e hora do processamento"
    )
