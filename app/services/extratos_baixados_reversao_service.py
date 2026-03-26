"""
Servico para gerenciar reversoes de extratos baixados.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional, List
from sqlalchemy import desc

from app.config import get_settings
from app.database import SessionLocal
from app.models.extratos_baixados_log import ExtratosBaixadosLog
from app.models.extratos_baixados_reversao_log import ExtratosBaixadosReversaoLog

logger = logging.getLogger(__name__)


class ExtratosBaixadosReversaoService:
    """Servico para reverter processamentos de extratos baixados."""

    def listar_processamentos(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        cliente: Optional[str] = None,
        apenas_existentes: bool = False,
    ) -> List[dict]:
        """Lista processamentos que podem ser revertidos."""
        db = SessionLocal()
        try:
            query = db.query(ExtratosBaixadosLog).order_by(desc(ExtratosBaixadosLog.id))

            if status:
                query = query.filter(ExtratosBaixadosLog.status == status)
            if cliente:
                query = query.filter(ExtratosBaixadosLog.cliente_nome.ilike(f"%{cliente}%"))

            query = query.limit(limit).offset(offset)
            logs = query.all()

            resultado = []
            for log in logs:
                arquivo_existe = False
                if log.arquivo_salvo:
                    arquivo_existe = Path(log.arquivo_salvo).exists()

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
        db,
        log: ExtratosBaixadosLog,
        arquivo_deletado: bool,
        tipo_reversao: str = "INDIVIDUAL",
        motivo: Optional[str] = None,
    ) -> ExtratosBaixadosReversaoLog:
        reversao_log = ExtratosBaixadosReversaoLog(
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
        motivo: Optional[str] = None,
    ) -> dict:
        """Reverte um unico processamento pelo ID."""
        db = SessionLocal()
        try:
            log = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.id == log_id).first()

            if not log:
                return {"success": False, "message": f"Registro {log_id} nao encontrado"}

            arquivo_deletado = False
            arquivo_restaurado = False
            destino_restauracao = None

            if log.arquivo_salvo:
                path = Path(log.arquivo_salvo)
                if path.exists():
                    # Tenta mover de volta para a pasta do watcher
                    if log.arquivo_original:
                        try:
                            watch_path = get_settings().watch_folder_path
                            destino = watch_path / log.arquivo_original
                            destino.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(path), str(destino))
                            arquivo_restaurado = True
                            destino_restauracao = str(destino)
                            logger.info(
                                "Arquivo restaurado para fila: %s -> %s",
                                path,
                                destino,
                            )
                        except Exception as e:
                            logger.error("Erro ao restaurar arquivo para fila: %s", e)
                            # Fallback: apaga se não conseguiu mover
                            if deletar_arquivo:
                                try:
                                    path.unlink()
                                    arquivo_deletado = True
                                except Exception as e2:
                                    logger.error("Erro ao deletar arquivo: %s", e2)
                                    return {"success": False, "message": f"Erro ao mover/deletar arquivo: {e}"}
                    elif deletar_arquivo:
                        try:
                            path.unlink()
                            arquivo_deletado = True
                        except Exception as e:
                            logger.error("Erro ao deletar arquivo: %s", e)
                            return {"success": False, "message": f"Erro ao deletar arquivo: {e}"}

            self._registrar_reversao(
                db=db,
                log=log,
                arquivo_deletado=arquivo_deletado,
                tipo_reversao="INDIVIDUAL",
                motivo=motivo,
            )

            db.delete(log)
            db.commit()

            return {
                "success": True,
                "message": f"Registro {log_id} revertido com sucesso",
                "arquivo_deletado": arquivo_deletado,
                "arquivo_restaurado": arquivo_restaurado,
                "arquivo_path": log.arquivo_salvo,
                "destino_restauracao": destino_restauracao,
                "cliente": log.cliente_nome,
            }
        except Exception as e:
            db.rollback()
            logger.error("Erro ao reverter extratos baixados: %s", e)
            return {"success": False, "message": str(e)}
        finally:
            db.close()

    def reverter_lote(
        self,
        ids: List[int],
        deletar_arquivos: bool = True,
        motivo: Optional[str] = None,
    ) -> dict:
        """Reverte multiplos processamentos."""
        resultados = {
            "total": len(ids),
            "sucesso": 0,
            "falha": 0,
            "arquivos_deletados": 0,
            "erros": [],
        }

        db = SessionLocal()
        try:
            for log_id in ids:
                log = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.id == log_id).first()

                if not log:
                    resultados["falha"] += 1
                    resultados["erros"].append({"id": log_id, "erro": f"Registro {log_id} nao encontrado"})
                    continue

                arquivo_deletado = False
                if log.arquivo_salvo:
                    path = Path(log.arquivo_salvo)
                    if path.exists():
                        if log.arquivo_original:
                            try:
                                watch_path = get_settings().watch_folder_path
                                destino = watch_path / log.arquivo_original
                                destino.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(path), str(destino))
                                resultados["arquivos_deletados"] += 1  # reutiliza contador como "movidos"
                                logger.info("Arquivo restaurado para fila: %s -> %s", path, destino)
                            except Exception as e:
                                logger.error("Erro ao restaurar arquivo %s: %s", path, e)
                                if deletar_arquivos:
                                    try:
                                        path.unlink()
                                        arquivo_deletado = True
                                        resultados["arquivos_deletados"] += 1
                                    except Exception as e2:
                                        logger.error("Erro ao deletar arquivo %s: %s", path, e2)
                        elif deletar_arquivos:
                            try:
                                path.unlink()
                                arquivo_deletado = True
                                resultados["arquivos_deletados"] += 1
                            except Exception as e:
                                logger.error("Erro ao deletar arquivo %s: %s", path, e)

                self._registrar_reversao(
                    db=db,
                    log=log,
                    arquivo_deletado=arquivo_deletado,
                    tipo_reversao="LOTE",
                    motivo=motivo,
                )

                db.delete(log)
                resultados["sucesso"] += 1

            db.commit()

            return {
                "success": True,
                "message": "Reversao em lote processada",
                "revertidos": resultados["sucesso"],
                "erros": resultados["falha"],
                "arquivos_deletados": resultados["arquivos_deletados"],
                "detalhes_erros": resultados["erros"],
            }
        except Exception as e:
            db.rollback()
            logger.error("Erro ao reverter lote extratos baixados: %s", e)
            return {
                "success": False,
                "message": str(e),
                "revertidos": 0,
                "erros": len(ids),
                "detalhes_erros": [{"erro_geral": str(e)}],
            }
        finally:
            db.close()

    def reverter_ultimos(
        self,
        quantidade: int,
        deletar_arquivos: bool = True,
        motivo: Optional[str] = None,
    ) -> dict:
        """Reverte os ultimos N processamentos."""
        db = SessionLocal()
        try:
            logs = db.query(ExtratosBaixadosLog).order_by(desc(ExtratosBaixadosLog.id)).limit(quantidade).all()
            ids = [log.id for log in logs]
            db.close()
            return self.reverter_lote(ids, deletar_arquivos, motivo)
        finally:
            db.close()

    def get_estatisticas(self) -> dict:
        """Retorna estatisticas dos processamentos."""
        db = SessionLocal()
        try:
            total = db.query(ExtratosBaixadosLog).count()
            sucesso = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.status == "SUCESSO").count()
            nao_id = db.query(ExtratosBaixadosLog).filter(
                ExtratosBaixadosLog.status == "NAO_IDENTIFICADO"
            ).count()
            falha = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.status == "FALHA").count()

            logs_sucesso = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.status == "SUCESSO").all()
            arquivos_existentes = sum(
                1 for log in logs_sucesso if log.arquivo_salvo and Path(log.arquivo_salvo).exists()
            )

            total_reversoes = db.query(ExtratosBaixadosReversaoLog).count()

            return {
                "total": total,
                "sucesso": sucesso,
                "nao_identificado": nao_id,
                "falha": falha,
                "arquivos_existentes": arquivos_existentes,
                "total_reversoes": total_reversoes,
            }
        finally:
            db.close()

    def listar_reversoes(
        self,
        limit: int = 100,
        offset: int = 0,
        cliente: Optional[str] = None,
    ) -> List[dict]:
        """Lista historico de reversoes realizadas."""
        db = SessionLocal()
        try:
            query = db.query(ExtratosBaixadosReversaoLog).order_by(desc(ExtratosBaixadosReversaoLog.id))

            if cliente:
                query = query.filter(ExtratosBaixadosReversaoLog.cliente_nome.ilike(f"%{cliente}%"))

            query = query.limit(limit).offset(offset)
            reversoes = query.all()
            return [r.to_dict() for r in reversoes]
        finally:
            db.close()

    def get_stats_reversoes(self) -> dict:
        """Retorna estatisticas das reversoes."""
        db = SessionLocal()
        try:
            total = db.query(ExtratosBaixadosReversaoLog).count()
            arquivos_deletados = db.query(ExtratosBaixadosReversaoLog).filter(
                ExtratosBaixadosReversaoLog.arquivo_deletado == True
            ).count()
            por_lote = db.query(ExtratosBaixadosReversaoLog).filter(
                ExtratosBaixadosReversaoLog.tipo_reversao == "LOTE"
            ).count()
            individual = db.query(ExtratosBaixadosReversaoLog).filter(
                ExtratosBaixadosReversaoLog.tipo_reversao == "INDIVIDUAL"
            ).count()

            return {
                "total_reversoes": total,
                "arquivos_deletados": arquivos_deletados,
                "reversoes_em_lote": por_lote,
                "reversoes_individuais": individual,
            }
        finally:
            db.close()


_extratos_baixados_reversao_service: Optional[ExtratosBaixadosReversaoService] = None


def get_extratos_baixados_reversao_service() -> ExtratosBaixadosReversaoService:
    """Retorna instancia singleton do servico de reversao de extratos baixados."""
    global _extratos_baixados_reversao_service
    if _extratos_baixados_reversao_service is None:
        _extratos_baixados_reversao_service = ExtratosBaixadosReversaoService()
    return _extratos_baixados_reversao_service
