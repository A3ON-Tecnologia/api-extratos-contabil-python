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
from app.models.reversao_log import ReversaoLog

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
    
    def _registrar_reversao(
        self,
        db: Session,
        log: ExtratoLog,
        arquivo_deletado: bool,
        tipo_reversao: str = "INDIVIDUAL",
        motivo: Optional[str] = None
    ) -> ReversaoLog:
        """
        Registra uma reversão na tabela de logs.
        
        Args:
            db: Sessão do banco de dados
            log: Registro original do ExtratoLog
            arquivo_deletado: Se o arquivo foi deletado do disco
            tipo_reversao: Tipo da reversão (INDIVIDUAL, LOTE, ULTIMOS)
            motivo: Motivo opcional da reversão
        
        Returns:
            Registro de reversão criado
        """
        reversao_log = ReversaoLog(
            extrato_log_id=log.id,
            arquivo_original=log.arquivo_original,
            arquivo_salvo=log.arquivo_salvo,
            cliente_nome=log.cliente_nome,
            cliente_cod=log.cliente_cod,
            banco=log.banco,
            tipo_documento=log.tipo_documento,
            ano=log.ano,
            mes=log.mes,
            status_original=log.status,
            arquivo_deletado=arquivo_deletado,
            tipo_reversao=tipo_reversao,
            motivo=motivo,
        )
        db.add(reversao_log)
        return reversao_log
    
    def reverter_por_id(
        self, 
        log_id: int, 
        deletar_arquivo: bool = True,
        motivo: Optional[str] = None
    ) -> dict:
        """
        Reverte um único processamento pelo ID.
        
        Args:
            log_id: ID do registro no banco
            deletar_arquivo: Se True, deleta o arquivo do disco
            motivo: Motivo opcional da reversão
        
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
            
            # Registra a reversão ANTES de deletar o log original
            cliente_nome = log.cliente_nome
            self._registrar_reversao(
                db=db,
                log=log,
                arquivo_deletado=arquivo_deletado,
                tipo_reversao="INDIVIDUAL",
                motivo=motivo
            )
            
            # Deleta registro do banco
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
        deletar_arquivos: bool = True,
        motivo: Optional[str] = None
    ) -> dict:
        """
        Reverte múltiplos processamentos.
        
        Args:
            ids: Lista de IDs para reverter
            deletar_arquivos: Se True, deleta os arquivos do disco
            motivo: Motivo opcional da reversão
        
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
        
        db = SessionLocal()
        try:
            for log_id in ids:
                log = db.query(ExtratoLog).filter(ExtratoLog.id == log_id).first()
                
                if not log:
                    resultados["falha"] += 1
                    resultados["erros"].append({
                        "id": log_id,
                        "erro": f"Registro {log_id} não encontrado"
                    })
                    continue
                
                arquivo_deletado = False
                arquivo_path = log.arquivo_salvo
                
                # Deleta arquivo do disco
                if deletar_arquivos and arquivo_path:
                    path = Path(arquivo_path)
                    if path.exists():
                        try:
                            path.unlink()
                            arquivo_deletado = True
                            resultados["arquivos_deletados"] += 1
                            logger.info(f"Arquivo deletado: {path}")
                        except Exception as e:
                            logger.error(f"Erro ao deletar arquivo {path}: {e}")
                
                # Registra a reversão
                self._registrar_reversao(
                    db=db,
                    log=log,
                    arquivo_deletado=arquivo_deletado,
                    tipo_reversao="LOTE",
                    motivo=motivo
                )
                
                # Deleta registro do banco
                db.delete(log)
                resultados["sucesso"] += 1
            
            db.commit()
            
            # Formata resposta para o frontend
            return {
                "success": True,
                "message": "Reversão em lote processada",
                "revertidos": resultados["sucesso"],
                "erros": resultados["falha"],  # Count
                "arquivos_deletados": resultados["arquivos_deletados"],
                "detalhes_erros": resultados["erros"]  # List
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao reverter lote: {e}")
            return {
                "success": False,
                "message": str(e),
                "revertidos": 0,
                "erros": len(ids),
                "detalhes_erros": [{"erro_geral": str(e)}]
            }
        finally:
            db.close()
    
    def reverter_ultimos(
        self,
        quantidade: int,
        deletar_arquivos: bool = True,
        motivo: Optional[str] = None
    ) -> dict:
        """
        Reverte os últimos N processamentos.
        
        Args:
            quantidade: Número de processamentos para reverter
            deletar_arquivos: Se True, deleta os arquivos do disco
            motivo: Motivo opcional da reversão
        
        Returns:
            Dicionário com resultado da operação
        """
        db = SessionLocal()
        try:
            logs = db.query(ExtratoLog).order_by(desc(ExtratoLog.id)).limit(quantidade).all()
            ids = [log.id for log in logs]
            db.close()
            
            return self.reverter_lote(ids, deletar_arquivos, motivo)
            
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
            
            # Conta reversões realizadas
            total_reversoes = db.query(ReversaoLog).count()
            
            return {
                "total": total,
                "sucesso": sucesso,
                "nao_identificado": nao_id,
                "falha": falha,
                "arquivos_existentes": arquivos_existentes,
                "total_reversoes": total_reversoes
            }
        finally:
            db.close()
    
    def listar_reversoes(
        self,
        limit: int = 100,
        offset: int = 0,
        cliente: Optional[str] = None,
    ) -> List[dict]:
        """
        Lista histórico de reversões realizadas.
        
        Args:
            limit: Número máximo de registros
            offset: Offset para paginação
            cliente: Filtrar por nome do cliente (parcial)
        
        Returns:
            Lista de dicionários com informações das reversões
        """
        db = SessionLocal()
        try:
            query = db.query(ReversaoLog).order_by(desc(ReversaoLog.id))
            
            if cliente:
                query = query.filter(ReversaoLog.cliente_nome.ilike(f"%{cliente}%"))
            
            query = query.limit(limit).offset(offset)
            reversoes = query.all()
            
            return [r.to_dict() for r in reversoes]
            
        finally:
            db.close()
    
    def get_stats_reversoes(self) -> dict:
        """Retorna estatísticas das reversões."""
        db = SessionLocal()
        try:
            total = db.query(ReversaoLog).count()
            arquivos_deletados = db.query(ReversaoLog).filter(ReversaoLog.arquivo_deletado == True).count()
            por_lote = db.query(ReversaoLog).filter(ReversaoLog.tipo_reversao == "LOTE").count()
            individual = db.query(ReversaoLog).filter(ReversaoLog.tipo_reversao == "INDIVIDUAL").count()
            
            return {
                "total_reversoes": total,
                "arquivos_deletados": arquivos_deletados,
                "reversoes_em_lote": por_lote,
                "reversoes_individuais": individual,
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

