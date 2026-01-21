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
from typing import Annotated, List
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
from app.services.db_log_service import get_db_log_service
from app.services.db_log_teste_service import get_db_log_teste_service
from app.services.reversao_service import get_reversao_service
from app.utils.hash import compute_hash

# Configuracao de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia o ciclo de vida da aplicação."""
    # Startup: inicializa banco de dados
    try:
        from app.database import init_db
        init_db()
        logger.info("Banco de dados inicializado com sucesso")
    except Exception as e:
        logger.error(f"Erro ao inicializar banco de dados: {e}")
    
    yield
    
    # Shutdown: limpeza se necessário
    logger.info("Encerrando aplicação...")


# Aplicacao FastAPI
app = FastAPI(
    title="Extratos Contabeis API",
    description="Sistema de automacao para processamento de extratos bancarios e documentos contabeis",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - permitir requisicoes do Make e outras origens
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir arquivos estáticos (CSS, JS)
from fastapi.staticfiles import StaticFiles
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Cache de hashes processados para idempotencia
_processed_hashes: set[str] = set()

# Armazenamento de jobs para consulta de status
_jobs: dict[str, dict] = {}

# Armazenamento de jobs de TESTE
_test_jobs: dict[str, dict] = {}

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

async def process_file_background(job_id: str, content: bytes, filename: str, is_zip: bool, test_mode: bool = False):
    """
    Processa o arquivo em background e atualiza o status do job.
    """
    event_manager = get_event_manager()
    jobs_dict = _test_jobs if test_mode else _jobs
    
    try:
        # Emitir evento de arquivo recebido
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_RECEIVED,
            filename=filename,
            message=f"Arquivo recebido: {filename}",
            details={"size": len(content), "job_id": job_id, "test_mode": test_mode}
        ))
        
        if is_zip:
            result = await process_zip_async(content, filename, job_id, test_mode)
        else:
            result = await process_pdf_async(content, filename, job_id, test_mode)
            result = UploadResponse(
                sucesso=result.status == ProcessingStatus.SUCESSO,
                total_arquivos=1,
                arquivos_sucesso=1 if result.status == ProcessingStatus.SUCESSO else 0,
                arquivos_nao_identificados=1 if result.status == ProcessingStatus.NAO_IDENTIFICADO else 0,
                arquivos_falha=1 if result.status == ProcessingStatus.FALHA else 0,
                resultados=[result],
            )
        
        # Atualiza o job com sucesso
        jobs_dict[job_id].update({
            "status": "completed",
            "message": f"Processamento concluido: {result.arquivos_sucesso} sucesso, {result.arquivos_nao_identificados} nao identificados, {result.arquivos_falha} falhas",
            "completed_at": datetime.now().isoformat(),
            "results": result.model_dump(),
        })
        
    except Exception as e:
        logger.exception(f"Erro no processamento do job {job_id}")
        
        # Atualiza o job com erro
        jobs_dict[job_id].update({
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


async def process_zip_async(zip_data: bytes, filename: str, job_id: str | None = None, test_mode: bool = False) -> UploadResponse:
    """Processa um arquivo ZIP contendo PDFs."""
    event_manager = get_event_manager()
    zip_service = get_zip_service()
    jobs_dict = _test_jobs if test_mode else _jobs
    
    # Check Cancelamento
    if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
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
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
             logger.warning(f"Processamento ZIP cancelado: {filename}")
             break

        result = await process_pdf_async(extracted_file.data, extracted_file.filename, job_id, test_mode)
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


async def process_pdf_async(pdf_data: bytes, filename: str, job_id: str | None = None, test_mode: bool = False) -> ProcessingResult:
    """Processa um unico arquivo PDF."""
    event_manager = get_event_manager()
    file_hash = compute_hash(pdf_data)
    jobs_dict = _test_jobs if test_mode else _jobs
    
    # Check cancelamento inicial
    if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
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
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
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
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
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
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
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
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
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
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

        # 4. Salva o arquivo (Simulado se modo de teste)
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_SAVING,
            filename=filename,
            message="Salvando arquivo...",
            progress=75
        ))
        
        storage_service = get_storage_service()
        
        if test_mode:
            # MODO TESTE: Calcula caminho simulado e NÃO salva
            ano, mes = storage_service._get_previous_month()
            if match_result.identificado:
                client_base_path = storage_service._resolve_client_path(match_result.cliente)
                if client_base_path:
                    target_path = storage_service._build_path_structure(client_base_path, ano, mes)
                    saved_path = str(target_path / f"{extraction.tipo_documento}_{extraction.banco}.pdf")
                else:
                    saved_path = str(storage_service.settings.unidentified_path / filename)
            else:
                saved_path = str(storage_service.settings.unidentified_path / filename)
            
            logger.info(f"[TESTE] Arquivo seria salvo em: {saved_path}")
            
        else:
            # MODO PRODUCÃO: Salva arquivo
            saved_path, ano, mes = storage_service.save_file(
                pdf_data=pdf_data,
                match_result=match_result,
                original_filename=filename,
                tipo_documento=extraction.tipo_documento,
                banco=extraction.banco,
            )
        
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_SAVED,
            filename=filename,
            message="Arquivo salvo (Simulado)" if test_mode else "Arquivo salvo",
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
        
        # 6. Registra no log (apenas se nao for teste, ou deve registrar?)
        # Audit service grava em arquivo JSON. O usuário pediu "igual producao, so nao salva arquivo final".
        # Vou manter o log de auditoria tambem? Talvez polua. Vou pular se test_mode.
        if not test_mode:
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
                ano=ano,
                mes=mes,
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
                "ano": ano,
                "mes": mes,
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
        
        # Salva log no banco de dados
        try:
            if test_mode:
                # MODO TESTE: Salva apenas registro de teste
                db_teste_service = get_db_log_teste_service()
                db_teste_service.log_extrato_teste(
                    arquivo_original=filename,
                    status=proc_status.value,
                    arquivo_salvo=saved_path,
                    hash_arquivo=file_hash,
                    cliente_nome=cliente_nome,
                    cliente_cod=match_result.cliente.cod if match_result.identificado else None,
                    cliente_cnpj=extraction.cnpj,
                    banco=extraction.banco,
                    tipo_documento=extraction.tipo_documento,
                    agencia=extraction.agencia,
                    conta=extraction.conta,
                    ano=ano,
                    mes=mes,
                    metodo_identificacao=match_result.metodo.value,
                    confianca_ia=extraction.confianca,
                    erro=match_result.motivo_fallback if not match_result.identificado else None,
                )
            else:
                # MODO PRODUÇÃO: Salva log oficial
                db_log_service = get_db_log_service()
                db_log_service.log_extrato(
                    arquivo_original=filename,
                    status=proc_status.value,
                    arquivo_salvo=saved_path,
                    hash_arquivo=file_hash,
                    cliente_nome=cliente_nome,
                    cliente_cod=match_result.cliente.cod if match_result.identificado else None,
                    cliente_cnpj=extraction.cnpj,
                    banco=extraction.banco,
                    tipo_documento=extraction.tipo_documento,
                    agencia=extraction.agencia,
                    conta=extraction.conta,
                    ano=ano,
                    mes=mes,
                    metodo_identificacao=match_result.metodo.value,
                    confianca_ia=extraction.confianca,
                    erro=match_result.motivo_fallback if not match_result.identificado else None,
                )
        except Exception as e:
            logger.error(f"Erro ao salvar log no banco de dados: {e}")
        
        logger.info(f"Processamento concluido: {filename} -> {proc_status.value} (cliente: {cliente_nome or 'N/A'})")
        
        return ProcessingResult(
            nome_arquivo_original=filename,
            nome_arquivo_final=saved_path,
            status=proc_status,
            cliente_identificado=cliente_nome,
            metodo_identificacao=match_result.metodo,
            tipo_documento=extraction.tipo_documento,
            ano=ano,
            mes=mes,
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


# ====== ENDPOINTS DE LOGS DO BANCO DE DADOS ======

@app.get("/logs")
async def get_logs(
    limit: int = 100,
    offset: int = 0,
    status: str = None,
    cliente: str = None,
    ano: int = None,
    mes: int = None,
):
    """
    Consulta logs de extratos processados.
    
    Args:
        limit: Quantidade máxima de registros (padrão: 100)
        offset: Offset para paginação
        status: Filtrar por status (SUCESSO, NAO_IDENTIFICADO, FALHA)
        cliente: Buscar por nome do cliente (parcial)
        ano: Filtrar por ano
        mes: Filtrar por mês
    """
    try:
        db_service = get_db_log_service()
        logs = db_service.get_logs(
            limit=limit,
            offset=offset,
            status=status,
            cliente_nome=cliente,
            ano=ano,
            mes=mes,
        )
        return {
            "total": len(logs),
            "offset": offset,
            "limit": limit,
            "logs": [log.to_dict() for log in logs]
        }
    except Exception as e:
        logger.error(f"Erro ao consultar logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs/stats")
async def get_logs_stats():
    """Retorna estatísticas gerais dos logs."""
    try:
        db_service = get_db_log_service()
        return db_service.get_stats()
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs/{log_id}")
async def get_log_detail(log_id: int):
    """Busca detalhes de um log específico."""
    try:
        db_service = get_db_log_service()
        log = db_service.get_log_by_id(log_id)
        if not log:
            raise HTTPException(status_code=404, detail="Log não encontrado")
        return log.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar log: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ====== ENDPOINTS DE TESTE (NÃO SALVA ARQUIVOS) ======

@app.get("/test", response_class=HTMLResponse)
async def test_monitor_page():
    """Página de monitoramento de TESTES."""
    from pathlib import Path
    
    html_path = Path(__file__).parent / "templates" / "test_monitor.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

@app.post("/test/upload")
async def test_upload_file(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP para teste")],
    background_tasks: BackgroundTasks
):
    """
    MODO TESTE: Processa o arquivo mas NÃO salva efetivamente.
    
    RESPOSTA IMEDIATA com job_id para acompanhamento.
    Use GET /test/job/{job_id} para verificar o status.
    
    Suporta PDF individual ou ZIP contendo múltiplos PDFs.
    Simula todo o processamento (extração de texto, análise IA, matching)
    mas não salva o arquivo no sistema de arquivos.
    """
    # Validação básica
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nome do arquivo não fornecido")
    
    content = await file.read()
    filename = file.filename
    
    # Gera job_id único
    job_id = f"test_{uuid.uuid4().hex[:12]}"
    
    # Detecta se é ZIP
    is_zip = filename.lower().endswith('.zip') or content[:4] == b'PK\x03\x04'
    
    # Inicializa o job na tabela de TESTE
    _test_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "filename": filename,
        "is_zip": is_zip,
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "progress": 0,
        "total_files": 0,
        "processed_files": 0,
        "results": [],
        "errors": [],
        "stats": {}
    }
    
    logger.info(f"[TESTE] Job {job_id} criado para: {filename} ({len(content)} bytes)")
    
    # Inicia processamento em background usando a MESMA função de produção, com flag de teste
    background_tasks.add_task(
        process_file_background,
        job_id,
        content,
        filename,
        is_zip,
        test_mode=True
    )
    
    # Retorna imediatamente
    return {
        "modo": "TESTE",
        "job_id": job_id,
        "status": "queued",
        "filename": filename,
        "is_zip": is_zip,
        "message": "Arquivo recebido. Processamento iniciado em background.",
        "check_status_url": f"/test/job/{job_id}"
    }


# Funções antigas de teste background removidas - Agora usa o fluxo unificado


@app.get("/test/job/{job_id}")
async def get_test_job_status(job_id: str):
    """Verifica o status de um job de teste."""
    if job_id not in _test_jobs:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    
    return _test_jobs[job_id]



async def _process_single_test_pdf(
    pdf_content: bytes, 
    filename: str, 
    event_manager, 
    progress_base: int = 0,
    emit_events: bool = True
):
    """Processa um único PDF no modo teste e retorna o resultado."""
    file_hash = compute_hash(pdf_content)
    
    try:
        if emit_events:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.PROCESSING_STARTED,
                filename=filename,
                message="Iniciando processamento de teste...",
                progress=0
            ))
        
        # 1. Extrai texto do PDF
        if emit_events:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.EXTRACTING_TEXT,
                filename=filename,
                message="Extraindo texto do PDF...",
                progress=20
            ))
        pdf_service = get_pdf_service()
        text = pdf_service.extract_text(pdf_content)
        
        # 2. Analisa com IA
        if emit_events:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.ANALYZING,
                filename=filename,
                message="Analisando com IA...",
                progress=40
            ))
        llm_service = get_llm_service()
        extraction = llm_service.extract_info_with_fallback(text)
        
        # 3. Matching de cliente
        if emit_events:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.MATCHING,
                filename=filename,
                message="Identificando cliente...",
                progress=60
            ))
        matching_service = get_matching_service()
        match_result = matching_service.match(extraction)
        
        # 4. Calcula caminho que SERIA usado (sem salvar)
        storage_service = get_storage_service()
        ano, mes = storage_service._get_previous_month()
        
        if match_result.identificado:
            client_base_path = storage_service._resolve_client_path(match_result.cliente)
            if client_base_path:
                target_path = storage_service._build_path_structure(client_base_path, ano, mes)
                simulated_path = str(target_path / f"{extraction.tipo_documento}_{extraction.banco}.pdf")
            else:
                simulated_path = str(storage_service.settings.unidentified_path / filename)
            proc_status = ProcessingStatus.SUCESSO
            cliente_nome = match_result.cliente.nome
        else:
            simulated_path = str(storage_service.settings.unidentified_path / filename)
            proc_status = ProcessingStatus.NAO_IDENTIFICADO
            cliente_nome = None
        
        # 5. Salva apenas no banco de TESTE
        db_teste_service = get_db_log_teste_service()
        log_entry = db_teste_service.log_extrato_teste(
            arquivo_original=filename,
            status=proc_status.value,
            arquivo_salvo=simulated_path,
            hash_arquivo=file_hash,
            cliente_nome=cliente_nome,
            cliente_cod=match_result.cliente.cod if match_result.identificado else None,
            cliente_cnpj=extraction.cnpj,
            banco=extraction.banco,
            tipo_documento=extraction.tipo_documento,
            agencia=extraction.agencia,
            conta=extraction.conta,
            ano=ano,
            mes=mes,
            metodo_identificacao=match_result.metodo.value,
            confianca_ia=extraction.confianca,
            erro=match_result.motivo_fallback if not match_result.identificado else None,
        )
        
        # Emite evento de conclusão
        if emit_events:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.PROCESSING_COMPLETED,
                filename=filename,
                message=f"Processamento concluído: {proc_status.value}",
                details={
                    "status": proc_status.value,
                    "cliente": cliente_nome,
                    "path": simulated_path,
                    "banco": extraction.banco,
                    "tipo": extraction.tipo_documento,
                    "ano": ano,
                    "mes": mes,
                },
                progress=100
            ))
        
        logger.info(f"[TESTE] Processamento simulado: {filename} -> {proc_status.value}")
        
        return {
            "arquivo": filename,
            "status": proc_status.value,
            "cliente_identificado": cliente_nome,
            "banco": extraction.banco,
            "tipo_documento": extraction.tipo_documento,
            "caminho_simulado": simulated_path,
            "ano": ano,
            "mes": mes,
            "metodo": match_result.metodo.value,
            "confianca": extraction.confianca,
            "log_id": log_entry.id,
        }
        
    except Exception as e:
        logger.error(f"[TESTE] Erro ao processar {filename}: {e}")
        raise


@app.get("/test/logs")
async def get_test_logs(
    limit: int = 100,
    offset: int = 0,
    status: str = None,
    cliente: str = None,
):
    """Consulta logs de TESTE."""
    try:
        db_teste_service = get_db_log_teste_service()
        logs = db_teste_service.get_logs_teste(
            limit=limit,
            offset=offset,
            status=status,
            cliente_nome=cliente,
        )
        return {
            "modo": "TESTE",
            "total": len(logs),
            "logs": [log.to_dict() for log in logs]
        }
    except Exception as e:
        logger.error(f"Erro ao consultar logs de teste: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/test/stats")
async def get_test_stats():
    """Estatísticas dos logs de TESTE."""
    try:
        db_teste_service = get_db_log_teste_service()
        return db_teste_service.get_stats_teste()
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas de teste: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/test/logs")
async def clear_test_logs():
    """Limpa todos os logs de TESTE."""
    try:
        db_teste_service = get_db_log_teste_service()
        count = db_teste_service.limpar_logs_teste()
        return {"message": f"{count} logs de teste removidos", "count": count}
    except Exception as e:
        logger.error(f"Erro ao limpar logs de teste: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/test")
async def websocket_test_endpoint(websocket: WebSocket):
    """WebSocket para monitoramento de TESTES em tempo real."""
    event_manager = get_event_manager()
    # Usa o mesmo método connect que já aceita a conexão e adiciona à lista
    await event_manager.connect(websocket)
    
    try:
        while True:
            # Mantém a conexão viva
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type": "pong"}')
    except WebSocketDisconnect:
        event_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Erro no WebSocket de teste: {e}")
        event_manager.disconnect(websocket)


# ====== ENDPOINTS DE REVERSÃO ======

@app.get("/reversao", response_class=HTMLResponse)
async def reversao_page():
    """Página de gestão de reversões."""
    from pathlib import Path
    
    html_path = Path(__file__).parent / "templates" / "reversao.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/reversao/listar")
async def listar_para_reversao(
    limit: int = 100,
    offset: int = 0,
    status: str = None,
    cliente: str = None,
    apenas_existentes: bool = False,
):
    """Lista processamentos que podem ser revertidos."""
    try:
        reversao_service = get_reversao_service()
        logs = reversao_service.listar_processamentos(
            limit=limit,
            offset=offset,
            status=status,
            cliente=cliente,
            apenas_existentes=apenas_existentes,
        )
        return {
            "total": len(logs),
            "logs": logs
        }
    except Exception as e:
        logger.error(f"Erro ao listar para reversão: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/reversao/{log_id}")
async def reverter_por_id(log_id: int, deletar_arquivo: bool = True):
    """Reverte um único processamento pelo ID."""
    try:
        reversao_service = get_reversao_service()
        resultado = reversao_service.reverter_por_id(log_id, deletar_arquivo)
        
        if not resultado["success"]:
            raise HTTPException(status_code=400, detail=resultado["message"])
        
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reverter: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reversao/lote")
async def reverter_lote(ids: List[int], deletar_arquivos: bool = True):
    """Reverte múltiplos processamentos."""
    try:
        reversao_service = get_reversao_service()
        resultado = reversao_service.reverter_lote(ids, deletar_arquivos)
        return resultado
    except Exception as e:
        logger.error(f"Erro ao reverter lote: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reversao/ultimos/{quantidade}")
async def reverter_ultimos(quantidade: int, deletar_arquivos: bool = True):
    """Reverte os últimos N processamentos."""
    try:
        reversao_service = get_reversao_service()
        resultado = reversao_service.reverter_ultimos(quantidade, deletar_arquivos)
        return resultado
    except Exception as e:
        logger.error(f"Erro ao reverter últimos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/reversao/stats")
async def stats_reversao():
    """Estatísticas para a página de reversão."""
    try:
        reversao_service = get_reversao_service()
        return reversao_service.get_estatisticas()
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/reversao/historico")
async def historico_reversoes(
    limit: int = 100,
    offset: int = 0,
    cliente: str = None,
):
    """
    Lista histórico de reversões realizadas.
    
    Args:
        limit: Quantidade máxima de registros (padrão: 100)
        offset: Offset para paginação
        cliente: Buscar por nome do cliente (parcial)
    """
    try:
        reversao_service = get_reversao_service()
        reversoes = reversao_service.listar_reversoes(
            limit=limit,
            offset=offset,
            cliente=cliente,
        )
        return {
            "total": len(reversoes),
            "offset": offset,
            "limit": limit,
            "reversoes": reversoes
        }
    except Exception as e:
        logger.error(f"Erro ao listar histórico de reversões: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/reversao/historico/stats")
async def stats_historico_reversoes():
    """Estatísticas do histórico de reversões."""
    try:
        reversao_service = get_reversao_service()
        return reversao_service.get_stats_reversoes()
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas de reversões: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
