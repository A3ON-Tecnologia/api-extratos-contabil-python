"""
Servico para gerenciar logs de extratos baixados no banco de dados.
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import extract, func

from app.database import SessionLocal
from app.models.extratos_baixados_log import ExtratosBaixadosLog

logger = logging.getLogger(__name__)


class ExtratosBaixadosLogService:
    """Servico para persistir e consultar logs de extratos baixados."""

    def log_extrato(
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
    ) -> ExtratosBaixadosLog:
        """Registra um extrato baixado processado no banco de dados."""
        db = SessionLocal()
        try:
            log_entry = ExtratosBaixadosLog(
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
            )

            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)

            logger.info(
                "Log extratos baixados salvo: ID=%s, arquivo=%s, status=%s",
                log_entry.id,
                arquivo_original,
                status,
            )

            return log_entry
        except Exception as e:
            db.rollback()
            logger.error("Erro ao salvar log de extratos baixados: %s", e)
            raise
        finally:
            db.close()

    def get_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        cliente_nome: Optional[str] = None,
        ano: Optional[int] = None,
        mes: Optional[int] = None,
        banco: Optional[str] = None,
        tipo_documento: Optional[str] = None,
        confianca_min: Optional[int] = None,
    ) -> list[ExtratosBaixadosLog]:
        """Busca logs com filtros opcionais."""
        db = SessionLocal()
        try:
            query = db.query(ExtratosBaixadosLog)

            if status:
                query = query.filter(ExtratosBaixadosLog.status == status)
            if cliente_nome:
                query = query.filter(ExtratosBaixadosLog.cliente_nome.ilike(f"%{cliente_nome}%"))
            if ano:
                query = query.filter(ExtratosBaixadosLog.ano == ano)
            if mes:
                query = query.filter(ExtratosBaixadosLog.mes == mes)
            if banco:
                query = query.filter(ExtratosBaixadosLog.banco.ilike(f"%{banco}%"))
            if tipo_documento:
                query = query.filter(ExtratosBaixadosLog.tipo_documento.ilike(f"%{tipo_documento}%"))
            if confianca_min is not None:
                query = query.filter(ExtratosBaixadosLog.confianca_ia <= confianca_min)

            query = query.order_by(ExtratosBaixadosLog.processado_em.desc())
            query = query.limit(limit).offset(offset)

            return query.all()
        finally:
            db.close()

    def get_log_by_id(self, log_id: int) -> Optional[ExtratosBaixadosLog]:
        """Busca um log especifico pelo ID."""
        db = SessionLocal()
        try:
            return db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.id == log_id).first()
        finally:
            db.close()

    def get_stats(self, ano: Optional[int] = None, mes: Optional[int] = None) -> dict:
        """Retorna estatisticas gerais dos logs."""
        db = SessionLocal()
        try:
            base_query = db.query(ExtratosBaixadosLog)
            if ano:
                base_query = base_query.filter(extract("year", ExtratosBaixadosLog.processado_em) == ano)
            if mes:
                base_query = base_query.filter(extract("month", ExtratosBaixadosLog.processado_em) == mes)

            total = base_query.count()
            sucesso = base_query.filter(ExtratosBaixadosLog.status == "SUCESSO").count()

            nao_identificado_values = [
                "NAO_IDENTIFICADO",
                "NAO IDENTIFICADO",
                "NÃO IDENTIFICADO",
                "NÃƒO IDENTIFICADO",
            ]
            nao_identificado = base_query.filter(
                ExtratosBaixadosLog.status.in_(nao_identificado_values)
            ).count()

            falha_values = ["FALHA", "ERRO"]
            falha = base_query.filter(ExtratosBaixadosLog.status.in_(falha_values)).count()

            return {
                "total": total,
                "sucesso": sucesso,
                "nao_identificado": nao_identificado,
                "falha": falha,
            }
        finally:
            db.close()

    def get_bank_stats(
        self,
        banco: str,
        ano: Optional[int] = None,
        mes: Optional[int] = None,
        top_tipos: int = 5,
    ) -> dict:
        """Retorna estatisticas agregadas por banco para extratos baixados."""
        db = SessionLocal()
        try:
            base_query = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.banco.ilike(f"%{banco}%"))
            if ano:
                base_query = base_query.filter(extract("year", ExtratosBaixadosLog.processado_em) == ano)
            if mes:
                base_query = base_query.filter(extract("month", ExtratosBaixadosLog.processado_em) == mes)

            total = base_query.count()
            sucesso = base_query.filter(ExtratosBaixadosLog.status == "SUCESSO").count()

            nao_identificado_values = [
                "NAO_IDENTIFICADO",
                "NAO IDENTIFICADO",
            ]
            nao_identificado = base_query.filter(ExtratosBaixadosLog.status.in_(nao_identificado_values)).count()

            falha_values = ["FALHA", "ERRO"]
            falha = base_query.filter(ExtratosBaixadosLog.status.in_(falha_values)).count()

            confianca_media = (
                base_query.with_entities(func.avg(ExtratosBaixadosLog.confianca_ia)).scalar()
            )

            tipos_rows = (
                db.query(
                    ExtratosBaixadosLog.tipo_documento,
                    func.count(ExtratosBaixadosLog.id).label("total"),
                )
                .filter(ExtratosBaixadosLog.banco.ilike(f"%{banco}%"))
            )
            if ano:
                tipos_rows = tipos_rows.filter(extract("year", ExtratosBaixadosLog.processado_em) == ano)
            if mes:
                tipos_rows = tipos_rows.filter(extract("month", ExtratosBaixadosLog.processado_em) == mes)

            tipos_rows = (
                tipos_rows
                .filter(ExtratosBaixadosLog.tipo_documento.isnot(None))
                .group_by(ExtratosBaixadosLog.tipo_documento)
                .order_by(func.count(ExtratosBaixadosLog.id).desc())
                .limit(max(1, min(int(top_tipos), 20)))
                .all()
            )

            taxa_sucesso = round((sucesso / total) * 100, 2) if total > 0 else 0.0

            return {
                "banco": banco,
                "periodo": {"ano": ano, "mes": mes},
                "total": total,
                "sucesso": sucesso,
                "nao_identificado": nao_identificado,
                "falha": falha,
                "taxa_sucesso": taxa_sucesso,
                "confianca_media": round(float(confianca_media), 2) if confianca_media is not None else None,
                "tipos_mais_comuns": [
                    {"tipo_documento": tipo or "-", "total": int(total_tipo)}
                    for tipo, total_tipo in tipos_rows
                ],
            }
        finally:
            db.close()



_extratos_baixados_log_service: Optional[ExtratosBaixadosLogService] = None


def get_extratos_baixados_log_service() -> ExtratosBaixadosLogService:
    """Retorna instancia singleton do servico de log de extratos baixados."""
    global _extratos_baixados_log_service
    if _extratos_baixados_log_service is None:
        _extratos_baixados_log_service = ExtratosBaixadosLogService()
    return _extratos_baixados_log_service
