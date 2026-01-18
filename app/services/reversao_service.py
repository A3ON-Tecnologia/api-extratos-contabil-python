"""
Serviço para gerenciar reversões de processamentos.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import SessionLocal
from app.models.extrato_log import ExtratoLog

logger = logging.getLogger(__name__)


class ReversaoService:
    """Serviço para reverter processamentos."""
    
    def listar_processamentos(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        cliente: Optional[str] = None,
        apenas_existentes: bool = False,
    ) -> List[dict]:
        """
        Lista processamentos que podem ser revertidos.
        
        Args:
            limit: Número máximo de registros
            offset: Offset para paginação
            status: Filtrar por status (SUCESSO, NAO_IDENTIFICADO, FALHA)
            cliente: Filtrar por nome do cliente (parcial)
            apenas_existentes: Se True, mostra apenas arquivos que existem no disco
        
        Returns:
            Lista de dicionários com informações dos processamentos
        """
        db = SessionLocal()
        try:
            query = db.query(ExtratoLog).order_by(desc(ExtratoLog.id))
            
            if status:
                query = query.filter(ExtratoLog.status == status)
            
            if cliente:
                query = query.filter(ExtratoLog.cliente_nome.ilike(f"%{cliente}%"))
            
            query = query.limit(limit).offset(offset)
            logs = query.all()
            
            resultado = []
            for log in logs:
                arquivo_existe = False
                if log.arquivo_salvo:
                    arquivo_existe = Path(log.arquivo_salvo).exists()
                
                # Se filtro de apenas existentes, pula os que não existem
                if apenas_existentes and not arquivo_existe:
                    continue
                
                resultado.append({
                    "id": log.id,
                    "processado_em": log.processado_em.isoformat() if log.processado_em else None,
                    "cliente_nome": log.cliente_nome,
                    "cliente_cod": log.cliente_cod,
                    "banco": log.banco,
                    "tipo_documento": log.tipo_documento,
                    "ano": log.ano,
                    "mes": log.mes,
                    "status": log.status,
                    "arquivo_original": log.arquivo_original,
                    "arquivo_salvo": log.arquivo_salvo,
                    "arquivo_existe": arquivo_existe,
                })
            
            return resultado
            
        finally:
            db.close()
    
    def reverter_por_id(self, log_id: int, deletar_arquivo: bool = True) -> dict:
        """
        Reverte um único processamento pelo ID.
        
        Args:
            log_id: ID do registro no banco
            deletar_arquivo: Se True, deleta o arquivo do disco
        
        Returns:
            Dicionário com resultado da operação
        """
        db = SessionLocal()
        try:
            log = db.query(ExtratoLog).filter(ExtratoLog.id == log_id).first()
            
            if not log:
                return {
                    "success": False,
                    "message": f"Registro {log_id} não encontrado"
                }
            
            arquivo_deletado = False
            arquivo_path = log.arquivo_salvo
            
            # Deleta arquivo do disco
            if deletar_arquivo and arquivo_path:
                path = Path(arquivo_path)
                if path.exists():
                    try:
                        path.unlink()
                        arquivo_deletado = True
                        logger.info(f"Arquivo deletado: {path}")
                    except Exception as e:
                        logger.error(f"Erro ao deletar arquivo: {e}")
                        return {
                            "success": False,
                            "message": f"Erro ao deletar arquivo: {e}"
                        }
            
            # Deleta registro do banco
            cliente_nome = log.cliente_nome
            db.delete(log)
            db.commit()
            
            logger.info(f"Registro {log_id} revertido: {cliente_nome}")
            
            return {
                "success": True,
                "message": f"Registro {log_id} revertido com sucesso",
                "arquivo_deletado": arquivo_deletado,
                "arquivo_path": arquivo_path,
                "cliente": cliente_nome
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao reverter: {e}")
            return {
                "success": False,
                "message": str(e)
            }
        finally:
            db.close()
    
    def reverter_lote(
        self,
        ids: List[int],
        deletar_arquivos: bool = True
    ) -> dict:
        """
        Reverte múltiplos processamentos.
        
        Args:
            ids: Lista de IDs para reverter
            deletar_arquivos: Se True, deleta os arquivos do disco
        
        Returns:
            Dicionário com resultado da operação
        """
        resultados = {
            "total": len(ids),
            "sucesso": 0,
            "falha": 0,
            "arquivos_deletados": 0,
            "erros": []
        }
        
        for log_id in ids:
            resultado = self.reverter_por_id(log_id, deletar_arquivos)
            
            if resultado["success"]:
                resultados["sucesso"] += 1
                if resultado.get("arquivo_deletado"):
                    resultados["arquivos_deletados"] += 1
            else:
                resultados["falha"] += 1
                resultados["erros"].append({
                    "id": log_id,
                    "erro": resultado["message"]
                })
        
        return resultados
    
    def reverter_ultimos(
        self,
        quantidade: int,
        deletar_arquivos: bool = True
    ) -> dict:
        """
        Reverte os últimos N processamentos.
        
        Args:
            quantidade: Número de processamentos para reverter
            deletar_arquivos: Se True, deleta os arquivos do disco
        
        Returns:
            Dicionário com resultado da operação
        """
        db = SessionLocal()
        try:
            logs = db.query(ExtratoLog).order_by(desc(ExtratoLog.id)).limit(quantidade).all()
            ids = [log.id for log in logs]
            db.close()
            
            return self.reverter_lote(ids, deletar_arquivos)
            
        finally:
            db.close()
    
    def get_estatisticas(self) -> dict:
        """Retorna estatísticas dos processamentos."""
        db = SessionLocal()
        try:
            total = db.query(ExtratoLog).count()
            sucesso = db.query(ExtratoLog).filter(ExtratoLog.status == "SUCESSO").count()
            nao_id = db.query(ExtratoLog).filter(ExtratoLog.status == "NAO_IDENTIFICADO").count()
            falha = db.query(ExtratoLog).filter(ExtratoLog.status == "FALHA").count()
            
            # Conta arquivos que existem
            logs_sucesso = db.query(ExtratoLog).filter(ExtratoLog.status == "SUCESSO").all()
            arquivos_existentes = sum(1 for log in logs_sucesso if log.arquivo_salvo and Path(log.arquivo_salvo).exists())
            
            return {
                "total": total,
                "sucesso": sucesso,
                "nao_identificado": nao_id,
                "falha": falha,
                "arquivos_existentes": arquivos_existentes
            }
        finally:
            db.close()


# Singleton
_reversao_service: Optional[ReversaoService] = None


def get_reversao_service() -> ReversaoService:
    """Retorna instância singleton do serviço de reversão."""
    global _reversao_service
    if _reversao_service is None:
        _reversao_service = ReversaoService()
    return _reversao_service
