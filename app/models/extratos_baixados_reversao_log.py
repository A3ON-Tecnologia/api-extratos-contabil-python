"""
Modelo para log de reversoes de extratos baixados.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from app.database import Base


class ExtratosBaixadosReversaoLog(Base):
    """
    Registro de cada reversao realizada para extratos baixados.
    """

    __tablename__ = "extratos_baixados_reversoes_log"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Data e hora da reversao
    revertido_em = Column(DateTime, default=datetime.now, nullable=False)

    # ID do registro original que foi revertido (extratos_baixados_log)
    extrato_log_id = Column(Integer, nullable=False)

    # Snapshot do registro original
    arquivo_original = Column(String(500), nullable=True)
    arquivo_salvo = Column(String(1000), nullable=True)
    cliente_nome = Column(String(500), nullable=True)
    cliente_cod = Column(String(20), nullable=True)
    banco = Column(String(100), nullable=True)
    tipo_documento = Column(String(50), nullable=True)
    ano = Column(Integer, nullable=True)
    mes = Column(Integer, nullable=True)
    status_original = Column(String(50), nullable=True)

    # Resultado da reversao
    arquivo_deletado = Column(Boolean, default=False)
    motivo = Column(Text, nullable=True)

    # Tipo de reversao
    tipo_reversao = Column(String(50), default="INDIVIDUAL")

    def __repr__(self):
        return (
            "<ExtratosBaixadosReversaoLog(id=%s, extrato_log_id=%s, cliente=%s)>"
            % (self.id, self.extrato_log_id, self.cliente_nome)
        )

    def to_dict(self):
        """Converte o modelo para dicionario."""
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
