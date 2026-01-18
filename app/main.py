"""
API FastAPI para processamento de extratos contabeis.

Versao com processamento ASSINCRONO para evitar timeout.
O Make recebe resposta imediata e o arquivo e processado em background.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import get_settings
from app.events import EventType, ProcessingEvent, get_event_manager
from app.schemas.api import ProcessingResult, ProcessingStatus, UploadResponse
from app.schemas.client import MatchMethod
from app.services import (
    AuditService,
    ClientService,
    LLMService,
    MatchingService,
    PDFService,
    StorageService,
    ZIPService,
)
from app.utils.hash import compute_hash

# Configuracao de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Aplicacao FastAPI
app = FastAPI(
    title="Extratos Contabeis API",
    description="Sistema de automacao para processamento de extratos bancarios e documentos contabeis",
    version="1.0.0",
)

# CORS - permitir requisicoes do Make e outras origens
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache de hashes processados para idempotencia
_processed_hashes: set[str] = set()

# Armazenamento de jobs para consulta de status
_jobs: dict[str, dict] = {}

# Executor para tarefas em background
_executor = ThreadPoolExecutor(max_workers=4)


# Instancias dos servicos (singleton pattern simples)
_pdf_service: PDFService | None = None
_zip_service: ZIPService | None = None
_llm_service: LLMService | None = None
_client_service: ClientService | None = None
_matching_service: MatchingService | None = None
_storage_service: StorageService | None = None
_audit_service: AuditService | None = None


def get_pdf_service() -> PDFService:
    global _pdf_service
    if _pdf_service is None:
        _pdf_service = PDFService()
    return _pdf_service


def get_zip_service() -> ZIPService:
    global _zip_service
    if _zip_service is None:
        _zip_service = ZIPService()
    return _zip_service


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service


def get_client_service() -> ClientService:
    global _client_service
    if _client_service is None:
        _client_service = ClientService()
    return _client_service


def get_matching_service() -> MatchingService:
    global _matching_service
    if _matching_service is None:
        _matching_service = MatchingService(get_client_service())
    return _matching_service


def get_storage_service() -> StorageService:
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service


def get_audit_service() -> AuditService:
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService()
    return _audit_service


# ============================================================
# DASHBOARD DE MONITORAMENTO
# ============================================================

@app.get("/monitor", response_class=HTMLResponse)
async def monitor_dashboard():
    """Dashboard de monitoramento em tempo real."""
    template_path = Path(__file__).parent / "templates" / "monitor.html"
    
    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template do monitor nao encontrado")
    
    return HTMLResponse(content=template_path.read_text(encoding="utf-8"))


@app.get("/monitor/stats")
async def monitor_stats():
    """Retorna estatísticas do sistema de arquivos."""
    settings = get_settings()
    unidentified_path = settings.unidentified_path
    
    # Conta arquivos na pasta NAO_IDENTIFICADOS (recursivamente)
    count_unidentified = 0
    if unidentified_path.exists():
        count_unidentified = sum(1 for _ in unidentified_path.rglob("*") if _.is_file())
        
    return {
        "unidentified_files_count": count_unidentified,
        "unidentified_path": str(unidentified_path)
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para atualizacoes em tempo real."""
    event_manager = get_event_manager()
    await event_manager.connect(websocket)
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_manager.disconnect(websocket)


# ============================================================
# ENDPOINTS DA API
# ============================================================

@app.get("/health")
async def health_check():
    """Endpoint de health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "jobs_pending": sum(1 for j in _jobs.values() if j["status"] == "processing"),
    }


@app.get("/")
async def root():
    """Endpoint raiz com informacoes da API."""
    settings = get_settings()
    return {
        "name": "Extratos Contabeis API",
        "version": "1.0.0",
        "base_path": str(settings.base_path),
        "docs": "/docs",
        "monitor": "/monitor",
    }


@app.post("/upload")
async def upload_file(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP para processar")],
    background_tasks: BackgroundTasks
):
    """
    Recebe um arquivo PDF ou ZIP e inicia processamento em background.
    
    RESPOSTA IMEDIATA com job_id para acompanhamento.
    O processamento ocorre em segundo plano.
    
    Use GET /job/{job_id} para verificar o status.
    """
    content = await file.read()
    filename = file.filename or "upload"
    
    # Validacao basica
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio")
    
    # Detecta tipo do arquivo (validação flexível)
    is_pdf = b"%PDF-" in content[:1024] or filename.lower().endswith('.pdf')
    is_zip = content.startswith(b"PK") or filename.lower().endswith('.zip')
    
    # Se não for PDF nem ZIP, aceita mesmo assim (será tratado depois)
    logger.info(f"Recebido arquivo: {filename} (PDF={is_pdf}, ZIP={is_zip})")
    
    # Gera ID do job
    job_id = str(uuid.uuid4())[:8]
    
    # Registra o job
    _jobs[job_id] = {
        "job_id": job_id,
        "filename": filename,
        "status": "processing",
        "message": "Arquivo recebido, processamento iniciado",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "results": None,
    }
    
    # Inicia processamento em background
    background_tasks.add_task(
        process_file_background,
        job_id=job_id,
        content=content,
        filename=filename,
        is_zip=is_zip,
    )
    
    # Retorna imediatamente
    return {
        "success": True,
        "job_id": job_id,
        "message": "Arquivo recebido! Processamento iniciado em background.",
        "status_url": f"/job/{job_id}",
        "monitor_url": "/monitor",
    }


@app.get("/job/{job_id}")
async def get_job_status(job_id: str):
    """
    Verifica o status de um job de processamento.
    
    Retorna o status atual e resultados quando concluido.
    """
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job nao encontrado")
    
    return _jobs[job_id]


@app.get("/jobs")
async def list_jobs():
    """Lista todos os jobs recentes."""
    # Retorna os ultimos 50 jobs
    jobs_list = list(_jobs.values())
    jobs_list.sort(key=lambda x: x["created_at"], reverse=True)
    return {
        "total": len(jobs_list),
        "jobs": jobs_list[:50]
    }


# ============================================================
# PROCESSAMENTO EM BACKGROUND
# ============================================================

async def process_file_background(job_id: str, content: bytes, filename: str, is_zip: bool):
    """
    Processa o arquivo em background e atualiza o status do job.
    """
    event_manager = get_event_manager()
    
    try:
        # Emitir evento de arquivo recebido
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_RECEIVED,
            filename=filename,
            message=f"Arquivo recebido: {filename}",
            details={"size": len(content), "job_id": job_id}
        ))
        
        if is_zip:
            result = await process_zip_async(content, filename, job_id)
        else:
            result = await process_pdf_async(content, filename, job_id)
            result = UploadResponse(
                sucesso=result.status == ProcessingStatus.SUCESSO,
                total_arquivos=1,
                arquivos_sucesso=1 if result.status == ProcessingStatus.SUCESSO else 0,
                arquivos_nao_identificados=1 if result.status == ProcessingStatus.NAO_IDENTIFICADO else 0,
                arquivos_falha=1 if result.status == ProcessingStatus.FALHA else 0,
                resultados=[result],
            )
        
        # Atualiza o job com sucesso
        _jobs[job_id].update({
            "status": "completed",
            "message": f"Processamento concluido: {result.arquivos_sucesso} sucesso, {result.arquivos_nao_identificados} nao identificados, {result.arquivos_falha} falhas",
            "completed_at": datetime.now().isoformat(),
            "results": result.model_dump(),
        })
        
    except Exception as e:
        logger.exception(f"Erro no processamento do job {job_id}")
        
        # Atualiza o job com erro
        _jobs[job_id].update({
            "status": "error",
            "message": f"Erro: {str(e)}",
            "completed_at": datetime.now().isoformat(),
            "results": None,
        })
        
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.PROCESSING_ERROR,
            filename=filename,
            message=str(e)
        ))


async def process_zip_async(zip_data: bytes, filename: str, job_id: str | None = None) -> UploadResponse:
    """Processa um arquivo ZIP contendo PDFs."""
    event_manager = get_event_manager()
    zip_service = get_zip_service()
    
    # Check Cancelamento
    if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

    await event_manager.emit(ProcessingEvent(
        event_type=EventType.ZIP_EXTRACTING,
        filename=filename,
        message="Extraindo arquivos do ZIP..."
    ))
    
    try:
        extracted_files = zip_service.extract_pdfs(zip_data)
    except ValueError as e:
        raise ValueError(f"Erro ao extrair ZIP: {e}")
    
    await event_manager.emit(ProcessingEvent(
        event_type=EventType.ZIP_EXTRACTED,
        filename=filename,
        message=f"{len(extracted_files)} PDFs extraidos",
        details={"count": len(extracted_files)}
    ))
    
    results: list[ProcessingResult] = []
    
    for extracted_file in extracted_files:
        # Check Cancelamento entre arquivos
        if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
             logger.warning(f"Processamento ZIP cancelado: {filename}")
             break

        result = await process_pdf_async(extracted_file.data, extracted_file.filename, job_id)
        results.append(result)
    
    sucesso = sum(1 for r in results if r.status == ProcessingStatus.SUCESSO)
    nao_identificado = sum(1 for r in results if r.status == ProcessingStatus.NAO_IDENTIFICADO)
    falha = sum(1 for r in results if r.status == ProcessingStatus.FALHA)
    
    return UploadResponse(
        sucesso=sucesso > 0,
        total_arquivos=len(results),
        arquivos_sucesso=sucesso,
        arquivos_nao_identificados=nao_identificado,
        arquivos_falha=falha,
        resultados=results,
    )


async def process_pdf_async(pdf_data: bytes, filename: str, job_id: str | None = None) -> ProcessingResult:
    """Processa um unico arquivo PDF."""
    event_manager = get_event_manager()
    file_hash = compute_hash(pdf_data)
    
    # Check cancelamento inicial
    if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
        logger.warning(f"Job {job_id} cancelado antes do inicio.")
        return ProcessingResult(
            nome_arquivo_original=filename,
            status=ProcessingStatus.FALHA,
            hash_arquivo=file_hash,
            erro="Cancelado manualmente",
            nome_arquivo_final=""
        )

    event_manager.start_processing()
    
    await event_manager.emit(ProcessingEvent(
        event_type=EventType.PDF_PROCESSING_START,
        filename=filename,
        message="Iniciando processamento do PDF",
        progress=0
    ))
    
    # Verifica idempotencia
    if file_hash in _processed_hashes:
        logger.info(f"Arquivo ja processado (hash: {file_hash[:8]}): {filename}")
        event_manager.end_processing()
        return ProcessingResult(
            nome_arquivo_original=filename,
            status=ProcessingStatus.SUCESSO,
            hash_arquivo=file_hash,
            erro="Arquivo ja processado anteriormente (duplicado)",
        )
    
    _processed_hashes.add(file_hash)
    
    # Verifica se é realmente um PDF (apenas para log, não bloqueia)
    is_pdf = pdf_data.startswith(b"%PDF-") or filename.lower().endswith(".pdf")
    if not is_pdf:
        logger.info(f"Processando arquivo não-PDF: {filename}")

    try:
        # Check Cancelamento
        if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

        # 1. Extrai texto (Documento Genérico)
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.PDF_TEXT_EXTRACTING,
            filename=filename,
            message=f"Extraindo conteúdo de {filename}...",
            progress=10
        ))
        
        pdf_service = get_pdf_service()
        try:
            # Executar em thread separada para nao bloquear
            loop = asyncio.get_event_loop()
            # Passa o filename para ajudar na detecção do tipo
            text = await loop.run_in_executor(_executor, pdf_service.extract_text, pdf_data, filename)
        except ValueError as e:
            # Se falhar extração, tenta salvar como imagem ou corrompido? Não, falha mesmo.
            return await create_failure_result(filename, file_hash, f"Erro ao extrair conteúdo: {e}")
        
        # Check Cancelamento
        if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.PDF_TEXT_EXTRACTED,
            filename=filename,
            message=f"Conteúdo extraído: {len(text)} caracteres",
            details={"chars": len(text)},
            progress=25
        ))
        
        # 2. Envia para LLM
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.LLM_ANALYZING,
            filename=filename,
            message="Analisando documento com IA...",
            progress=30
        ))
        
        llm_service = get_llm_service()
        # Executar LLM em thread separada
        loop = asyncio.get_event_loop()
        extraction = await loop.run_in_executor(_executor, llm_service.extract_info_with_fallback, text)
        
        # Check Cancelamento
        if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.LLM_COMPLETED,
            filename=filename,
            message=f"Analise concluida: {extraction.cliente_sugerido or 'N/A'}",
            details={
                "cliente": extraction.cliente_sugerido,
                "banco": extraction.banco,
                "tipo": extraction.tipo_documento,
                "confianca": extraction.confianca
            },
            progress=50
        ))
        
        # 3. Faz matching do cliente
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.MATCHING_START,
            filename=filename,
            message="Buscando cliente na base...",
            progress=55
        ))
        
        # Check Cancelamento
        if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

        matching_service = get_matching_service()
        match_result = matching_service.match(extraction)
        
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.MATCHING_COMPLETED,
            filename=filename,
            message=f"Match: {match_result.cliente.nome if match_result.identificado else 'Nao encontrado'}",
            details={
                "found": match_result.identificado,
                "cliente": match_result.cliente.nome if match_result.identificado else None,
                "metodo": match_result.metodo.value,
                "score": match_result.score
            },
            progress=70
        ))
        
        # Check Cancelamento
        if job_id and _jobs.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

        # 4. Salva o arquivo
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_SAVING,
            filename=filename,
            message="Salvando arquivo...",
            progress=75
        ))
        
        storage_service = get_storage_service()
        saved_path = storage_service.save_file(
            pdf_data=pdf_data,
            match_result=match_result,
            ano=extraction.ano,
            mes=extraction.mes,
            original_filename=filename,
            tipo_documento=extraction.tipo_documento,
            banco=extraction.banco,
        )
        
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_SAVED,
            filename=filename,
            message=f"Arquivo salvo",
            details={"path": saved_path},
            progress=85
        ))
        
        # 5. Determina status
        if match_result.identificado:
            proc_status = ProcessingStatus.SUCESSO
            cliente_nome = match_result.cliente.nome
        else:
            proc_status = ProcessingStatus.NAO_IDENTIFICADO
            cliente_nome = None
        
        # 6. Registra no log
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.LOG_WRITING,
            filename=filename,
            message="Registrando no log...",
            progress=90
        ))
        
        audit_service = get_audit_service()
        audit_service.log_result(
            nome_cliente=cliente_nome,
            tipo_extrato=extraction.tipo_documento,
            ano=extraction.ano,
            mes=extraction.mes,
            status=proc_status,
            nome_arquivo_final=saved_path,
        )
        
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.LOG_WRITTEN,
            filename=filename,
            message="Log registrado",
            progress=95
        ))
        
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.PROCESSING_COMPLETED,
            filename=filename,
            message=f"Processamento concluido: {proc_status.value}",
            details={
                "status": proc_status.value,
                "cliente": cliente_nome,
                "path": saved_path,
                "banco": extraction.banco,
                "tipo": extraction.tipo_documento,
                "ano": extraction.ano,
                "mes": extraction.mes,
                "metodo": match_result.metodo.value,
            },
            progress=100
        ))
        
        event_manager.update_stats(
            sucesso=(proc_status == ProcessingStatus.SUCESSO),
            nao_identificado=(proc_status == ProcessingStatus.NAO_IDENTIFICADO)
        )
        event_manager.end_processing()
        await event_manager.emit_stats()
        
        logger.info(f"Processamento concluido: {filename} -> {proc_status.value} (cliente: {cliente_nome or 'N/A'})")
        
        return ProcessingResult(
            nome_arquivo_original=filename,
            nome_arquivo_final=saved_path,
            status=proc_status,
            cliente_identificado=cliente_nome,
            metodo_identificacao=match_result.metodo,
            tipo_documento=extraction.tipo_documento,
            ano=extraction.ano,
            mes=extraction.mes,
            hash_arquivo=file_hash,
            erro=match_result.motivo_fallback if not match_result.identificado else None,
        )
        
    except asyncio.CancelledError:
        logger.warning(f"Processamento cancelado explicitamente: {filename}")
        event_manager.end_processing()
        return ProcessingResult(
            nome_arquivo_original=filename,
            status=ProcessingStatus.FALHA,
            hash_arquivo=file_hash,
            erro="Cancelado manualmente pelo usuário",
            nome_arquivo_final=""
        )

    except Exception as e:
        logger.exception(f"Erro inesperado ao processar {filename}")
        return await create_failure_result(filename, file_hash, str(e))


async def create_failure_result(filename: str, file_hash: str, error: str) -> ProcessingResult:
    """Cria um resultado de falha e registra no log."""
    event_manager = get_event_manager()
    
    await event_manager.emit(ProcessingEvent(
        event_type=EventType.PROCESSING_ERROR,
        filename=filename,
        message=error
    ))
    
    event_manager.update_stats(falha=True)
    event_manager.end_processing()
    await event_manager.emit_stats()
    
    audit_service = get_audit_service()
    audit_service.log_result(
        nome_cliente=None,
        tipo_extrato=None,
        ano=None,
        mes=None,
        status=ProcessingStatus.FALHA,
        nome_arquivo_final=None,
    )
    
    return ProcessingResult(
        nome_arquivo_original=filename,
        status=ProcessingStatus.FALHA,
        hash_arquivo=file_hash,
        erro=error,
    )


# ============================================================
# ENDPOINTS AUXILIARES
# ============================================================

@app.post("/reload-clients")
async def reload_clients():
    """Forca recarga da planilha de clientes."""
    client_service = get_client_service()
    client_service.invalidate_cache()
    clients = client_service.load_clients(force_reload=True)
    return {"message": "Planilha de clientes recarregada", "total_clients": len(clients)}


@app.post("/reload-settings")
async def reload_settings():
    """Limpa cache das configurações e força releitura do .env."""
    from app.config import clear_settings_cache
    
    # Limpa cache das configurações
    new_settings = clear_settings_cache()
    
    # Também invalida cache dos clientes
    client_service = get_client_service()
    client_service.invalidate_cache()
    
    return {
        "message": "Configurações recarregadas com sucesso",
        "clients_excel_path": str(new_settings.clients_excel_path),
        "base_path": str(new_settings.base_path),
        "log_excel_path": str(new_settings.log_excel_path),
    }


@app.get("/monitor/history")
async def get_history():
    """Retorna o histórico de processamento persistente."""
    try:
        audit_service = get_audit_service()
        # Executa em thread separada pois leitura de excel pode ser bloqueante
        loop = asyncio.get_event_loop()
        history = await loop.run_in_executor(_executor, audit_service.get_recent_logs, 100)
        return history
    except Exception as e:
        logger.error(f"Erro ao buscar histórico: {e}")
        return []


@app.post("/merge-fallback-logs")
async def merge_fallback_logs():
    """Mescla logs de fallback no arquivo principal."""
    audit_service = get_audit_service()
    merged = audit_service.merge_fallback_logs()
    return {"message": "Logs de fallback mesclados", "entries_merged": merged}


@app.get("/debug/clients")
async def debug_clients():
    """Endpoint de diagnóstico para verificar leitura da planilha de clientes."""
    settings = get_settings()
    path = settings.clients_excel_path
    
    result = {
        "status": "verificando",
        "path_configured": str(path),
        "exists": path.exists(),
        "is_file": path.is_file() if path.exists() else False,
        "absolute_path": str(path.resolve()) if path.exists() else None,
        "error": None,
        "columns": [],
        "sample_clients": [],
        "total_loaded": 0
    }
    
    if not result["exists"]:
        result["status"] = "ERROR"
        result["error"] = "Arquivo não encontrado no disco."
        return result
        
    try:
        # Tenta carregar usando o serviço
        client_service = get_client_service()
        clients = client_service.load_clients(force_reload=True)
        
        result["status"] = "OK"
        result["total_loaded"] = len(clients)
        result["sample_clients"] = [c.nome for c in clients[:5]]
        
        # Lê colunas brutas para debug
        import pandas as pd
        df = pd.read_excel(path, engine="openpyxl", nrows=0)
        result["columns"] = list(df.columns)
        
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        import traceback
        result["traceback"] = traceback.format_exc()
        
    return result


@app.post("/monitor/reset")
async def reset_processing():
    """Força o encerramento de todos os processamentos em andamento."""
    count = 0
    event_manager = get_event_manager()
    
    for job_id, job in _jobs.items():
        if job["status"] == "processing":
            job["status"] = "cancelled"
            job["message"] = "Processamento cancelado manualmente pelo usuário"
            job["completed_at"] = datetime.now().isoformat()
            
            # Notifica cancelamento
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.PROCESSING_ERROR,
                filename=job["filename"],
                message="Cancelado manualmente"
            ))
            count += 1
    
    # Força reset do contador interno do event manager
    if count > 0 or event_manager.is_processing:
        event_manager.end_processing()
        await event_manager.emit_stats()
        
    return {"message": f"{count} processamentos encerrados manualmente.", "count": count}


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Handler global de excecoes."""
    logger.exception(f"Erro nao tratado: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno do servidor", "error": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
