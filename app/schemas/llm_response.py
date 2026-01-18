"""
Schema do retorno estruturado da LLM.

Define a estrutura esperada do JSON retornado pela LLM
após análise do texto do documento.
"""

from pydantic import BaseModel, Field


class LLMExtractionResult(BaseModel):
    """
    Resultado da extração de informações do documento pela LLM.
    
    Attributes:
        cliente_sugerido: Nome do cliente sugerido pela LLM
        cnpj: CNPJ encontrado no documento (formato XX.XXX.XXX/XXXX-XX)
        banco: Nome do banco identificado
        agencia: Número da agência bancária
        conta: Número da conta bancária
        tipo_documento: Tipo do documento (ex: "extrato bancário")
        ano: Ano do período do documento
        mes: Mês do período do documento (1-12)
        confianca: Nível de confiança da extração (0.0 a 1.0)
    """
    
    cliente_sugerido: str | None = Field(
        default=None,
        description="Nome do cliente/empresa identificado no documento"
    )
    
    cnpj: str | None = Field(
        default=None,
        description="CNPJ encontrado no documento"
    )
    
    banco: str | None = Field(
        default=None,
        description="Nome do banco identificado"
    )
    
    agencia: str | None = Field(
        default=None,
        description="Número da agência bancária"
    )
    
    conta: str | None = Field(
        default=None,
        description="Número da conta bancária"
    )
    
    tipo_documento: str = Field(
        default="documento",
        description="Tipo do documento identificado"
    )
    
    ano: int | None = Field(
        default=None,
        ge=2000,
        le=2100,
        description="Ano do período do documento"
    )
    
    mes: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description="Mês do período do documento"
    )
    
    confianca: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Nível de confiança da extração"
    )
