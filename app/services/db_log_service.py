"""
Serviço para gerenciar logs de extratos no banco de dados.
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import extract, func

from app.database import SessionLocal
from app.models.extrato_log import ExtratoLog

logger = logging.getLogger(__name__)


class DatabaseLogService:
    """Serviço para persistir e consultar logs de extratos."""
    
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
    ) -> ExtratoLog:
        """
        Registra um extrato processado no banco de dados.
        
        Args:
            arquivo_original: Nome do arquivo original
            status: Status do processamento (SUCESSO, NAO_IDENTIFICADO, FALHA)
            ... outros campos opcionais
            
        Returns:
            O registro criado no banco de dados
        """
        db = SessionLocal()
        try:
            log_entry = ExtratoLog(
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
            
            logger.info(f"Log salvo no banco: ID={log_entry.id}, arquivo={arquivo_original}, status={status}")
            
            return log_entry
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar log no banco: {e}")
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
    ) -> list[ExtratoLog]:
        """
        Busca logs com filtros opcionais.
        
        Args:
            limit: Quantidade máxima de registros
            offset: Offset para paginação
            status: Filtrar por status
            cliente_nome: Filtrar por nome do cliente (parcial)
            ano: Filtrar por ano
            mes: Filtrar por mês
            
        Returns:
            Lista de logs encontrados
        """
        db = SessionLocal()
        try:
            query = db.query(ExtratoLog)
            
            if status:
                query = query.filter(ExtratoLog.status == status)
            if cliente_nome:
                query = query.filter(ExtratoLog.cliente_nome.ilike(f"%{cliente_nome}%"))
            if ano:
                query = query.filter(ExtratoLog.ano == ano)
            if mes:
                query = query.filter(ExtratoLog.mes == mes)
            
            query = query.order_by(ExtratoLog.processado_em.desc())
            query = query.limit(limit).offset(offset)
            
            return query.all()
            
        finally:
            db.close()
    
    def get_log_by_id(self, log_id: int) -> Optional[ExtratoLog]:
        """Busca um log específico pelo ID."""
        db = SessionLocal()
        try:
            return db.query(ExtratoLog).filter(ExtratoLog.id == log_id).first()
        finally:
            db.close()
    
    def get_stats(self, ano: Optional[int] = None, mes: Optional[int] = None) -> dict:
        """Retorna estatísticas gerais dos logs."""
        db = SessionLocal()
        try:
            base_query = db.query(ExtratoLog)
            if ano:
                base_query = base_query.filter(extract("year", ExtratoLog.processado_em) == ano)
            if mes:
                base_query = base_query.filter(extract("month", ExtratoLog.processado_em) == mes)

            total = base_query.count()
            sucesso = base_query.filter(ExtratoLog.status == "SUCESSO").count()

            nao_identificado_values = [
                "NAO_IDENTIFICADO",
                "NAO IDENTIFICADO",
                "NÃO IDENTIFICADO",
                "NÃƒO IDENTIFICADO",
            ]
            nao_identificado = base_query.filter(ExtratoLog.status.in_(nao_identificado_values)).count()

            falha_values = ["FALHA", "ERRO"]
            falha = base_query.filter(ExtratoLog.status.in_(falha_values)).count()
            
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
        """Retorna estatisticas agregadas por banco."""
        db = SessionLocal()
        try:
            base_query = db.query(ExtratoLog).filter(ExtratoLog.banco.ilike(f"%{banco}%"))
            if ano:
                base_query = base_query.filter(extract("year", ExtratoLog.processado_em) == ano)
            if mes:
                base_query = base_query.filter(extract("month", ExtratoLog.processado_em) == mes)

            total = base_query.count()
            sucesso = base_query.filter(ExtratoLog.status == "SUCESSO").count()

            nao_identificado_values = [
                "NAO_IDENTIFICADO",
                "NAO IDENTIFICADO",
            ]
            nao_identificado = base_query.filter(ExtratoLog.status.in_(nao_identificado_values)).count()

            falha_values = ["FALHA", "ERRO"]
            falha = base_query.filter(ExtratoLog.status.in_(falha_values)).count()

            confianca_media = (
                base_query.with_entities(func.avg(ExtratoLog.confianca_ia)).scalar()
            )

            tipos_rows = (
                db.query(
                    ExtratoLog.tipo_documento,
                    func.count(ExtratoLog.id).label("total"),
                )
                .filter(ExtratoLog.banco.ilike(f"%{banco}%"))
            )
            if ano:
                tipos_rows = tipos_rows.filter(extract("year", ExtratoLog.processado_em) == ano)
            if mes:
                tipos_rows = tipos_rows.filter(extract("month", ExtratoLog.processado_em) == mes)

            tipos_rows = (
                tipos_rows
                .filter(ExtratoLog.tipo_documento.isnot(None))
                .group_by(ExtratoLog.tipo_documento)
                .order_by(func.count(ExtratoLog.id).desc())
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


    def update_batch(self, ids: list[int], updates: dict) -> int:
        """
        Atualiza múltiplos logs com os valores fornecidos em updates.
        
        Args:
            ids: Lista de IDs para atualizar
            updates: Dicionário com campos e valores a atualizar (ex: {"banco": "SICREDI"})
            
        Returns:
            Número de registros atualizados
        """
        db = SessionLocal()
        try:
            # Filtra apenas campos válidos do modelo para evitar erros
            valid_fields = {
                k: v for k, v in updates.items() 
                if hasattr(ExtratoLog, k) and k not in ['id', 'processado_em', 'created_at']
            }
            
            if not valid_fields:
                return 0
                
            # Executa update em lote
            query = db.query(ExtratoLog).filter(ExtratoLog.id.in_(ids))
            count = query.update(valid_fields, synchronize_session=False)
            db.commit()
            
            logger.info(f"Update em lote: {count} registros atualizados. Campos: {valid_fields}")
            return count
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao atualizar logs em lote: {e}")
            raise
        finally:
            db.close()


# Instância singleton
_db_log_service: Optional[DatabaseLogService] = None


def get_db_log_service() -> DatabaseLogService:
    """Retorna instância singleton do serviço de log."""
    global _db_log_service
    if _db_log_service is None:
        _db_log_service = DatabaseLogService()
    return _db_log_service
