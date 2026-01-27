"""
API FastAPI para processamento de extratos contabeis.

Versao com processamento ASSINCRONO para evitar timeout.
O Make recebe resposta imediata e o arquivo e processado em background.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, List
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from app.config import get_settings
from app.events import (
    EventType,
    ProcessingEvent,
    get_event_manager,
    get_test_event_manager,
    get_extratos_event_manager,
    get_extratos_test_event_manager,
)
from app.schemas.api import ProcessingResult, ProcessingStatus, UploadResponse
from app.schemas.client import MatchMethod
from app.services import (
    ClientService,
    LLMService,
    MatchingService,
    PDFService,
    StorageService,
    ZIPService,
)
from app.services.db_log_service import get_db_log_service
from app.services.db_log_teste_service import get_db_log_teste_service
from app.services.db_extratos_baixados_log_service import get_extratos_baixados_log_service
from app.services.db_extratos_baixados_log_teste_service import (
    get_extratos_baixados_log_teste_service,
)
from app.services.reversao_service import get_reversao_service
from app.services.extratos_baixados_reversao_service import get_extratos_baixados_reversao_service
from app.services.extratos_baixados_simulacao_service import (
    ExtratosBaixadosSimulacaoService,
)
from app.utils.hash import compute_hash
from app.utils.template import render_tech_navbar

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

# Injeta a navbar em respostas HTML que contenham o placeholder.
@app.middleware("http")
async def _inject_navbar_middleware(request, call_next):
    response = await call_next(request)
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        return response

    if getattr(response, "body", None) is not None:
        body_bytes = response.body  # type: ignore[attr-defined]
    else:
        chunks = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            chunks.append(chunk)
        body_bytes = b"".join(chunks)

    if b"TECH_NAVBAR" not in body_bytes:
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
            background=response.background,
        )

    path = request.url.path or ""
    active_main: str | None = None
    active_extratos: str | None = None
    show_main = True
    show_extratos = True
    show_extratos_test = False

    if path == "/monitor":
        active_main = "monitor"
    elif path == "/test":
        active_main = "test"
    elif path == "/reversao":
        active_main = "reversao"
    elif path == "/extratos":
        active_extratos = "extratos"
    elif path == "/extratos/teste":
        active_extratos = "teste"
        show_extratos_test = True
    elif path == "/extratos/simular":
        active_extratos = "simulacao"
    elif path == "/extratos/reversao":
        active_extratos = "reversao-extratos"

    navbar_html = render_tech_navbar(
        active_main=active_main,
        active_extratos=active_extratos,
        show_main=show_main,
        show_extratos=show_extratos,
        show_extratos_test=show_extratos_test,
    )
    navbar_placeholder_re = re.compile(r"\{\{\s*TECH_NAVBAR\s*\}\}|\{\s*TECH_NAVBAR\s*\}")
    body_text = body_bytes.decode("utf-8", errors="replace")
    rendered = navbar_placeholder_re.sub(lambda _m: navbar_html, body_text)
    rendered_bytes = rendered.encode("utf-8")

    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(
        content=rendered_bytes,
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
        background=response.background,
    )

# Incluir rotas de teste de extratos
from app.routes.extratos_test import router as extratos_test_router
app.include_router(extratos_test_router)

# Cache de hashes processados para idempotencia
_processed_hashes: set[str] = set()

# Cache de hashes processados para extratos baixados (idempotencia)
_extratos_processed_hashes: set[str] = set()

# Armazenamento de jobs para consulta de status
_jobs: dict[str, dict] = {}

# Armazenamento de jobs de TESTE
_test_jobs: dict[str, dict] = {}

# Armazenamento de jobs de EXTRATOS BAIXADOS (PRODUCAO)
_extratos_jobs: dict[str, dict] = {}

# Armazenamento de jobs de EXTRATOS BAIXADOS (TESTE)
_extratos_test_jobs: dict[str, dict] = {}

# Executor para tarefas em background
_executor = ThreadPoolExecutor(max_workers=4)

# Watcher de pasta de entrada (controlado por endpoints)
_watch_task: asyncio.Task | None = None
_watch_running: bool = False
_watch_seen: dict[str, int] = {}
_watch_processed: dict[str, float] = {}


# Instancias dos servicos (singleton pattern simples)
_pdf_service: PDFService | None = None
_zip_service: ZIPService | None = None
_llm_service: LLMService | None = None
_client_service: ClientService | None = None
_matching_service: MatchingService | None = None
_storage_service: StorageService | None = None
_extratos_sim_service: ExtratosBaixadosSimulacaoService | None = None


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


def get_extratos_sim_service() -> ExtratosBaixadosSimulacaoService:
    global _extratos_sim_service
    if _extratos_sim_service is None:
        _extratos_sim_service = ExtratosBaixadosSimulacaoService()
    return _extratos_sim_service


def _render_template_with_navbar(
    template_path: Path,
    *,
    active_main: str | None = None,
    active_extratos: str | None = None,
    show_main: bool = True,
    show_extratos: bool = False,
    show_extratos_test: bool = False,
) -> str:
    html = template_path.read_text(encoding="utf-8")
    navbar_html = render_tech_navbar(
        active_main=active_main,
        active_extratos=active_extratos,
        show_main=show_main,
        show_extratos=show_extratos,
        show_extratos_test=show_extratos_test,
    )
    # Aceita tanto "{{TECH_NAVBAR}}" quanto "{TECH_NAVBAR}" (pode acontecer se algum passo aplicar str.format).
    navbar_placeholder_re = re.compile(r"\{\{\s*TECH_NAVBAR\s*\}\}|\{\s*TECH_NAVBAR\s*\}")
    return navbar_placeholder_re.sub(lambda _m: navbar_html, html)


# ============================================================
# DASHBOARD DE MONITORAMENTO
# ============================================================

@app.get("/monitor", response_class=HTMLResponse)
async def monitor_dashboard():
    """Dashboard de monitoramento em tempo real."""
    template_path = Path(__file__).parent / "templates" / "monitor.html"
    
    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template do monitor nao encontrado")
    
    return HTMLResponse(content=_render_template_with_navbar(template_path, active_main="monitor", show_main=True, show_extratos=True))


@app.get("/extratos", response_class=HTMLResponse)
async def extratos_page():
    """Pagina inicial de extratos."""
    template_path = Path(__file__).parent / "templates" / "extratos.html"

    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template de extratos nao encontrado")

    return HTMLResponse(content=_render_template_with_navbar(template_path, active_extratos="extratos", show_main=True, show_extratos=True))


@app.get("/extratos/teste", response_class=HTMLResponse)
async def extratos_teste_page():
    """Pagina de teste de processamento de extratos."""
    template_path = Path(__file__).parent / "templates" / "extratos_teste.html"

    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template de teste de extratos nao encontrado")

    return HTMLResponse(content=_render_template_with_navbar(template_path, active_extratos="teste", show_main=True, show_extratos=True, show_extratos_test=True))


async def _watch_folder_loop():
    """Loop de observacao da pasta de entrada de extratos."""
    global _watch_running, _watch_seen, _watch_processed
    settings = get_settings()
    watch_path = settings.watch_folder_path

    logger.info(f"Iniciando loop do watcher para: {watch_path}")

    if not watch_path.exists() or not watch_path.is_dir():
        logger.error(f"Pasta de extratos nao encontrada: {watch_path}")
        _watch_running = False
        return

    logger.info(f"Watcher ativo! Monitorando: {watch_path}")

    try:
        iteration = 0
        while _watch_running:
            iteration += 1
            if iteration % 12 == 1:  # Log a cada 1 minuto (12 * 5seg)
                logger.info(f"Watcher ativo - iteracao {iteration} - arquivos pendentes: {len(_watch_seen)}")
            for file_path in watch_path.iterdir():
                if not file_path.is_file():
                    continue

                if file_path.suffix.lower() not in {".pdf", ".zip"}:
                    continue

                file_key = str(file_path.resolve())
                try:
                    stat_info = file_path.stat()
                except OSError:
                    continue

                size = stat_info.st_size
                mtime = stat_info.st_mtime

                if _watch_processed.get(file_key) == mtime:
                    continue

                last_size = _watch_seen.get(file_key)
                if last_size is None or last_size != size:
                    _watch_seen[file_key] = size
                    continue

                # Arquivo estavel, processar
                _watch_seen.pop(file_key, None)

                logger.info(f"Arquivo estavel detectado: {file_path.name} - Iniciando processamento")

                try:
                    content = file_path.read_bytes()
                except OSError as e:
                    logger.error(f"Erro ao ler arquivo {file_path}: {e}")
                    continue

                filename = file_path.name
                is_zip = file_path.suffix.lower() == ".zip"
                job_id = str(uuid.uuid4())[:8]

                logger.info(f"Criando job {job_id} para {filename}")

                _extratos_jobs[job_id] = {
                    "job_id": job_id,
                    "filename": filename,
                    "status": "processing",
                    "message": "Arquivo recebido via watcher de extratos, processamento iniciado",
                    "created_at": datetime.now().isoformat(),
                    "completed_at": None,
                    "results": None,
                    "source": "extratos",
                }

                _watch_processed[file_key] = mtime

                asyncio.create_task(
                    process_extratos_file_background(
                        job_id=job_id,
                        content=content,
                        filename=filename,
                        is_zip=is_zip,
                        test_mode=False,
                    )
                )

                logger.info(f"Job {job_id} criado e processamento iniciado em background")

            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("Watcher de extratos interrompido")
    finally:
        _watch_running = False


@app.get("/extratos/watch/status")
async def extratos_watch_status():
    """Status do watcher da pasta de extratos."""
    settings = get_settings()
    watch_path = settings.watch_folder_path

    return {
        "running": _watch_running,
        "watch_path": str(watch_path),
        "pending_files": len(_watch_seen),
        "path_exists": watch_path.exists(),
        "is_directory": watch_path.is_dir() if watch_path.exists() else False,
    }


@app.get("/extratos/watch/debug")
async def extratos_watch_debug():
    """Debug detalhado do watcher e configurações."""
    settings = get_settings()
    watch_path = settings.watch_folder_path

    # Tenta listar arquivos se a pasta existir
    files_in_folder = []
    try:
        if watch_path.exists() and watch_path.is_dir():
            files_in_folder = [
                {
                    "name": f.name,
                    "is_file": f.is_file(),
                    "size": f.stat().st_size if f.is_file() else None,
                    "extension": f.suffix.lower()
                }
                for f in watch_path.iterdir()
            ]
    except Exception as e:
        files_in_folder = [{"error": str(e)}]

    return {
        "watcher": {
            "running": _watch_running,
            "task_exists": _watch_task is not None,
            "pending_files": len(_watch_seen),
            "processed_files": len(_watch_processed),
        },
        "path": {
            "configured": str(watch_path),
            "exists": watch_path.exists(),
            "is_directory": watch_path.is_dir() if watch_path.exists() else False,
            "absolute": str(watch_path.resolve()) if watch_path.exists() else None,
        },
        "files_in_folder": files_in_folder[:20],  # Máximo 20 arquivos
        "total_files_in_folder": len(files_in_folder),
        "config_source": {
            "WATCH_FOLDER_PATH": str(settings.watch_folder_path),
            "EXTRATOS_EXCEL_PATH": str(settings.extratos_excel_path),
        }
    }


@app.get("/extratos/mapear")
async def mapear_extratos():
    """Lista todos os arquivos PDF disponíveis na pasta de extratos."""
    settings = get_settings()
    watch_path = settings.watch_folder_path

    if not watch_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Pasta não encontrada: {watch_path}"
        )

    if not watch_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Caminho não é um diretório: {watch_path}"
        )

    try:
        # Testa permissão de leitura primeiro
        try:
            test_list = list(watch_path.iterdir())
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail=f"Sem permissão para acessar a pasta: {watch_path}"
            )
        except OSError as e:
            logger.error(f"Erro de sistema ao acessar pasta: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Erro ao acessar pasta: {str(e)}"
            )

        pdf_files = []
        erros_leitura = []

        for file_path in watch_path.iterdir():
            try:
                if file_path.is_file() and file_path.suffix.lower() == '.pdf':
                    stat = file_path.stat()
                    pdf_files.append({
                        "nome": file_path.name,
                        "tamanho": stat.st_size,
                        "tamanho_mb": round(stat.st_size / (1024 * 1024), 2),
                        "modificado_em": stat.st_mtime,
                        "caminho_completo": str(file_path),
                    })
            except PermissionError:
                erros_leitura.append(f"{file_path.name} (sem permissão)")
                logger.warning(f"Sem permissão para ler: {file_path}")
            except Exception as e:
                erros_leitura.append(f"{file_path.name} ({str(e)})")
                logger.warning(f"Erro ao ler arquivo {file_path}: {e}")

        # Ordena por data de modificação (mais recente primeiro)
        pdf_files.sort(key=lambda x: x["modificado_em"], reverse=True)

        resultado = {
            "total": len(pdf_files),
            "pasta": str(watch_path),
            "arquivos": pdf_files
        }

        if erros_leitura:
            resultado["avisos"] = erros_leitura
            logger.warning(f"Arquivos com erro de leitura: {len(erros_leitura)}")

        return resultado

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao mapear arquivos: {e}")
        raise HTTPException(status_code=500, detail=f"Erro inesperado: {str(e)}")


@app.get("/extratos/simular", response_class=HTMLResponse)
async def extratos_simular_page():
    """Página de simulação de processamento de extratos."""
    template_path = Path(__file__).parent / "templates" / "extratos_simular.html"

    if not template_path.exists():
        raise HTTPException(status_code=500, detail="Template de simulação não encontrado")

    return HTMLResponse(content=_render_template_with_navbar(template_path, active_extratos="simulacao", show_main=True, show_extratos=True))


@app.post("/extratos/simular-arquivo")
async def simular_processamento_arquivo(request: dict):
    """
    Simula o processamento de um arquivo específico da pasta de extratos.

    NÃO salva o arquivo, apenas retorna onde seria salvo e as informações extraídas.
    """
    filename = request.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="Campo 'filename' é obrigatório")

    settings = get_settings()
    watch_path = settings.watch_folder_path
    file_path = watch_path / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {filename}")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Caminho não é um arquivo: {filename}")

    try:
        pdf_data = file_path.read_bytes()
        sim_service = get_extratos_sim_service()
        result = await sim_service.simular_arquivo(
            pdf_data=pdf_data,
            filename=filename,
            executor=_executor,
            caminho_origem=file_path,
        )

        logger.info(
            "[SIMULACAO] %s -> %s -> %s",
            filename,
            result.get("status"),
            result.get("caminho_destino"),
        )

        return result

    except Exception as e:
        logger.exception(f"Erro ao simular processamento de {filename}")
        raise HTTPException(status_code=500, detail=f"Erro ao processar: {str(e)}")


@app.post("/extratos/simular-todos")
async def simular_todos_extratos():
    """
    Simula o processamento de TODOS os arquivos PDF da pasta de extratos.

    Retorna uma lista com a simulação de cada arquivo.
    """
    settings = get_settings()
    watch_path = settings.watch_folder_path

    if not watch_path.exists():
        raise HTTPException(status_code=400, detail=f"Pasta não encontrada: {watch_path}")

    resultados = []
    erros = []

    # Lista todos os PDFs
    pdf_files = [f for f in watch_path.iterdir() if f.is_file() and f.suffix.lower() == '.pdf']

    logger.info(f"[SIMULACAO EM LOTE] Processando {len(pdf_files)} arquivos")

    sim_service = get_extratos_sim_service()

    for file_path in pdf_files:
        try:
            filename = file_path.name
            pdf_data = file_path.read_bytes()

            resultado = await sim_service.simular_arquivo(
                pdf_data=pdf_data,
                filename=filename,
                executor=_executor,
                caminho_origem=file_path,
            )

            resultados.append(resultado)
            logger.info("[SIMULACAO] %s -> %s", filename, resultado.get("status"))

        except Exception as e:
            logger.error(f"[SIMULACAO] Erro ao processar {file_path.name}: {e}")
            erros.append({
                "arquivo": file_path.name,
                "erro": str(e)
            })

    # Estatísticas
    total = len(resultados)
    sucesso = sum(1 for r in resultados if r["status"] == "SUCESSO")
    nao_identificado = sum(1 for r in resultados if r["status"] == "NAO_IDENTIFICADO")

    return {
        "total_arquivos": len(pdf_files),
        "processados": total,
        "erros": len(erros),
        "estatisticas": {
            "sucesso": sucesso,
            "nao_identificado": nao_identificado,
            "falha": len(erros),
        },
        "resultados": resultados,
        "erros_detalhes": erros if erros else None,
    }

class ExtratosSimulacaoWebhook(BaseModel):
    filename: str | None = None
    todos: bool = False

@app.post("/extratos/webhook/simulacao")
async def extratos_webhook_simulacao(payload: ExtratosSimulacaoWebhook):
    """
    Webhook específico da view Simulação de Extratos.
    - Se `todos=true`, simula todos os arquivos.
    - Se `filename` for informado, simula apenas esse arquivo.
    """
    if payload.todos:
        return await simular_todos_extratos()
    if payload.filename:
        return await simular_processamento_arquivo({"filename": payload.filename})
    raise HTTPException(status_code=400, detail="Informe 'filename' ou 'todos=true'")


@app.post("/extratos/watch/start")
async def extratos_watch_start():
    """Inicia o watcher da pasta de extratos."""
    global _watch_task, _watch_running
    settings = get_settings()
    watch_path = settings.watch_folder_path

    if _watch_running:
        return {"running": True, "message": "Watcher ja esta em execucao"}

    # Validação detalhada do caminho
    path_exists = watch_path.exists()
    is_directory = watch_path.is_dir() if path_exists else False

    logger.info(f"Tentando iniciar watcher para: {watch_path}")
    logger.info(f"Path existe: {path_exists}, é diretório: {is_directory}")

    if not path_exists:
        error_msg = f"Pasta nao encontrada: {watch_path}"
        logger.error(error_msg)
        raise HTTPException(
            status_code=400,
            detail={
                "error": error_msg,
                "path": str(watch_path),
                "exists": False,
                "suggestion": "Verifique se o caminho WATCH_FOLDER_PATH no .env esta correto e se a pasta existe no sistema"
            }
        )

    if not is_directory:
        error_msg = f"Caminho existe mas nao e um diretorio: {watch_path}"
        logger.error(error_msg)
        raise HTTPException(
            status_code=400,
            detail={
                "error": error_msg,
                "path": str(watch_path),
                "exists": True,
                "is_directory": False
            }
        )

    _watch_running = True
    _watch_task = asyncio.create_task(_watch_folder_loop())
    logger.info(f"Watcher iniciado com sucesso em: {watch_path}")

    return {
        "running": True,
        "message": "Watcher iniciado com sucesso",
        "watch_path": str(watch_path),
        "path_exists": True,
        "is_directory": True
    }


@app.post("/extratos/watch/stop")
async def extratos_watch_stop():
    """Interrompe o watcher da pasta de extratos."""
    global _watch_task, _watch_running
    if not _watch_running:
        return {"running": False, "message": "Watcher ja esta parado"}

    _watch_running = False
    if _watch_task:
        _watch_task.cancel()
        _watch_task = None

    return {"running": False, "message": "Watcher interrompido"}


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




@app.get("/extratos/monitor/stats")
async def extratos_monitor_stats():
    """Retorna estatisticas do sistema de arquivos para extratos baixados."""
    settings = get_settings()
    unidentified_path = settings.unidentified_path

    count_unidentified = 0
    if unidentified_path.exists():
        count_unidentified = sum(1 for _ in unidentified_path.rglob("*") if _.is_file())

    return {
        "unidentified_files_count": count_unidentified,
        "unidentified_path": str(unidentified_path),
    }
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para atualizacoes em tempo real (PRODUÇÃO)."""
    event_manager = get_event_manager()
    await event_manager.connect(websocket)
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_manager.disconnect(websocket)


@app.websocket("/ws/test")
async def websocket_test_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para atualizacoes em tempo real (TESTE)."""
    test_event_manager = get_test_event_manager()
    await test_event_manager.connect(websocket)
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        test_event_manager.disconnect(websocket)


@app.websocket("/ws/extratos")
async def websocket_extratos_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para atualizacoes em tempo real (EXTRATOS BAIXADOS)."""
    event_manager = get_extratos_event_manager()
    await event_manager.connect(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_manager.disconnect(websocket)


@app.websocket("/ws/extratos/test")
async def websocket_extratos_test_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para atualizacoes em tempo real (EXTRATOS BAIXADOS - TESTE)."""
    event_manager = get_extratos_test_event_manager()
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
    """Endpoint de health check completo."""
    settings = get_settings()

    # Valida caminhos
    paths_status = settings.validate_paths()
    paths_ok = all(paths_status.values())

    # Valida conexão com banco de dados
    db_status = settings.validate_database_connection()

    return {
        "status": "healthy" if (paths_ok and db_status["connected"]) else "degraded",
        "timestamp": datetime.now().isoformat(),
        "jobs_pending": sum(1 for j in _jobs.values() if j["status"] == "processing"),
        "database": db_status,
        "paths": paths_status,
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
        "extratos": "/extratos",
    }


@app.get("/config")
async def get_config():
    """
    Retorna as configurações atuais do sistema (sem dados sensíveis).

    Útil para debug e verificação de configurações.
    """
    settings = get_settings()
    return settings.get_summary()


@app.get("/config/validate")
async def validate_config():
    """
    Valida todas as configurações do sistema.

    Verifica:
    - Existência de caminhos configurados
    - Conexão com banco de dados
    - Configurações essenciais
    """
    settings = get_settings()

    # Valida caminhos
    paths_status = settings.validate_paths()

    # Valida banco de dados
    db_status = settings.validate_database_connection()

    # Verifica API key da OpenAI
    has_openai_key = bool(settings.openai_api_key and len(settings.openai_api_key) > 20)

    all_paths_ok = all(paths_status.values())
    db_ok = db_status["connected"]

    return {
        "status": "ok" if (all_paths_ok and db_ok and has_openai_key) else "error",
        "paths": {
            "status": "ok" if all_paths_ok else "error",
            "details": paths_status,
        },
        "database": db_status,
        "openai": {
            "status": "ok" if has_openai_key else "error",
            "configured": has_openai_key,
            "model": settings.llm_model,
        },
        "summary": settings.get_summary(),
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

@app.post("/make/webhook/monitor")
async def make_webhook_monitor(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP para processar (MAKE Monitor)")],
    background_tasks: BackgroundTasks,
):
    """
    Webhook específico do Módulo MAKE para a view Monitor.
    Mesmo fluxo do /upload, mas com rota dedicada.
    """
    return await upload_file(file=file, background_tasks=background_tasks)


async def _handle_extratos_webhook(
    *,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    source: str,
    test_mode: bool = False,
):
    content = await file.read()
    filename = file.filename or "webhook_extratos"

    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio")

    is_zip = content.startswith(b"PK") or filename.lower().endswith(".zip")

    if test_mode:
        job_id = f"test_{uuid.uuid4().hex[:12]}"
        jobs_dict = _extratos_test_jobs
        jobs_dict[job_id] = {
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
            "stats": {},
            "source": source,
        }
    else:
        job_id = str(uuid.uuid4())[:8]
        jobs_dict = _extratos_jobs
        jobs_dict[job_id] = {
            "job_id": job_id,
            "filename": filename,
            "status": "processing",
            "message": "Arquivo recebido via webhook de extratos",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "results": None,
            "source": source,
        }

    background_tasks.add_task(
        process_extratos_file_background,
        job_id,
        content,
        filename,
        is_zip,
        test_mode=test_mode,
    )

    return {
        "success": True,
        "job_id": job_id,
        "message": "Webhook recebido! Processamento iniciado em background.",
        "status_url": f"/extratos/job/{job_id}" if not test_mode else None,
        "check_status_url": f"/extratos/test/job/{job_id}" if test_mode else None,
        "source": source,
        "test_mode": test_mode,
    }


@app.post("/extratos/webhook")
async def extratos_webhook_prod(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP de extratos")],
    background_tasks: BackgroundTasks,
):
    """
    Webhook de produção para extratos.
    Mesmo fluxo do /upload, mas com rota dedicada.
    """
    return await _handle_extratos_webhook(
        file=file,
        background_tasks=background_tasks,
        source="monitor",
        test_mode=False,
    )


@app.post("/extratos/webhook/test")
async def extratos_webhook_test(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP de extratos para teste")],
    background_tasks: BackgroundTasks
):
    """
    Webhook de teste para extratos.
    Mesmo fluxo do /test/upload, mas com rota dedicada.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nome do arquivo nao fornecido")

    return await _handle_extratos_webhook(
        file=file,
        background_tasks=background_tasks,
        source="monitor-test",
        test_mode=True,
    )

@app.post("/extratos/webhook/monitor")
async def extratos_webhook_monitor(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP de extratos (Monitor Extratos)")],
    background_tasks: BackgroundTasks,
):
    """Webhook específico da view Monitor Extratos (produção)."""
    return await _handle_extratos_webhook(
        file=file,
        background_tasks=background_tasks,
        source="monitor",
        test_mode=False,
    )

@app.post("/extratos/webhook/extratos")
async def extratos_webhook_extratos(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP de extratos (Extratos)")],
    background_tasks: BackgroundTasks,
):
    """Webhook específico da view Extratos (produção)."""
    return await _handle_extratos_webhook(
        file=file,
        background_tasks=background_tasks,
        source="extratos",
        test_mode=False,
    )

@app.post("/extratos/webhook/monitor/test")
async def extratos_webhook_monitor_test(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP de extratos (Monitor Teste)")],
    background_tasks: BackgroundTasks,
):
    """Webhook específico da view Monitor Teste (extratos baixados)."""
    return await _handle_extratos_webhook(
        file=file,
        background_tasks=background_tasks,
        source="monitor-test",
        test_mode=True,
    )


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



@app.get("/extratos/job/{job_id}")
async def get_extratos_job_status(job_id: str):
    """Verifica status de job de extratos baixados."""
    if job_id not in _extratos_jobs:
        raise HTTPException(status_code=404, detail="Job nao encontrado")
    return _extratos_jobs[job_id]

@app.get("/extratos/jobs")
async def list_extratos_jobs(source: str | None = None):
    """Lista jobs recentes de extratos baixados."""
    jobs_list = list(_extratos_jobs.values())
    if source:
        jobs_list = [job for job in jobs_list if job.get("source") == source]
    jobs_list.sort(key=lambda x: x["created_at"], reverse=True)
    return {
        "total": len(jobs_list),
        "jobs": jobs_list[:50]
    }

@app.get("/extratos/test/job/{job_id}")
async def get_extratos_test_job_status(job_id: str):
    """Verifica status de job de extratos baixados (teste)."""
    if job_id not in _extratos_test_jobs:
        raise HTTPException(status_code=404, detail="Job nao encontrado")
    return _extratos_test_jobs[job_id]

@app.get("/extratos/test/jobs")
async def list_extratos_test_jobs(source: str | None = None):
    """Lista jobs recentes de extratos baixados (teste)."""
    jobs_list = list(_extratos_test_jobs.values())
    if source:
        jobs_list = [job for job in jobs_list if job.get("source") == source]
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
    # Usa o event manager correto baseado no modo
    event_manager = get_test_event_manager() if test_mode else get_event_manager()
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
        
        # Se for ZIP, precisamos emitir um evento de conclusão para o arquivo ZIP principal
        # para que o frontend remova o card de processamento
        if is_zip:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.PROCESSING_COMPLETED,
                filename=filename,
                message=f"ZIP processado: {result.total_arquivos} arquivos.",
                details={
                    "status": "SUCESSO",
                    "cliente": "LOTE ZIP COMPLETO",
                    "path": "-",
                    "banco": "-",
                    "tipo": "ZIP",
                    "ano": "-",
                    "mes": "-",
                    "metodo": "-",
                    "log_id": None
                },
                progress=100
            ))
        
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
    # Usa o event manager correto baseado no modo
    event_manager = get_test_event_manager() if test_mode else get_event_manager()
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
    # Usa o event manager correto baseado no modo
    event_manager = get_test_event_manager() if test_mode else get_event_manager()
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
        # Executar LLM em thread separada (com fallback de visão para identificar banco)
        loop = asyncio.get_event_loop()
        extraction = await loop.run_in_executor(_executor, llm_service.extract_info_with_fallback, text, pdf_data)
        
        # Check Cancelamento
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
             raise asyncio.CancelledError("Cancelado pelo usuário")

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.LLM_COMPLETED,
            filename=filename,
            message=f"Analise textual concluida: {extraction.cliente_sugerido or 'N/A'}",
            details={
                "cliente": extraction.cliente_sugerido,
                "banco": extraction.banco,
                "tipo": extraction.tipo_documento,
                "confianca": extraction.confianca
            },
            progress=40
        ))

        # --- FALLBACK VISUAL: Se não achou banco, tenta olhar o logo ---
        if not extraction.banco and pdf_service.is_valid_pdf(pdf_data):
            try:
                await event_manager.emit(ProcessingEvent(
                    event_type=EventType.LLM_ANALYZING,
                    filename=filename,
                    message="Banco não identificado no texto. Analisando logos (Visão IA)...",
                    progress=45
                ))
                
                # Extrai imagens da primeira página
                images = await loop.run_in_executor(_executor, pdf_service.extract_first_page_images, pdf_data)
                
                if images:
                    # Pergunta pra LLM via visão
                    banco_visual = await loop.run_in_executor(_executor, llm_service.identify_bank_from_images, images)
                    
                    if banco_visual:
                        extraction.banco = banco_visual
                        logger.info(f"Banco identificado visualmente: {banco_visual}")
                        
                        await event_manager.emit(ProcessingEvent(
                            event_type=EventType.LLM_COMPLETED,
                            filename=filename,
                            message=f"Banco identificado visualmente: {banco_visual}",
                            details={"banco": banco_visual},
                            progress=50
                        ))
            except Exception as e:
                logger.warning(f"Erro no fallback visual: {e}")
        # ----------------------------------------------------------------
        
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
                    # Usa a conta extraída do extrato (prioritário) ou a conta da planilha
                    conta = storage_service._select_account(extraction.banco, extraction.conta, match_result.cliente.conta)
                    target_path = storage_service._build_path_structure(
                        client_base_path,
                        ano,
                        mes,
                        extraction.banco,
                        conta
                    )

                    # Constrói o nome do arquivo usando a mesma lógica do storage_service
                    file_name = storage_service._build_filename(
                        extraction.banco,
                        extraction.tipo_documento,
                        pdf_data,
                        target_path,
                        filename
                    )
                    saved_path = str(target_path / file_name)
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
                conta_extrato=extraction.conta,
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
        
        # 6. Registra no log do banco de dados
        log_id = None
        if not test_mode:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.LOG_WRITING,
                filename=filename,
                message="Registrando no banco de dados...",
                progress=90
            ))
            
            # Salva log no banco de dados ANTES de emitir evento de conclusão
            try:
                db_log_service = get_db_log_service()
                log_entry = db_log_service.log_extrato(
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
                log_id = log_entry.id
            except Exception as e:
                logger.error(f"Erro ao salvar log no banco de dados: {e}")
            
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.LOG_WRITTEN,
                filename=filename,
                message="Log registrado",
                progress=95
            ))
        else:
            # MODO TESTE: Salva apenas registro de teste
            try:
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
            except Exception as e:
                logger.error(f"Erro ao salvar log de teste no banco de dados: {e}")
        
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
                "log_id": log_id,  # ID do log no banco para reversão
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
    
    return ProcessingResult(
        nome_arquivo_original=filename,
        status=ProcessingStatus.FALHA,
        hash_arquivo=file_hash,
        erro=error,
    )


# ============================================================
# PROCESSAMENTO EM BACKGROUND (EXTRATOS BAIXADOS)
# ============================================================

async def process_extratos_file_background(
    job_id: str,
    content: bytes,
    filename: str,
    is_zip: bool,
    test_mode: bool = False,
):
    """Processa arquivo de extratos baixados em background."""
    event_manager = get_extratos_test_event_manager() if test_mode else get_extratos_event_manager()
    jobs_dict = _extratos_test_jobs if test_mode else _extratos_jobs

    try:
        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_RECEIVED,
            filename=filename,
            message=f"Arquivo recebido: {filename}",
            details={"size": len(content), "job_id": job_id, "test_mode": test_mode}
        ))

        if is_zip:
            result = await process_extratos_zip_async(content, filename, job_id, test_mode)
        else:
            result = await process_extratos_pdf_async(content, filename, job_id, test_mode)
            result = UploadResponse(
                sucesso=result.status == ProcessingStatus.SUCESSO,
                total_arquivos=1,
                arquivos_sucesso=1 if result.status == ProcessingStatus.SUCESSO else 0,
                arquivos_nao_identificados=1 if result.status == ProcessingStatus.NAO_IDENTIFICADO else 0,
                arquivos_falha=1 if result.status == ProcessingStatus.FALHA else 0,
                resultados=[result],
            )

        if is_zip:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.PROCESSING_COMPLETED,
                filename=filename,
                message=f"ZIP processado: {result.total_arquivos} arquivos.",
                details={
                    "status": "SUCESSO",
                    "cliente": "LOTE ZIP COMPLETO",
                    "path": "-",
                    "banco": "-",
                    "tipo": "ZIP",
                    "ano": "-",
                    "mes": "-",
                    "metodo": "-",
                    "log_id": None,
                },
                progress=100
            ))

        jobs_dict[job_id].update({
            "status": "completed",
            "message": (
                "Processamento concluido: "
                f"{result.arquivos_sucesso} sucesso, "
                f"{result.arquivos_nao_identificados} nao identificados, "
                f"{result.arquivos_falha} falhas"
            ),
            "completed_at": datetime.now().isoformat(),
            "results": result.model_dump(),
        })
    except Exception as e:
        logger.exception(f"Erro no processamento do job {job_id} (extratos baixados)")
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


async def process_extratos_zip_async(
    zip_data: bytes,
    filename: str,
    job_id: str | None = None,
    test_mode: bool = False,
) -> UploadResponse:
    """Processa um arquivo ZIP contendo PDFs (extratos baixados)."""
    event_manager = get_extratos_test_event_manager() if test_mode else get_extratos_event_manager()
    zip_service = get_zip_service()
    jobs_dict = _extratos_test_jobs if test_mode else _extratos_jobs

    if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
        raise asyncio.CancelledError("Cancelado pelo usuario")

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
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
            logger.warning(f"Processamento ZIP cancelado: {filename}")
            break

        result = await process_extratos_pdf_async(
            extracted_file.data,
            extracted_file.filename,
            job_id,
            test_mode,
        )
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


async def process_extratos_pdf_async(
    pdf_data: bytes,
    filename: str,
    job_id: str | None = None,
    test_mode: bool = False,
) -> ProcessingResult:
    """Processa um unico arquivo PDF (extratos baixados)."""
    event_manager = get_extratos_test_event_manager() if test_mode else get_extratos_event_manager()
    file_hash = compute_hash(pdf_data)
    jobs_dict = _extratos_test_jobs if test_mode else _extratos_jobs

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

    if file_hash in _extratos_processed_hashes:
        logger.info(f"Arquivo ja processado (hash: {file_hash[:8]}): {filename}")
        event_manager.end_processing()
        return ProcessingResult(
            nome_arquivo_original=filename,
            status=ProcessingStatus.SUCESSO,
            hash_arquivo=file_hash,
            erro="Arquivo ja processado anteriormente (duplicado)",
        )

    _extratos_processed_hashes.add(file_hash)

    is_pdf = pdf_data.startswith(b"%PDF-") or filename.lower().endswith(".pdf")
    if not is_pdf:
        logger.info(f"Processando arquivo nao-PDF: {filename}")

    try:
        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
            raise asyncio.CancelledError("Cancelado pelo usuario")

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.PDF_TEXT_EXTRACTING,
            filename=filename,
            message=f"Extraindo conteudo de {filename}...",
            progress=10
        ))

        pdf_service = get_pdf_service()
        try:
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(_executor, pdf_service.extract_text, pdf_data, filename)
        except ValueError as e:
            return await create_extratos_failure_result(
                filename,
                file_hash,
                f"Erro ao extrair conteudo: {e}",
                test_mode=test_mode,
            )

        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
            raise asyncio.CancelledError("Cancelado pelo usuario")

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.PDF_TEXT_EXTRACTED,
            filename=filename,
            message=f"Conteudo extraido: {len(text)} caracteres",
            details={"chars": len(text)},
            progress=25
        ))

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.LLM_ANALYZING,
            filename=filename,
            message="Analisando documento com IA...",
            progress=30
        ))

        llm_service = get_llm_service()
        loop = asyncio.get_event_loop()
        extraction = await loop.run_in_executor(_executor, llm_service.extract_info_with_fallback, text, pdf_data)

        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
            raise asyncio.CancelledError("Cancelado pelo usuario")

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.LLM_COMPLETED,
            filename=filename,
            message=f"Analise concluida: {extraction.cliente_sugerido or 'N/A'}",
            details={
                "cliente": extraction.cliente_sugerido,
                "banco": extraction.banco,
                "tipo": extraction.tipo_documento,
                "confianca": extraction.confianca,
            },
            progress=50
        ))

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.MATCHING_START,
            filename=filename,
            message="Buscando cliente na base...",
            progress=55
        ))

        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
            raise asyncio.CancelledError("Cancelado pelo usuario")

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
                "score": match_result.score,
            },
            progress=70
        ))

        if job_id and jobs_dict.get(job_id, {}).get("status") == "cancelled":
            raise asyncio.CancelledError("Cancelado pelo usuario")

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_SAVING,
            filename=filename,
            message="Salvando arquivo...",
            progress=75
        ))

        storage_service = get_storage_service()

        if test_mode:
            ano, mes = storage_service._get_previous_month()
            if match_result.identificado:
                client_base_path = storage_service._resolve_client_path(match_result.cliente)
                if client_base_path:
                    conta = storage_service._select_account(extraction.banco, extraction.conta, match_result.cliente.conta)
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
                        pdf_data,
                        target_path,
                        filename,
                    )
                    saved_path = str(target_path / file_name)
                else:
                    saved_path = str(storage_service.settings.unidentified_path / filename)
            else:
                saved_path = str(storage_service.settings.unidentified_path / filename)
            logger.info(f"[TESTE EXTRATOS] Arquivo seria salvo em: {saved_path}")
        else:
            saved_path, ano, mes = storage_service.save_file(
                pdf_data=pdf_data,
                match_result=match_result,
                original_filename=filename,
                tipo_documento=extraction.tipo_documento,
                banco=extraction.banco,
                conta_extrato=extraction.conta,
            )

        await event_manager.emit(ProcessingEvent(
            event_type=EventType.FILE_SAVED,
            filename=filename,
            message="Arquivo salvo (Simulado)" if test_mode else "Arquivo salvo",
            details={"path": saved_path},
            progress=85
        ))

        if match_result.identificado:
            proc_status = ProcessingStatus.SUCESSO
            cliente_nome = match_result.cliente.nome
        else:
            proc_status = ProcessingStatus.NAO_IDENTIFICADO
            cliente_nome = None

        log_id = None
        if not test_mode:
            await event_manager.emit(ProcessingEvent(
                event_type=EventType.LOG_WRITING,
                filename=filename,
                message="Registrando no banco de dados...",
                progress=90
            ))
            try:
                db_log_service = get_extratos_baixados_log_service()
                log_entry = db_log_service.log_extrato(
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
                log_id = log_entry.id
            except Exception as e:
                logger.error(f"Erro ao salvar log de extratos baixados: {e}")

            await event_manager.emit(ProcessingEvent(
                event_type=EventType.LOG_WRITTEN,
                filename=filename,
                message="Log registrado",
                progress=95
            ))
        else:
            try:
                db_teste_service = get_extratos_baixados_log_teste_service()
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
            except Exception as e:
                logger.error(f"Erro ao salvar log de teste extratos baixados: {e}")

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
                "log_id": log_id,
            },
            progress=100
        ))

        event_manager.update_stats(
            sucesso=(proc_status == ProcessingStatus.SUCESSO),
            nao_identificado=(proc_status == ProcessingStatus.NAO_IDENTIFICADO),
            falha=(proc_status == ProcessingStatus.FALHA),
        )
        event_manager.end_processing()
        await event_manager.emit_stats()

        logger.info(
            "Processamento concluido (extratos baixados): %s -> %s",
            filename,
            proc_status.value,
        )

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
        logger.warning(f"Processamento cancelado explicitamente (extratos baixados): {filename}")
        event_manager.end_processing()
        return ProcessingResult(
            nome_arquivo_original=filename,
            status=ProcessingStatus.FALHA,
            hash_arquivo=file_hash,
            erro="Cancelado manualmente pelo usuario",
            nome_arquivo_final=""
        )
    except Exception as e:
        logger.exception(f"Erro inesperado ao processar extratos baixados: {filename}")
        return await create_extratos_failure_result(filename, file_hash, str(e), test_mode=test_mode)


async def create_extratos_failure_result(
    filename: str,
    file_hash: str,
    error: str,
    test_mode: bool = False,
) -> ProcessingResult:
    """Cria um resultado de falha para extratos baixados."""
    event_manager = get_extratos_test_event_manager() if test_mode else get_extratos_event_manager()

    await event_manager.emit(ProcessingEvent(
        event_type=EventType.PROCESSING_ERROR,
        filename=filename,
        message=error
    ))

    event_manager.update_stats(falha=True)
    event_manager.end_processing()
    await event_manager.emit_stats()

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
        "watch_folder_path": str(new_settings.watch_folder_path),
    }


@app.get("/monitor/history")
async def get_history():
    """Retorna o histórico de processamento persistente do banco de dados."""
    try:
        db_log_service = get_db_log_service()
        logs = db_log_service.get_logs(limit=100)
        
        # Mapeia para o formato esperado pelo frontend
        result = []
        for log in logs:
            result.append({
                "timestamp": log.processado_em.isoformat() if log.processado_em else None,
                "data_hora_formatada": log.processado_em.strftime("%d/%m/%Y, %H:%M:%S") if log.processado_em else "-",
                "cliente": log.cliente_nome or "NÃO IDENTIFICADO",
                "tipo": log.tipo_documento or "-",
                "banco": log.banco or "-",
                "status": log.status,
                "filename": log.arquivo_original or "-",
                "full_path": log.arquivo_salvo or "-",
                "periodo": f"{log.mes}/{log.ano}" if log.ano and log.mes else "-",
                "log_id": log.id,  # ID para reversão
            })
        
        return result
    except Exception as e:
        logger.error(f"Erro ao buscar histórico: {e}")
        return []



@app.get("/extratos/monitor/history")
async def get_extratos_history():
    """Retorna o historico de extratos baixados do banco."""
    try:
        db_log_service = get_extratos_baixados_log_service()
        logs = db_log_service.get_logs(limit=100)

        result = []
        for log in logs:
            result.append({
                "timestamp": log.processado_em.isoformat() if log.processado_em else None,
                "data_hora_formatada": log.processado_em.strftime("%d/%m/%Y, %H:%M:%S") if log.processado_em else "-",
                "cliente": log.cliente_nome or "NAO IDENTIFICADO",
                "tipo": log.tipo_documento or "-",
                "banco": log.banco or "-",
                "status": log.status,
                "filename": log.arquivo_original or "-",
                "full_path": log.arquivo_salvo or "-",
                "periodo": f"{log.mes}/{log.ano}" if log.ano and log.mes else "-",
                "log_id": log.id,
            })

        return result
    except Exception as e:
        logger.error(f"Erro ao buscar historico de extratos baixados: {e}")
        return []
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


@app.delete("/logs")
async def clear_all_logs():
    """
    Limpa TODOS os logs de produção.
    Usa o serviço de reversão para garantir que arquivos físicos também sejam removidos (se existirem)
    e que as estatísticas sejam atualizadas corretamente.
    Força a limpeza do cache em memória (_jobs) para evitar dados fantasmas.
    """
    try:
        from app.database import SessionLocal
        from app.models.extrato_log import ExtratoLog
        
        # IMPORTANTE: Limpa caches em memória incondicionalmente
        # Isso resolve o problema de dados fantasmas que persistem após limpeza manual do banco/arquivos
        qtde_memory = len(_jobs)
        _jobs.clear()
        _processed_hashes.clear()
        
        # 1. Busca todos os IDs
        db = SessionLocal()
        try:
            logs = db.query(ExtratoLog).with_entities(ExtratoLog.id).all()
            log_ids = [l.id for l in logs]
        finally:
            db.close()
            
        count_db = 0
        resultado_detalhes = {}
        
        if log_ids:
            # 2. Chama reverter_lote para garantir consistência de arquivos e banco
            reversao_service = get_reversao_service()
            resultado = reversao_service.reverter_lote(log_ids)
            count_db = len(log_ids)
            resultado_detalhes = resultado
        
        # 3. Emite evento de zeramento total para atualizar todos os clientes conectados
        event_manager.emit_stats()
        
        return {
            "success": True, 
            "message": f"Limpeza completa realizada. Banco: {count_db} registros, Memória: {qtde_memory} jobs.",
            "details": resultado_detalhes
        }
        
    except Exception as e:
        logger.error(f"Erro ao limpar logs: {e}")
        # Retorna erro JSON válido para o frontend
        return JSONResponse(
            status_code=500,
            content={
                "success": False, 
                "message": f"Erro interno ao limpar logs: {str(e)}"
            }
        )


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



@app.post("/extratos/monitor/reset")
async def reset_extratos_processing():
    """Forca o encerramento dos processamentos de extratos baixados."""
    count = 0
    event_manager = get_extratos_event_manager()

    for job_id, job in _extratos_jobs.items():
        if job["status"] == "processing":
            job["status"] = "cancelled"
            job["message"] = "Processamento cancelado manualmente pelo usuario"
            job["completed_at"] = datetime.now().isoformat()

            await event_manager.emit(ProcessingEvent(
                event_type=EventType.PROCESSING_ERROR,
                filename=job["filename"],
                message="Cancelado manualmente"
            ))
            count += 1

    if count > 0:
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



@app.get("/logs/{log_id}/view")
async def view_log_file(log_id: int):
    """
    Retorna o conteúdo do arquivo associado ao log para visualização.
    Serve tanto para logs de produção quanto para logs de teste (simulado).
    """
    try:
        from fastapi.responses import FileResponse
        import os
        
        # 1. Tenta buscar no log de PRODUÇÃO
        db_service = get_db_log_service()
        log = db_service.get_log_by_id(log_id)
        
        file_path = None
        filename = None
        
        if log:
            file_path = log.arquivo_salvo
            filename = log.arquivo_original
        else:
            # 2. Se não achar, tenta buscar no log de TESTE
            db_teste_service = get_db_log_teste_service()
            # O serviço de teste geralmente não tem get_by_id exposto simples
            # Mas vamos tentar buscar nos logs recentes ou idealmente implementar um get_by_id no serviço de teste
            # Como hack rápido, vamos instanciar uma busca direta ou assumir que o ID pode ser de teste
            
            # Vamos tentar ler a tabela de testes diretamente
            from app.services.db_log_teste_service import TesteLog
            from app.database import SessionLocal
            
            db = SessionLocal()
            try:
                log_teste = db.query(TesteLog).filter(TesteLog.id == log_id).first()
                if log_teste:
                    file_path = log_teste.arquivo_salvo
                    filename = log_teste.arquivo_original
            finally:
                db.close()
            
        if not file_path or file_path == '-':
            # Se não tem caminho salvo, tenta procurar nos Nao Identificados
            # (Caso comum em falhas)
            if filename:
                 settings = get_settings()
                 potential_path = settings.unidentified_path / filename
                 if potential_path.exists():
                     file_path = str(potential_path)
        
        if not file_path or not os.path.exists(file_path):
             raise HTTPException(status_code=404, detail="Arquivo físico não encontrado no servidor.")
             
        # Serve o arquivo
        return FileResponse(
            path=file_path,
            filename=filename or "documento.pdf",
            media_type="application/pdf",
            content_disposition_type="inline" # Abre no navegador em vez de baixar
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao visualizar arquivo: {e}")
        raise HTTPException(status_code=500, detail=str(e))




@app.get("/extratos/logs")
async def get_extratos_logs(
    limit: int = 100,
    offset: int = 0,
    status: str = None,
    cliente: str = None,
    ano: int = None,
    mes: int = None,
):
    """Consulta logs de extratos baixados."""
    try:
        db_service = get_extratos_baixados_log_service()
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
            "logs": [log.to_dict() for log in logs],
        }
    except Exception as e:
        logger.error(f"Erro ao consultar logs de extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/extratos/logs/stats")
async def get_extratos_logs_stats():
    """Retorna estatisticas gerais dos logs de extratos baixados."""
    try:
        db_service = get_extratos_baixados_log_service()
        return db_service.get_stats()
    except Exception as e:
        logger.error(f"Erro ao obter estatisticas de extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/extratos/logs/{log_id}")
async def get_extratos_log_detail(log_id: int):
    """Busca detalhes de um log de extratos baixados."""
    try:
        db_service = get_extratos_baixados_log_service()
        log = db_service.get_log_by_id(log_id)
        if not log:
            raise HTTPException(status_code=404, detail="Log nao encontrado")
        return log.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar log de extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/extratos/logs/{log_id}/view")
async def view_extratos_log_file(log_id: int):
    """Retorna o arquivo associado ao log de extratos baixados."""
    try:
        from fastapi.responses import FileResponse
        import os

        db_service = get_extratos_baixados_log_service()
        log = db_service.get_log_by_id(log_id)

        if not log:
            raise HTTPException(status_code=404, detail="Log nao encontrado")

        file_path = log.arquivo_salvo
        filename = log.arquivo_original

        if (not file_path or file_path == '-') and filename:
            settings = get_settings()
            potential_path = settings.unidentified_path / filename
            if potential_path.exists():
                file_path = str(potential_path)

        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Arquivo fisico nao encontrado")

        return FileResponse(
            path=file_path,
            filename=filename or "documento.pdf",
            media_type="application/pdf",
            content_disposition_type="inline",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao visualizar arquivo de extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/extratos/logs")
async def clear_extratos_logs():
    """Limpa logs de extratos baixados e remove arquivos quando possivel."""
    try:
        from app.database import SessionLocal
        from app.models.extratos_baixados_log import ExtratosBaixadosLog

        qtde_memory = len(_extratos_jobs)
        _extratos_jobs.clear()
        _extratos_processed_hashes.clear()

        db = SessionLocal()
        try:
            logs = db.query(ExtratosBaixadosLog).all()
            count_db = len(logs)

            for log in logs:
                if log.arquivo_salvo:
                    try:
                        path = Path(log.arquivo_salvo)
                        if path.exists():
                            path.unlink()
                    except Exception:
                        pass

            db.query(ExtratosBaixadosLog).delete()
            db.commit()
        finally:
            db.close()

        await get_extratos_event_manager().emit_stats()

        return {
            "success": True,
            "message": f"Limpeza completa realizada. Banco: {count_db} registros, Memoria: {qtde_memory} jobs.",
        }
    except Exception as e:
        logger.error(f"Erro ao limpar logs de extratos baixados: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Erro interno ao limpar logs: {str(e)}"}
        )

@app.get("/extratos/monitor/test/logs")
async def get_extratos_test_logs(limit: int = 30, offset: int = 0):
    """Consulta logs de teste de extratos baixados."""
    db_teste_service = get_extratos_baixados_log_teste_service()
    logs = db_teste_service.get_logs_teste(limit=limit, offset=offset)
    return {
        "modo": "TESTE",
        "total": len(logs),
        "logs": [log.to_dict() for log in logs],
    }

@app.get("/extratos/monitor/test/stats")
async def get_extratos_test_stats():
    """Estatisticas dos logs de teste de extratos baixados."""
    db_teste_service = get_extratos_baixados_log_teste_service()
    return db_teste_service.get_stats_teste()

@app.delete("/extratos/monitor/test/logs")
async def clear_extratos_test_logs():
    """Limpa logs de teste de extratos baixados."""
    db_teste_service = get_extratos_baixados_log_teste_service()
    count = db_teste_service.limpar_logs_teste()
    return {"message": f"{count} logs de teste removidos", "count": count}
@app.get("/test", response_class=HTMLResponse)
async def test_monitor_page():
    """Página de monitoramento de TESTES."""
    from pathlib import Path
    
    html_path = Path(__file__).parent / "templates" / "test_monitor.html"
    return HTMLResponse(content=_render_template_with_navbar(html_path, active_main="test", show_main=True, show_extratos=True))

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

@app.post("/make/webhook/test")
async def make_webhook_test(
    file: Annotated[UploadFile, File(description="Arquivo PDF ou ZIP para teste (MAKE Test)")],
    background_tasks: BackgroundTasks,
):
    """
    Webhook específico do Módulo MAKE para a view Test.
    Mesmo fluxo do /test/upload, mas com rota dedicada.
    """
    return await test_upload_file(file=file, background_tasks=background_tasks)


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
        extraction = llm_service.extract_info_with_fallback(text, pdf_content)
        
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
                # Usa a conta extraída do extrato (prioritário) ou a conta da planilha
                conta = storage_service._select_account(extraction.banco, extraction.conta, match_result.cliente.conta)
                target_path = storage_service._build_path_structure(
                    client_base_path,
                    ano,
                    mes,
                    extraction.banco,
                    conta
                )

                # Constrói o nome do arquivo usando a mesma lógica do storage_service
                file_name = storage_service._build_filename(
                    extraction.banco,
                    extraction.tipo_documento,
                    pdf_content,
                    target_path,
                    filename
                )
                simulated_path = str(target_path / file_name)
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



class UpdateBatchRequest(BaseModel):
    ids: list[int]
    updates: dict

class MakeReversaoWebhook(BaseModel):
    ids: list[int]
    deletar_arquivos: bool = True
class ExtratosReversaoWebhook(BaseModel):
    ids: list[int]
    deletar_arquivos: bool = True

@app.patch("/logs/update-batch")
async def update_batch_logs(payload: UpdateBatchRequest):
    """
    Atualiza múltiplos logs com os valores fornecidos.
    Útil para corrigir dados como banco, cliente, etc. em massa.
    """
    try:
        db_log_service = get_db_log_service()
        count = db_log_service.update_batch(payload.ids, payload.updates)
        
        # Emite evento para atualizar o monitor (força reload para todos)
        event_manager.emit_stats()
        
        return {
            "success": True, 
            "message": f"{count} registros atualizados com sucesso.",
            "updated_count": count
        }
    except Exception as e:
        logger.error(f"Erro ao atualizar logs em lote: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ====== ENDPOINTS DE REVERSÃO ======


@app.get("/extratos/reversao", response_class=HTMLResponse)
async def extratos_reversao_page():
    """Pagina de gestao de reversoes de extratos baixados."""
    from pathlib import Path

    html_path = Path(__file__).parent / "templates" / "extratos_reversao.html"
    return HTMLResponse(content=_render_template_with_navbar(html_path, active_extratos="reversao-extratos", show_main=True, show_extratos=True))
@app.get("/reversao", response_class=HTMLResponse)
async def reversao_page():
    """Página de gestão de reversões."""
    from pathlib import Path
    
    html_path = Path(__file__).parent / "templates" / "reversao.html"
    return HTMLResponse(content=_render_template_with_navbar(html_path, active_main="reversao", show_main=True, show_extratos=True))



@app.get("/extratos/reversao/listar")
async def listar_extratos_para_reversao(
    limit: int = 100,
    offset: int = 0,
    status: str = None,
    cliente: str = None,
    apenas_existentes: bool = False,
):
    """Lista processamentos de extratos baixados que podem ser revertidos."""
    try:
        reversao_service = get_extratos_baixados_reversao_service()
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
        logger.error(f"Erro ao listar reversoes de extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/extratos/reversao/stats")
async def stats_extratos_reversao():
    """Estatisticas para pagina de reversao de extratos baixados."""
    try:
        reversao_service = get_extratos_baixados_reversao_service()
        return reversao_service.get_estatisticas()
    except Exception as e:
        logger.error(f"Erro ao obter estatisticas de reversao extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/extratos/reversao/historico")
async def historico_extratos_reversoes(
    limit: int = 100,
    offset: int = 0,
    cliente: str = None,
    banco: str = None,
    tipo: str = None,
    arquivo_deletado: str = None,
):
    """Lista historico de reversoes de extratos baixados."""
    try:
        from app.database import SessionLocal
        from app.models.extratos_baixados_reversao_log import ExtratosBaixadosReversaoLog

        db = SessionLocal()
        try:
            query = db.query(ExtratosBaixadosReversaoLog)
            if cliente:
                query = query.filter(ExtratosBaixadosReversaoLog.cliente_nome.ilike(f"%{cliente}%"))
            if banco:
                query = query.filter(ExtratosBaixadosReversaoLog.banco.ilike(f"%{banco}%"))
            if tipo:
                query = query.filter(ExtratosBaixadosReversaoLog.tipo_reversao.ilike(f"%{tipo}%"))
            if arquivo_deletado in {"true", "false"}:
                query = query.filter(
                    ExtratosBaixadosReversaoLog.arquivo_deletado == (arquivo_deletado == "true")
                )

            query = query.order_by(ExtratosBaixadosReversaoLog.id.desc())
            query = query.limit(limit).offset(offset)
            reversoes = query.all()
        finally:
            db.close()

        return {
            "total": len(reversoes),
            "offset": offset,
            "limit": limit,
            "reversoes": [r.to_dict() for r in reversoes],
        }
    except Exception as e:
        logger.error(f"Erro ao listar historico de reversoes extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/extratos/reversao/historico/stats")
async def stats_extratos_historico_reversoes():
    """Estatisticas do historico de reversoes de extratos baixados."""
    try:
        from app.database import SessionLocal
        from app.models.extratos_baixados_reversao_log import ExtratosBaixadosReversaoLog

        db = SessionLocal()
        try:
            total = db.query(ExtratosBaixadosReversaoLog).count()
            arquivos_deletados = db.query(ExtratosBaixadosReversaoLog).filter(
                ExtratosBaixadosReversaoLog.arquivo_deletado == True
            ).count()
            ultima = db.query(ExtratosBaixadosReversaoLog).order_by(ExtratosBaixadosReversaoLog.id.desc()).first()
        finally:
            db.close()

        return {
            "total": total,
            "arquivos_deletados": arquivos_deletados,
            "arquivos_nao_deletados": max(0, total - arquivos_deletados),
            "ultima_reversao": ultima.revertido_em.isoformat() if ultima and ultima.revertido_em else None,
        }
    except Exception as e:
        logger.error(f"Erro ao obter estatisticas de historico extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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



def _remove_logs_from_memory(logs):
    """Reflete a remoção no cache em memória (_jobs) para evitar inconsistência."""
    if not logs:
        return
        
    hashes_to_remove = {log.hash_arquivo for log in logs if log.hash_arquivo}
    names_to_remove = {log.arquivo_original for log in logs if log.arquivo_original}
    
    # Percorre todos os jobs em memória
    for job_key in list(_jobs.keys()):
        job = _jobs[job_key]
        if not job.get("results") or "resultados" not in job["results"]:
            continue
            
        # Filtra os resultados mantendo apenas os que NÃO foram removidos
        original_results = job["results"]["resultados"]
        new_results = []
        changed = False
        
        for res in original_results:
            h = res.get("hash_arquivo")
            n = res.get("nome_arquivo_original")
            
            # Se hash bater ou nome bater, ignora (foi removido)
            if (h and h in hashes_to_remove) or (n and n in names_to_remove):
                changed = True
                continue
            new_results.append(res)
            
        if changed:
            job["results"]["resultados"] = new_results
            # Se ficou vazio, poderiamos remover o job, mas talvez seja melhor manter o histórico de que houve um job
            # Mas vamos atualizar os contadores do job para refletir
            res_list = job["results"].get("resultados", [])
            job["results"]["arquivos_sucesso"] = sum(1 for r in res_list if r.get("status") == "SUCESSO")
            job["results"]["arquivos_falha"] = sum(1 for r in res_list if r.get("status") == "FALHA")
            job["results"]["arquivos_nao_identificados"] = sum(1 for r in res_list if r.get("status") == "NAO_IDENTIFICADO")
            job["results"]["total_arquivos"] = len(res_list)


def _remove_extratos_logs_from_memory(logs):
    """Reflete a remocao no cache de extratos baixados para evitar inconsistencia."""
    if not logs:
        return

    hashes_to_remove = {log.hash_arquivo for log in logs if log.hash_arquivo}
    names_to_remove = {log.arquivo_original for log in logs if log.arquivo_original}

    for job_key in list(_extratos_jobs.keys()):
        job = _extratos_jobs[job_key]
        if not job.get("results") or "resultados" not in job["results"]:
            continue

        original_results = job["results"]["resultados"]
        new_results = []
        changed = False

        for res in original_results:
            h = res.get("hash_arquivo")
            n = res.get("nome_arquivo_original")
            if (h and h in hashes_to_remove) or (n and n in names_to_remove):
                changed = True
                continue
            new_results.append(res)

        if changed:
            job["results"]["resultados"] = new_results
            res_list = job["results"].get("resultados", [])
            job["results"]["arquivos_sucesso"] = sum(1 for r in res_list if r.get("status") == "SUCESSO")
            job["results"]["arquivos_falha"] = sum(1 for r in res_list if r.get("status") == "FALHA")
            job["results"]["arquivos_nao_identificados"] = sum(
                1 for r in res_list if r.get("status") == "NAO_IDENTIFICADO"
            )
            job["results"]["total_arquivos"] = len(res_list)


@app.delete("/reversao/{log_id}")
async def reverter_por_id(log_id: int, deletar_arquivo: bool = True):
    """Reverte um único processamento pelo ID."""
    try:
        from app.database import SessionLocal
        from app.models.extrato_log import ExtratoLog
        
        db = SessionLocal()
        status_original = None
        log_obj = None
        
        try:
            log = db.query(ExtratoLog).filter(ExtratoLog.id == log_id).first()
            if log:
                status_original = log.status
                # Cria objeto leve para passar para limpeza de memória
                # Precisamos copiar dados pois o objeto será deletado ou detatched
                from types import SimpleNamespace
                log_obj = SimpleNamespace(
                    hash_arquivo=log.hash_arquivo, 
                    arquivo_original=log.arquivo_original
                )
        finally:
            db.close()
            
        reversao_service = get_reversao_service()
        resultado = reversao_service.reverter_por_id(log_id, deletar_arquivo)
        
        if not resultado["success"]:
            raise HTTPException(status_code=400, detail=resultado["message"])
        
        # Limpa da memória global _jobs
        if log_obj:
            _remove_logs_from_memory([log_obj])

        # Atualiza estatísticas globais se tivermos o status
        if status_original:
            event_manager = get_event_manager()
            event_manager.decrement_stats(
                sucesso=1 if status_original == 'SUCESSO' else 0,
                nao_identificado=1 if status_original == 'NAO_IDENTIFICADO' else 0,
                falha=1 if status_original == 'FALHA' else 0
            )
            await event_manager.emit_stats()
        
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reverter: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@app.delete("/extratos/reversao/{log_id}")
async def reverter_extrato_baixado(log_id: int, deletar_arquivo: bool = True):
    """Reverte um extrato baixado pelo ID."""
    try:
        from app.database import SessionLocal
        from app.models.extratos_baixados_log import ExtratosBaixadosLog
        from types import SimpleNamespace

        db = SessionLocal()
        status_original = None
        log_obj = None

        try:
            log = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.id == log_id).first()
            if log:
                status_original = log.status
                log_obj = SimpleNamespace(
                    hash_arquivo=log.hash_arquivo,
                    arquivo_original=log.arquivo_original,
                )
        finally:
            db.close()

        reversao_service = get_extratos_baixados_reversao_service()
        resultado = reversao_service.reverter_por_id(log_id, deletar_arquivo)
        if not resultado.get("success"):
            raise HTTPException(status_code=400, detail=resultado.get("message"))

        if log_obj:
            _remove_extratos_logs_from_memory([log_obj])

        if status_original:
            event_manager = get_extratos_event_manager()
            event_manager.decrement_stats(
                sucesso=1 if status_original == "SUCESSO" else 0,
                nao_identificado=1 if status_original == "NAO_IDENTIFICADO" else 0,
                falha=1 if status_original == "FALHA" else 0,
            )
            await event_manager.emit_stats()

        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reverter extrato baixado: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/extratos/reversao/lote")
async def reverter_extrato_baixado_lote(ids: List[int], deletar_arquivos: bool = True):
    """Reverte multiplos extratos baixados."""
    try:
        from app.database import SessionLocal
        from app.models.extratos_baixados_log import ExtratosBaixadosLog
        from types import SimpleNamespace

        db = SessionLocal()
        stats_diff = {"SUCESSO": 0, "NAO_IDENTIFICADO": 0, "FALHA": 0}
        logs_to_clean = []

        try:
            logs = db.query(ExtratosBaixadosLog).filter(ExtratosBaixadosLog.id.in_(ids)).all()
            for log in logs:
                stats_diff[log.status] = stats_diff.get(log.status, 0) + 1
                logs_to_clean.append(SimpleNamespace(hash_arquivo=log.hash_arquivo, arquivo_original=log.arquivo_original))
        finally:
            db.close()

        reversao_service = get_extratos_baixados_reversao_service()
        resultado = reversao_service.reverter_lote(ids, deletar_arquivos)

        if logs_to_clean:
            _remove_extratos_logs_from_memory(logs_to_clean)

        event_manager = get_extratos_event_manager()
        event_manager.decrement_stats(
            sucesso=stats_diff.get("SUCESSO", 0),
            nao_identificado=stats_diff.get("NAO_IDENTIFICADO", 0),
            falha=stats_diff.get("FALHA", 0),
        )
        await event_manager.emit_stats()

        return resultado
    except Exception as e:
        logger.error(f"Erro ao reverter extratos baixados em lote: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/extratos/webhook/reversao")
async def extratos_webhook_reversao(payload: ExtratosReversaoWebhook):
    """
    Webhook específico da view Reversão de Extratos.
    Mesmo fluxo do /extratos/reversao/lote, mas com rota dedicada.
    """
    return await reverter_extrato_baixado_lote(
        ids=payload.ids,
        deletar_arquivos=payload.deletar_arquivos,
    )

@app.post("/extratos/reversao/ultimos/{quantidade}")
async def reverter_extratos_ultimos(quantidade: int, deletar_arquivos: bool = True):
    """Reverte os ultimos N extratos baixados."""
    try:
        reversao_service = get_extratos_baixados_reversao_service()
        return reversao_service.reverter_ultimos(quantidade, deletar_arquivos)
    except Exception as e:
        logger.error(f"Erro ao reverter ultimos extratos baixados: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/reversao/lote")
async def reverter_lote(ids: List[int], deletar_arquivos: bool = True):
    """Reverte múltiplos processamentos."""
    try:
        from app.database import SessionLocal
        from app.models.extrato_log import ExtratoLog
        from sqlalchemy import func
        from types import SimpleNamespace
        
        db = SessionLocal()
        stats_diff = {"SUCESSO": 0, "NAO_IDENTIFICADO": 0, "FALHA": 0}
        logs_to_clean = []
        
        try:
            # Busca logs completos para limpeza de memória
            logs = db.query(ExtratoLog).filter(ExtratoLog.id.in_(ids)).all()
            for log in logs:
                if log.status in stats_diff:
                    stats_diff[log.status] += 1
                logs_to_clean.append(SimpleNamespace(
                    hash_arquivo=log.hash_arquivo, 
                    arquivo_original=log.arquivo_original
                ))
        finally:
            db.close()
            
        reversao_service = get_reversao_service()
        resultado = reversao_service.reverter_lote(ids, deletar_arquivos)
        
        if resultado.get("success"):
            # Limpa memória
            _remove_logs_from_memory(logs_to_clean)
            
            # Atualiza stats
            event_manager = get_event_manager()
            event_manager.decrement_stats(
                sucesso=stats_diff["SUCESSO"],
                nao_identificado=stats_diff["NAO_IDENTIFICADO"],
                falha=stats_diff["FALHA"]
            )
            await event_manager.emit_stats()
            
        return resultado
    except Exception as e:
        logger.error(f"Erro ao reverter lote: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/make/webhook/reversao")
async def make_webhook_reversao(payload: MakeReversaoWebhook):
    """
    Webhook específico do Módulo MAKE para a view Reversão.
    Mesmo fluxo do /reversao/lote, mas com rota dedicada.
    """
    return await reverter_lote(ids=payload.ids, deletar_arquivos=payload.deletar_arquivos)


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
