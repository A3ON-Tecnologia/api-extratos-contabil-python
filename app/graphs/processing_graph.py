"""
LangGraph pipeline for extratos processing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from app.events import EventType, ProcessingEvent
from app.schemas.api import ProcessingStatus

logger = logging.getLogger(__name__)


class ProcessingState(TypedDict, total=False):
    pdf_data: bytes
    filename: str
    file_hash: str
    test_mode: bool
    is_ofx: bool
    module: str

    text: str
    extraction: Any
    match_result: Any

    saved_path: str
    ano: int
    mes: int
    proc_status: ProcessingStatus
    cliente_nome: str | None
    log_id: int | None

    event_manager: Any
    cancel_check: Callable[[], bool] | None

    pdf_service: Any
    llm_service: Any
    matching_service: Any
    storage_service: Any
    executor: Any

    log_writer: Callable[[ProcessingState], int | None] | None
    log_teste_writer: Callable[[ProcessingState], None] | None


async def _raise_if_cancelled(state: ProcessingState) -> None:
    check = state.get("cancel_check")
    if check and check():
        raise asyncio.CancelledError("Cancelado pelo usuario")


def _is_conta_capital(extraction: Any) -> bool:
    tipo = getattr(extraction, "tipo_documento", None)
    return bool(tipo and "CONTA CAPITAL" in tipo.upper())


def _apply_planilha_overrides(state: ProcessingState) -> None:
    extraction = state["extraction"]
    match_result = state["match_result"]
    if match_result.identificado and match_result.cliente:
        if match_result.cliente.banco:
            extraction.banco = match_result.cliente.banco.strip().upper()
        if match_result.cliente.agencia:
            extraction.agencia = str(match_result.cliente.agencia)
        if match_result.cliente.conta:
            if not _is_conta_capital(extraction):
                extraction.conta = str(match_result.cliente.conta)


def _select_account_for_conta_capital(state: ProcessingState) -> None:
    extraction = state["extraction"]
    if not _is_conta_capital(extraction):
        return
    match_result = state["match_result"]
    storage_service = state["storage_service"]
    conta_cadastrada = match_result.cliente.conta if match_result.identificado else None
    extraction.conta = storage_service._select_account(
        extraction.banco,
        extraction.conta,
        conta_cadastrada,
        extraction.tipo_documento,
    )


async def _extract_text(state: ProcessingState) -> ProcessingState:
    await _raise_if_cancelled(state)
    event_manager = state["event_manager"]
    filename = state["filename"]
    pdf_service = state["pdf_service"]
    pdf_data = state["pdf_data"]
    executor = state["executor"]

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.PDF_TEXT_EXTRACTING,
            filename=filename,
            message=f"Extraindo conteudo de {filename}...",
            progress=10,
        )
    )

    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(executor, pdf_service.extract_text, pdf_data, filename)

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.PDF_TEXT_EXTRACTED,
            filename=filename,
            message=f"Conteudo extraido: {len(text)} caracteres",
            details={"chars": len(text)},
            progress=25,
        )
    )

    return {"text": text}


async def _llm_extract(state: ProcessingState) -> ProcessingState:
    await _raise_if_cancelled(state)
    event_manager = state["event_manager"]
    filename = state["filename"]
    llm_service = state["llm_service"]
    pdf_data = state["pdf_data"]
    text = state["text"]
    executor = state["executor"]

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.LLM_ANALYZING,
            filename=filename,
            message="Analisando documento com IA...",
            progress=30,
        )
    )

    loop = asyncio.get_event_loop()
    extraction = await loop.run_in_executor(executor, llm_service.extract_info_with_fallback, text, pdf_data)

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.LLM_COMPLETED,
            filename=filename,
            message=f"Analise concluida: {extraction.cliente_sugerido or 'N/A'}",
            details={
                "cliente": extraction.cliente_sugerido,
                "banco": extraction.banco,
                "tipo": extraction.tipo_documento,
                "confianca": extraction.confianca,
            },
            progress=50,
        )
    )

    return {"extraction": extraction}


async def _match_client(state: ProcessingState) -> ProcessingState:
    await _raise_if_cancelled(state)
    event_manager = state["event_manager"]
    filename = state["filename"]
    matching_service = state["matching_service"]
    extraction = state["extraction"]
    is_ofx = state.get("is_ofx", False)

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.MATCHING_START,
            filename=filename,
            message="Buscando cliente na base...",
            progress=55,
        )
    )

    match_result = matching_service.match(extraction, is_ofx=is_ofx)
    _apply_planilha_overrides(state | {"match_result": match_result})

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.MATCHING_COMPLETED,
            filename=filename,
            message=f"Match: {match_result.cliente.nome if match_result.identificado else 'Nao encontrado'}",
            details={
                "found": match_result.identificado,
                "cliente": match_result.cliente.nome if match_result.identificado else None,
                "metodo": match_result.metodo.value,
                "score": match_result.score,
            },
            progress=70,
        )
    )

    return {"match_result": match_result}


async def _select_account(state: ProcessingState) -> ProcessingState:
    await _raise_if_cancelled(state)
    _select_account_for_conta_capital(state)
    return {}


async def _save_file(state: ProcessingState) -> ProcessingState:
    await _raise_if_cancelled(state)
    event_manager = state["event_manager"]
    filename = state["filename"]
    storage_service = state["storage_service"]
    pdf_data = state["pdf_data"]
    extraction = state["extraction"]
    match_result = state["match_result"]
    test_mode = state.get("test_mode", False)
    module = state.get("module", "make")

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.FILE_SAVING,
            filename=filename,
            message="Salvando arquivo...",
            progress=75,
        )
    )

    if test_mode:
        ano, mes = storage_service._get_previous_month()
        if match_result.identificado:
            client_base_path = storage_service._resolve_client_path(match_result.cliente)
            if client_base_path:
                conta = storage_service._select_account(
                    extraction.banco,
                    extraction.conta,
                    match_result.cliente.conta,
                    extraction.tipo_documento,
                )
                target_path = storage_service._build_path_structure(
                    client_base_path,
                    ano,
                    mes,
                    extraction.banco,
                    conta,
                )
                file_name = storage_service._build_filename(
                    extraction.banco,
                    extraction.tipo_documento,
                    extraction.contrato,
                    pdf_data,
                    target_path,
                    filename,
                    conta,
                )
                saved_path = str(target_path / file_name)
            else:
                saved_path = str(storage_service.get_unidentified_path(module, test_mode) / filename)
        else:
            saved_path = str(storage_service.get_unidentified_path(module, test_mode) / filename)
    else:
        saved_path, ano, mes = storage_service.save_file(
            pdf_data=pdf_data,
            match_result=match_result,
            original_filename=filename,
            tipo_documento=extraction.tipo_documento,
            banco=extraction.banco,
            conta_extrato=extraction.conta,
            contrato=extraction.contrato,
            module=module,
            test_mode=test_mode,
        )

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.FILE_SAVED,
            filename=filename,
            message="Arquivo salvo (Simulado)" if test_mode else "Arquivo salvo",
            details={"path": saved_path},
            progress=85,
        )
    )

    return {"saved_path": saved_path, "ano": ano, "mes": mes}


async def _write_log(state: ProcessingState) -> ProcessingState:
    await _raise_if_cancelled(state)
    event_manager = state["event_manager"]
    filename = state["filename"]
    extraction = state["extraction"]
    match_result = state["match_result"]
    file_hash = state["file_hash"]
    saved_path = state["saved_path"]
    ano = state["ano"]
    mes = state["mes"]
    test_mode = state.get("test_mode", False)

    if match_result.identificado:
        proc_status = ProcessingStatus.SUCESSO
        cliente_nome = match_result.cliente.nome
    else:
        proc_status = ProcessingStatus.NAO_IDENTIFICADO
        cliente_nome = None

    log_id = None
    if not test_mode:
        await event_manager.emit(
            ProcessingEvent(
                event_type=EventType.LOG_WRITING,
                filename=filename,
                message="Registrando no banco de dados...",
                progress=90,
            )
        )
        log_writer = state.get("log_writer")
        if log_writer:
            try:
                log_id = log_writer(state)
            except Exception as exc:
                logger.error("Erro ao salvar log no banco de dados: %s", exc)
        await event_manager.emit(
            ProcessingEvent(
                event_type=EventType.LOG_WRITTEN,
                filename=filename,
                message="Log registrado",
                progress=95,
            )
        )
    else:
        log_teste_writer = state.get("log_teste_writer")
        if log_teste_writer:
            try:
                log_teste_writer(state)
            except Exception as exc:
                logger.error("Erro ao salvar log de teste no banco de dados: %s", exc)

    return {
        "proc_status": proc_status,
        "cliente_nome": cliente_nome,
        "log_id": log_id,
    }


async def _finalize(state: ProcessingState) -> ProcessingState:
    event_manager = state["event_manager"]
    filename = state["filename"]
    extraction = state["extraction"]
    match_result = state["match_result"]

    await event_manager.emit(
        ProcessingEvent(
            event_type=EventType.PROCESSING_COMPLETED,
            filename=filename,
            message=f"Processamento concluido: {state['proc_status'].value}",
            details={
                "status": state["proc_status"].value,
                "cliente": state["cliente_nome"],
                "path": state["saved_path"],
                "banco": extraction.banco,
                "tipo": extraction.tipo_documento,
                "ano": state["ano"],
                "mes": state["mes"],
                "metodo": match_result.metodo.value,
                "log_id": state.get("log_id"),
            },
            progress=100,
        )
    )

    event_manager.update_stats(
        sucesso=(state["proc_status"] == ProcessingStatus.SUCESSO),
        nao_identificado=(state["proc_status"] == ProcessingStatus.NAO_IDENTIFICADO),
        falha=(state["proc_status"] == ProcessingStatus.FALHA),
    )
    event_manager.end_processing()
    await event_manager.emit_stats()

    return {}


def build_processing_graph() -> Any:
    graph = StateGraph(ProcessingState)
    graph.add_node("extract_text", _extract_text)
    graph.add_node("llm_extract", _llm_extract)
    graph.add_node("match_client", _match_client)
    graph.add_node("select_account", _select_account)
    graph.add_node("save_file", _save_file)
    graph.add_node("write_log", _write_log)
    graph.add_node("finalize", _finalize)

    graph.set_entry_point("extract_text")
    graph.add_edge("extract_text", "llm_extract")
    graph.add_edge("llm_extract", "match_client")
    graph.add_edge("match_client", "select_account")
    graph.add_edge("select_account", "save_file")
    graph.add_edge("save_file", "write_log")
    graph.add_edge("write_log", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


_processing_graph = None


def get_processing_graph() -> Any:
    global _processing_graph
    if _processing_graph is None:
        _processing_graph = build_processing_graph()
    return _processing_graph


async def run_processing_graph(state: ProcessingState) -> ProcessingState:
    graph = get_processing_graph()
    return await graph.ainvoke(state)
