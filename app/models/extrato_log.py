"""
Modelo para log de extratos processados.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text
from app.database import Base


class ExtratoLog(Base):
    """
    Registro de cada extrato processado pelo sistema.
    
    Guarda informações completas para auditoria e consulta posterior.
    """
    
    __tablename__ = "extratos_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Data e hora do processamento
    processado_em = Column(DateTime, default=datetime.now, nullable=False)
    
    # Informações do arquivo
    arquivo_original = Column(String(500), nullable=False)
    arquivo_salvo = Column(String(1000), nullable=True)
    hash_arquivo = Column(String(64), nullable=True)
    
    # Informações do cliente
    cliente_nome = Column(String(500), nullable=True)
    cliente_cod = Column(String(20), nullable=True)
    cliente_cnpj = Column(String(20), nullable=True)
    
    # Informações do banco/extrato
    banco = Column(String(100), nullable=True)
    tipo_documento = Column(String(50), nullable=True)
    agencia = Column(String(20), nullable=True)
    conta = Column(String(30), nullable=True)
    
    # Período do extrato
    ano = Column(Integer, nullable=True)
    mes = Column(Integer, nullable=True)
    
    # Status do processamento
    status = Column(String(50), nullable=False)  # SUCESSO, NAO_IDENTIFICADO, FALHA
    metodo_identificacao = Column(String(50), nullable=True)  # CNPJ, CONTA, NOME, NAO_IDENTIFICADO
    
    # Confiança da IA
    confianca_ia = Column(Integer, nullable=True)  # 0-100
    
    # Erro (se houver)
    erro = Column(Text, nullable=True)
    
    def __repr__(self):
        return f"<ExtratoLog(id={self.id}, cliente={self.cliente_nome}, status={self.status})>"
    
    def to_dict(self):
        """Converte o modelo para dicionário."""
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
        }
