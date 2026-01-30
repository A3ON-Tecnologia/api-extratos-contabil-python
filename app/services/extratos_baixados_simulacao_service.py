"""
Servico de simulacao para extratos baixados.

Centraliza a logica de simulacao e grava os resultados no modo teste.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

from app.events import EventType, ProcessingEvent, get_extratos_test_event_manager
from app.schemas.client import MatchResult
from app.services import ClientService, LLMService, MatchingService, PDFService, StorageService
from app.services.db_extratos_baixados_log_teste_service import (
    get_extratos_baixados_log_teste_service,
)
from app.utils.hash import compute_hash

logger = logging.getLogger(__name__)


class ExtratosBaixadosSimulacaoService:
    """Servico para simular o processamento de extratos baixados."""

    def __init__(self) -> None:
        self._pdf_service = PDFService()
        self._llm_service = LLMService()
        self._matching_service = MatchingService(ClientService())
        self._storage_service = StorageService()
        self._log_teste_service = get_extratos_baixados_log_teste_service()

    async def simular_arquivo(
        self,
        pdf_data: bytes,
        filename: str,
        executor: ThreadPoolExecutor,
        caminho_origem: Path | None = None,
    ) -> dict[str, Any]:
        """Simula o processamento e registra no modo teste."""
        file_hash = compute_hash(pdf_data)

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(
            executor, self._pdf_service.extract_text, pdf_data, filename
        )

        extraction = await loop.run_in_executor(
            executor, self._llm_service.extract_info_with_fallback, text, pdf_data
        )

        is_ofx = filename.lower().endswith(".ofx")
        match_result = self._matching_service.match(extraction, is_ofx=is_ofx)

        # Se identificou o cliente, USA SEMPRE OS DADOS DA PLANILHA (não do PDF)
        if match_result.identificado and match_result.cliente:
            # BANCO: sempre usa o da planilha
            if match_result.cliente.banco:
                banco_original = extraction.banco
                extraction.banco = match_result.cliente.banco.strip().upper()
                if banco_original != extraction.banco:
                    logger.info(
                        "[SIMULACAO] Banco corrigido pela planilha: '%s' -> '%s'",
                        banco_original,
                        extraction.banco,
                    )

            # AGÊNCIA: sempre usa a da planilha
            if match_result.cliente.agencia:
                agencia_original = extraction.agencia
                extraction.agencia = str(match_result.cliente.agencia)
                if agencia_original != extraction.agencia:
                    logger.info(
                        "[SIMULACAO] Agência corrigida pela planilha: '%s' -> '%s'",
                        agencia_original,
                        extraction.agencia,
                    )

            # CONTA: sempre usa a da planilha (exceto para Conta Capital)
            if match_result.cliente.conta:
                is_conta_capital = extraction.tipo_documento and "CONTA CAPITAL" in extraction.tipo_documento.upper()
                if not is_conta_capital:
                    conta_original = extraction.conta
                    extraction.conta = str(match_result.cliente.conta)
                    if conta_original != extraction.conta:
                        logger.info(
                            "[SIMULACAO] Conta corrigida pela planilha: '%s' -> '%s'",
                            conta_original,
                            extraction.conta,
                        )
        if extraction.tipo_documento and "CONTA CAPITAL" in extraction.tipo_documento.upper():
            conta_cadastrada = match_result.cliente.conta if match_result.identificado else None
            extraction.conta = self._storage_service._select_account(
                extraction.banco,
                extraction.conta,
                conta_cadastrada,
                extraction.tipo_documento,
            )
        ano, mes = self._storage_service._get_previous_month()

        caminho_destino, pasta_destino_existe, status, cliente_nome = (
            self._calcular_destino(
                filename=filename,
                match_result=match_result,
                banco=extraction.banco,
                conta_extrato=extraction.conta,
                contrato=extraction.contrato,
                tipo_documento=extraction.tipo_documento,
                pdf_data=pdf_data,
                ano=ano,
                mes=mes,
            )
        )

        motivo_falha = match_result.motivo_fallback if not match_result.identificado else None

        log_entry = self._log_teste_service.log_extrato_teste(
            arquivo_original=filename,
            status=status,
            arquivo_salvo=caminho_destino,
            hash_arquivo=file_hash,
            cliente_nome=cliente_nome,
            cliente_cod=match_result.cliente.cod if match_result.identificado else None,
            cliente_cnpj=match_result.cliente.cnpj if match_result.identificado else None,
            banco=extraction.banco,
            tipo_documento=extraction.tipo_documento,
            agencia=extraction.agencia,
            conta=extraction.conta,
            ano=ano,
            mes=mes,
            metodo_identificacao=match_result.metodo.value,
            confianca_ia=extraction.confianca,
            erro=motivo_falha,
        )

        await self._emit_test_event(
            filename=filename,
            status=status,
            match_result=match_result,
            banco=extraction.banco,
            tipo_documento=extraction.tipo_documento,
            ano=ano,
            mes=mes,
            log_id=log_entry.id,
            erro=motivo_falha,
        )

        return {
            "sucesso": True,
            "arquivo_original": filename,
            "status": status,
            "caminho_origem": str(caminho_origem) if caminho_origem else None,
            "caminho_destino": caminho_destino,
            "pasta_destino_existe": pasta_destino_existe,
            "cliente": {
                "identificado": match_result.identificado,
                "nome": cliente_nome,
                "cod": match_result.cliente.cod if match_result.identificado else None,
                "conta": match_result.cliente.conta if match_result.identificado else None,
                "metodo": match_result.metodo.value,
                "score": match_result.score,
            },
            "extrato_info": {
                "banco": extraction.banco,
                "tipo_documento": extraction.tipo_documento,
                "cnpj": extraction.cnpj,
                "agencia": extraction.agencia,
                "conta": extraction.conta,
                "confianca": extraction.confianca,
            },
            "periodo": {"ano": ano, "mes": mes},
            "motivo_nao_identificado": motivo_falha,
            "hash": file_hash,
            "tamanho_mb": round(len(pdf_data) / (1024 * 1024), 2),
            "log_id": log_entry.id,
        }

    def _calcular_destino(
        self,
        filename: str,
        match_result: MatchResult,
        banco: str | None,
        conta_extrato: str | None,
        contrato: str | None,
        tipo_documento: str,
        pdf_data: bytes,
        ano: int,
        mes: int,
    ) -> tuple[str, bool, str, str | None]:
        """Calcula caminho simulado e status."""
        if match_result.identificado:
            client_base_path = self._storage_service._resolve_client_path(match_result.cliente)
            if client_base_path:
                conta = self._storage_service._select_account(
                    banco,
                    conta_extrato,
                    match_result.cliente.conta,
                    tipo_documento,
                )
                target_path = self._storage_service._build_path_structure(
                    client_base_path, ano, mes, banco, conta
                )
                file_name = self._storage_service._build_filename(
                    banco, tipo_documento, contrato, pdf_data, target_path, filename, conta
                )
                return (
                    str(target_path / file_name),
                    True,
                    "SUCESSO",
                    match_result.cliente.nome,
                )

        return (
            str(self._storage_service.settings.unidentified_path / filename),
            self._storage_service.settings.unidentified_path.exists(),
            "NAO_IDENTIFICADO",
            None,
        )

    async def _emit_test_event(
        self,
        filename: str,
        status: str,
        match_result: MatchResult,
        banco: str | None,
        tipo_documento: str,
        ano: int,
        mes: int,
        log_id: int | None,
        erro: str | None,
    ) -> None:
        """Emite evento no monitor de teste para refletir a simulacao."""
        event_manager = get_extratos_test_event_manager()

        event_manager.update_stats(
            sucesso=status == "SUCESSO",
            nao_identificado=status == "NAO_IDENTIFICADO",
            falha=status == "FALHA",
        )
        await event_manager.emit_stats()

        await event_manager.emit(
            ProcessingEvent(
                event_type=EventType.PROCESSING_COMPLETED,
                filename=filename,
                message="Processamento simulado concluido",
                details={
                    "status": status,
                    "cliente": match_result.cliente.nome if match_result.identificado else None,
                    "path": None,
                    "banco": banco,
                    "tipo": tipo_documento,
                    "ano": ano,
                    "mes": mes,
                    "metodo": match_result.metodo.value,
                    "log_id": log_id,
                    "erro": erro,
                },
                progress=100,
            )
        )
