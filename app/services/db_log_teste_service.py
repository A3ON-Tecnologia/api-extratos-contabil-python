"""
Serviço para gerenciar logs de TESTE de extratos no banco de dados.
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.extrato_log_teste import ExtratoLogTeste

logger = logging.getLogger(__name__)


class DatabaseLogTesteService:
    """Serviço para persistir e consultar logs de teste de extratos."""
    
    def log_extrato_teste(
        self,
        arquivo_original: str,
        status: str,
        arquivo_salvo: Optional[str] = None,
        hash_arquivo: Optional[str] = None,
        cliente_nome: Optional[str] = None,
        cliente_cod: Optional[str] = None,
        cliente_cnpj: Optional[str] = None,
        banco: Optional[str] = None,
        tipo_documento: Optional[str] = None,
        agencia: Optional[str] = None,
        conta: Optional[str] = None,
        ano: Optional[int] = None,
        mes: Optional[int] = None,
        metodo_identificacao: Optional[str] = None,
        confianca_ia: Optional[float] = None,
        erro: Optional[str] = None,
    ) -> ExtratoLogTeste:
        """
        Registra um TESTE de extrato processado no banco de dados.
        
        NÃO salva o arquivo efetivamente - apenas simula o processamento.
        """
        db = SessionLocal()
        try:
            log_entry = ExtratoLogTeste(
                processado_em=datetime.now(),
                arquivo_original=arquivo_original,
                arquivo_salvo=arquivo_salvo,  # Caminho que SERIA usado
                hash_arquivo=hash_arquivo,
                cliente_nome=cliente_nome,
                cliente_cod=cliente_cod,
                cliente_cnpj=cliente_cnpj,
                banco=banco,
                tipo_documento=tipo_documento,
                agencia=agencia,
                conta=conta,
                ano=ano,
                mes=mes,
                status=status,
                metodo_identificacao=metodo_identificacao,
                confianca_ia=int(confianca_ia * 100) if confianca_ia else None,
                erro=erro,
                modo_teste=1,
            )
            
            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)
            
            logger.info(f"Log de TESTE salvo: ID={log_entry.id}, arquivo={arquivo_original}, status={status}")
            
            return log_entry
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar log de teste: {e}")
            raise
        finally:
            db.close()
    
    def get_logs_teste(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        cliente_nome: Optional[str] = None,
    ) -> list[ExtratoLogTeste]:
        """Busca logs de teste com filtros opcionais."""
        db = SessionLocal()
        try:
            query = db.query(ExtratoLogTeste)
            
            if status:
                query = query.filter(ExtratoLogTeste.status == status)
            if cliente_nome:
                query = query.filter(ExtratoLogTeste.cliente_nome.ilike(f"%{cliente_nome}%"))
            
            query = query.order_by(ExtratoLogTeste.processado_em.desc())
            query = query.limit(limit).offset(offset)
            
            return query.all()
            
        finally:
            db.close()
    
    def get_stats_teste(self) -> dict:
        """Retorna estatísticas dos logs de teste."""
        db = SessionLocal()
        try:
            total = db.query(ExtratoLogTeste).count()
            sucesso = db.query(ExtratoLogTeste).filter(ExtratoLogTeste.status == "SUCESSO").count()
            nao_identificado_values = [
                "NAO_IDENTIFICADO",
                "NAO IDENTIFICADO",
                "NÃO IDENTIFICADO",
                "NÃƒO IDENTIFICADO",
            ]
            nao_identificado = db.query(ExtratoLogTeste).filter(ExtratoLogTeste.status.in_(nao_identificado_values)).count()
            falha = db.query(ExtratoLogTeste).filter(ExtratoLogTeste.status == "FALHA").count()
            
            return {
                "total": total,
                "sucesso": sucesso,
                "nao_identificado": nao_identificado,
                "falha": falha,
                "modo": "TESTE"
            }
        finally:
            db.close()
    
    def limpar_logs_teste(self) -> int:
        """Limpa todos os logs de teste."""
        db = SessionLocal()
        try:
            count = db.query(ExtratoLogTeste).delete()
            db.commit()
            logger.info(f"Logs de teste limpos: {count} registros removidos")
            return count
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao limpar logs de teste: {e}")
            raise
        finally:
            db.close()


# Instância singleton
_db_log_teste_service: Optional[DatabaseLogTesteService] = None


def get_db_log_teste_service() -> DatabaseLogTesteService:
    """Retorna instância singleton do serviço de log de teste."""
    global _db_log_teste_service
    if _db_log_teste_service is None:
        _db_log_teste_service = DatabaseLogTesteService()
    return _db_log_teste_service
