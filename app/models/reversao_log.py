"""
Modelo para log de reversões realizadas.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from app.database import Base


class ReversaoLog(Base):
    """
    Registro de cada reversão realizada no sistema.
    
    Guarda informações completas para auditoria de reversões.
    """
    
    __tablename__ = "reversoes_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Data e hora da reversão
    revertido_em = Column(DateTime, default=datetime.now, nullable=False)
    
    # ID do registro original que foi revertido (da tabela extratos_log)
    extrato_log_id = Column(Integer, nullable=False)
    
    # Informações do registro original (snapshot no momento da reversão)
    arquivo_original = Column(String(500), nullable=True)
    arquivo_salvo = Column(String(1000), nullable=True)
    cliente_nome = Column(String(500), nullable=True)
    cliente_cod = Column(String(20), nullable=True)
    banco = Column(String(100), nullable=True)
    tipo_documento = Column(String(50), nullable=True)
    ano = Column(Integer, nullable=True)
    mes = Column(Integer, nullable=True)
    status_original = Column(String(50), nullable=True)  # Status que tinha antes da reversão
    
    # Resultado da reversão
    arquivo_deletado = Column(Boolean, default=False)  # Se o arquivo foi deletado do disco
    motivo = Column(Text, nullable=True)  # Motivo opcional informado pelo usuário
    
    # Tipo de reversão
    tipo_reversao = Column(String(50), default="INDIVIDUAL")  # INDIVIDUAL, LOTE, ULTIMOS
    
    def __repr__(self):
        return f"<ReversaoLog(id={self.id}, extrato_log_id={self.extrato_log_id}, cliente={self.cliente_nome})>"
    
    def to_dict(self):
        """Converte o modelo para dicionário."""
        return {
            "id": self.id,
            "revertido_em": self.revertido_em.isoformat() if self.revertido_em else None,
            "extrato_log_id": self.extrato_log_id,
            "arquivo_original": self.arquivo_original,
            "arquivo_salvo": self.arquivo_salvo,
            "cliente_nome": self.cliente_nome,
            "cliente_cod": self.cliente_cod,
            "banco": self.banco,
            "tipo_documento": self.tipo_documento,
            "ano": self.ano,
            "mes": self.mes,
            "status_original": self.status_original,
            "arquivo_deletado": self.arquivo_deletado,
            "motivo": self.motivo,
            "tipo_reversao": self.tipo_reversao,
        }
