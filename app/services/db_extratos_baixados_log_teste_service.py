"""
Servico para gerenciar logs de teste de extratos baixados no banco de dados.
"""

import logging
from datetime import datetime
from typing import Optional

from app.database import SessionLocal
from app.models.extratos_baixados_log_teste import ExtratosBaixadosLogTeste

logger = logging.getLogger(__name__)


class ExtratosBaixadosLogTesteService:
    """Servico para persistir e consultar logs de teste de extratos baixados."""

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
    ) -> ExtratosBaixadosLogTeste:
        """Registra um teste de extrato baixado processado no banco de dados."""
        db = SessionLocal()
        try:
            log_entry = ExtratosBaixadosLogTeste(
                processado_em=datetime.now(),
                arquivo_original=arquivo_original,
                arquivo_salvo=arquivo_salvo,
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

            logger.info(
                "Log de teste extratos baixados salvo: ID=%s, arquivo=%s, status=%s",
                log_entry.id,
                arquivo_original,
                status,
            )

            return log_entry
        except Exception as e:
            db.rollback()
            logger.error("Erro ao salvar log de teste extratos baixados: %s", e)
            raise
        finally:
            db.close()

    def get_logs_teste(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        cliente_nome: Optional[str] = None,
    ) -> list[ExtratosBaixadosLogTeste]:
        """Busca logs de teste com filtros opcionais."""
        db = SessionLocal()
        try:
            query = db.query(ExtratosBaixadosLogTeste)

            if status:
                query = query.filter(ExtratosBaixadosLogTeste.status == status)
            if cliente_nome:
                query = query.filter(ExtratosBaixadosLogTeste.cliente_nome.ilike(f"%{cliente_nome}%"))

            query = query.order_by(ExtratosBaixadosLogTeste.processado_em.desc())
            query = query.limit(limit).offset(offset)

            return query.all()
        finally:
            db.close()

    def get_stats_teste(self) -> dict:
        """Retorna estatisticas dos logs de teste."""
        db = SessionLocal()
        try:
            total = db.query(ExtratosBaixadosLogTeste).count()
            sucesso = db.query(ExtratosBaixadosLogTeste).filter(ExtratosBaixadosLogTeste.status == "SUCESSO").count()
            nao_identificado = db.query(ExtratosBaixadosLogTeste).filter(
                ExtratosBaixadosLogTeste.status == "NAO_IDENTIFICADO"
            ).count()
            falha = db.query(ExtratosBaixadosLogTeste).filter(ExtratosBaixadosLogTeste.status == "FALHA").count()

            return {
                "total": total,
                "sucesso": sucesso,
                "nao_identificado": nao_identificado,
                "falha": falha,
                "modo": "TESTE",
            }
        finally:
            db.close()

    def limpar_logs_teste(self) -> int:
        """Limpa todos os logs de teste."""
        db = SessionLocal()
        try:
            count = db.query(ExtratosBaixadosLogTeste).delete()
            db.commit()
            logger.info("Logs de teste extratos baixados limpos: %s registros removidos", count)
            return count
        except Exception as e:
            db.rollback()
            logger.error("Erro ao limpar logs de teste extratos baixados: %s", e)
            raise
        finally:
            db.close()

    def delete_log_teste(self, log_id: int) -> None:
        """Remove um log de teste especifico pelo ID."""
        db = SessionLocal()
        try:
            log_entry = db.query(ExtratosBaixadosLogTeste).filter(ExtratosBaixadosLogTeste.id == log_id).first()
            if not log_entry:
                raise ValueError(f"Log de teste nao encontrado: {log_id}")
            db.delete(log_entry)
            db.commit()
            logger.info("Log de teste extratos baixados removido: ID=%s", log_id)
        except Exception as e:
            db.rollback()
            logger.error("Erro ao remover log de teste extratos baixados: %s", e)
            raise
        finally:
            db.close()


_extratos_baixados_log_teste_service: Optional[ExtratosBaixadosLogTesteService] = None


def get_extratos_baixados_log_teste_service() -> ExtratosBaixadosLogTesteService:
    """Retorna instancia singleton do servico de log de teste."""
    global _extratos_baixados_log_teste_service
    if _extratos_baixados_log_teste_service is None:
        _extratos_baixados_log_teste_service = ExtratosBaixadosLogTesteService()
    return _extratos_baixados_log_teste_service
