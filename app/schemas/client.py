"""
Schemas relacionados a clientes e matching.

Define estruturas para representar informações de clientes
carregadas da planilha Excel e resultados de matching.
"""

from enum import Enum
from pydantic import BaseModel, Field


class MatchMethod(str, Enum):
    """Método utilizado para identificar o cliente."""
    
    CNPJ = "cnpj"
    CONTA_AGENCIA = "conta_agencia"
    NOME_SIMILARIDADE = "nome_similaridade"
    NAO_IDENTIFICADO = "nao_identificado"


class ClientInfo(BaseModel):
    """
    Informações de um cliente carregadas da planilha.
    
    Representa uma linha da planilha de clientes.
    """
    
    cod: str = Field(description="Código do cliente (ex: '098')")
    nome: str = Field(description="Nome/Razão social do cliente")
    cnpj: str | None = Field(default=None, description="CNPJ do cliente")
    banco: str | None = Field(default=None, description="Banco do cliente")
    agencia: str | None = Field(default=None, description="Agência bancária")
    conta: str | None = Field(default=None, description="Número da conta")
    tipo_documento: str | None = Field(default=None, description="Tipo de documento/extrato")
    
    @property
    def folder_name(self) -> str:
        """
        Nome da pasta do cliente no formato esperado.
        
        Returns:
            String no formato "COD - NOME" (ex: "098 - JP CONTABIL LTDA")
        """
        return f"{self.cod} - {self.nome}"


class MatchResult(BaseModel):
    """
    Resultado do processo de matching de cliente.
    
    Contém o cliente encontrado (se houver) e metadados
    sobre como o match foi realizado.
    """
    
    cliente: ClientInfo | None = Field(
        default=None,
        description="Cliente identificado, None se não encontrado"
    )
    
    metodo: MatchMethod = Field(
        default=MatchMethod.NAO_IDENTIFICADO,
        description="Método utilizado para identificar o cliente"
    )
    
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Score de confiança do match (0-100)"
    )
    
    motivo_fallback: str | None = Field(
        default=None,
        description="Motivo do fallback quando não identificado"
    )
    
    @property
    def identificado(self) -> bool:
        """Retorna True se um cliente foi identificado."""
        return self.cliente is not None
