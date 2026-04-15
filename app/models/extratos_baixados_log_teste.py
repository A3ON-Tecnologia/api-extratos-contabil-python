"""
Modelo para log de testes de extratos baixados processados.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from app.database import Base


class ExtratosBaixadosLogTeste(Base):
    """
    Registro de testes de extratos baixados processados.
    """

    __tablename__ = "extratos_baixados_log_teste"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Data e hora do processamento
    processado_em = Column(DateTime, default=datetime.now, nullable=False)

    # Informacoes do arquivo
    arquivo_original = Column(String(500), nullable=False)
    arquivo_salvo = Column(String(1000), nullable=True)
    hash_arquivo = Column(String(64), nullable=True)

    # Informacoes do cliente
    cliente_nome = Column(String(500), nullable=True)
    cliente_cod = Column(String(20), nullable=True)
    cliente_cnpj = Column(String(20), nullable=True)

    # Informacoes do banco/extrato
    banco = Column(String(100), nullable=True)
    tipo_documento = Column(String(50), nullable=True)
    agencia = Column(String(20), nullable=True)
    conta = Column(String(30), nullable=True)

    # Periodo do extrato
    ano = Column(Integer, nullable=True)
    mes = Column(Integer, nullable=True)

    # Status do processamento
    status = Column(String(50), nullable=False)
    metodo_identificacao = Column(String(50), nullable=True)
    manually_moved = Column(Boolean, default=False, nullable=False)

    # Confianca da IA
    confianca_ia = Column(Integer, nullable=True)

    # Erro (se houver)
    erro = Column(Text, nullable=True)

    # Indica se e teste
    modo_teste = Column(Integer, default=1)

    def __repr__(self):
        return f"<ExtratosBaixadosLogTeste(id={self.id}, cliente={self.cliente_nome}, status={self.status})>"

    def to_dict(self):
        """Converte o modelo para dicionario."""
        return {
            "id": self.id,
            "processado_em": self.processado_em.isoformat() if self.processado_em else None,
            "arquivo_original": self.arquivo_original,
            "arquivo_salvo": self.arquivo_salvo,
            "hash_arquivo": self.hash_arquivo,
            "cliente_nome": self.cliente_nome,
            "cliente_cod": self.cliente_cod,
            "cliente_cnpj": self.cliente_cnpj,
            "banco": self.banco,
            "tipo_documento": self.tipo_documento,
            "agencia": self.agencia,
            "conta": self.conta,
            "ano": self.ano,
            "mes": self.mes,
            "status": self.status,
            "metodo_identificacao": self.metodo_identificacao,
            "confianca_ia": self.confianca_ia,
            "erro": self.erro,
            "modo_teste": True,
        }
