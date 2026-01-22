"""
Rotas de TESTE para processamento de extratos.

Simula todo o fluxo sem salvar arquivos no disco.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.extratos_service import ExtratosService
from app.services.llm_service import LLMService
from app.services.pdf_service import PDFService
from app.services.db_log_teste_service import get_db_log_teste_service
from app.utils.hash import compute_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extratos/test", tags=["Extratos - Teste"])

# Armazenamento de jobs de teste
_test_jobs: dict[str, dict] = {}


@router.post("/processar")
async def processar_extrato_teste(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None
):
    """
    MODO TESTE: Processa um extrato simulando todo o fluxo.

    - Lê PDF da pasta configurada ou recebe via upload
    - Extrai informações com LLM
    - Faz matching na planilha RELAÇÃO EXTRATOS
    - SIMULA onde o arquivo seria salvo (não salva de verdade)
    - Registra no banco de testes para permitir reversão

    Returns:
        Job ID para acompanhamento
    """
    content = await file.read()
    filename = file.filename or "teste.pdf"
    file_hash = compute_hash(content)

    # Cria job ID
    job_id = f"test_{uuid.uuid4().hex[:12]}"

    # Inicializa job
    _test_jobs[job_id] = {
        "job_id": job_id,
        "filename": filename,
        "status": "processing",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "result": None,
        "error": None
    }

    logger.info(f"[TESTE] Job {job_id} criado para {filename}")

    # Processa em background
    if background_tasks:
        background_tasks.add_task(_process_test_job, job_id, content, filename, file_hash)
    else:
        asyncio.create_task(_process_test_job(job_id, content, filename, file_hash))

    return {
        "mode": "TESTE",
        "job_id": job_id,
        "filename": filename,
        "message": "Processamento iniciado em background (modo teste)",
        "status_url": f"/extratos/test/job/{job_id}"
    }


@router.get("/job/{job_id}")
async def get_test_job(job_id: str):
    """
    Verifica status de um job de teste.

    Args:
        job_id: ID do job

    Returns:
        Status e resultado do processamento
    """
    if job_id not in _test_jobs:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    return _test_jobs[job_id]


@router.get("/jobs")
async def list_test_jobs():
    """Lista todos os jobs de teste."""
    jobs_list = list(_test_jobs.values())
    jobs_list.sort(key=lambda x: x["created_at"], reverse=True)

    return {
        "mode": "TESTE",
        "total": len(jobs_list),
        "jobs": jobs_list[:50]  # Últimos 50
    }


@router.post("/processar-pasta")
async def processar_pasta_teste():
    """
    MODO TESTE: Processa todos os PDFs da pasta WATCH_FOLDER_PATH.

    Processa todos os arquivos PDF encontrados na pasta configurada,
    simulando onde seriam salvos sem salvar de verdade.

    Returns:
        Lista de resultados
    """
    settings = get_settings()
    watch_path = settings.watch_folder_path

    if not watch_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Pasta não encontrada: {watch_path}"
        )

    # Lista todos os PDFs
    pdf_files = list(watch_path.glob("*.pdf"))

    if not pdf_files:
        return {
            "mode": "TESTE",
            "message": "Nenhum PDF encontrado na pasta",
            "path": str(watch_path),
            "total": 0,
            "files": []
        }

    results = []

    for pdf_path in pdf_files:
        try:
            content = pdf_path.read_bytes()
            file_hash = compute_hash(content)

            # Processa síncrono para ter resultado imediato
            result = await _process_test_extrato(content, pdf_path.name, file_hash)
            results.append(result)

        except Exception as e:
            logger.error(f"[TESTE] Erro ao processar {pdf_path.name}: {e}")
            results.append({
                "filename": pdf_path.name,
                "status": "ERRO",
                "error": str(e)
            })

    return {
        "mode": "TESTE",
        "total_files": len(pdf_files),
        "processed": len(results),
        "results": results
    }


@router.get("/logs")
async def get_test_logs(limit: int = 100):
    """
    Retorna logs de testes.

    Args:
        limit: Quantidade máxima de registros

    Returns:
        Logs de processamento de teste
    """
    db_teste_service = get_db_log_teste_service()
    logs = db_teste_service.get_logs_teste(limit=limit)

    return {
        "mode": "TESTE",
        "total": len(logs),
        "logs": [log.to_dict() for log in logs]
    }


@router.delete("/logs")
async def clear_test_logs():
    """
    Limpa todos os logs de teste (reversão completa).

    ATENÇÃO: Esta ação remove TODOS os registros de teste!

    Returns:
        Quantidade de registros removidos
    """
    db_teste_service = get_db_log_teste_service()
    count = db_teste_service.limpar_logs_teste()

    return {
        "mode": "TESTE",
        "message": f"{count} logs de teste removidos",
        "count": count
    }


@router.delete("/log/{log_id}")
async def reverter_test_log(log_id: int):
    """
    Reverte um único log de teste.

    Args:
        log_id: ID do log no banco de dados

    Returns:
        Resultado da reversão
    """
    db_teste_service = get_db_log_teste_service()

    try:
        db_teste_service.delete_log_teste(log_id)
        return {
            "mode": "TESTE",
            "success": True,
            "message": f"Log {log_id} removido com sucesso"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao reverter log: {str(e)}"
        )


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

async def _process_test_job(job_id: str, content: bytes, filename: str, file_hash: str):
    """Processa um job de teste em background."""
    try:
        result = await _process_test_extrato(content, filename, file_hash)

        _test_jobs[job_id].update({
            "status": "completed",
            "completed_at": datetime.now().isoformat(),
            "result": result,
            "error": None
        })

        logger.info(f"[TESTE] Job {job_id} concluído com sucesso")

    except Exception as e:
        logger.error(f"[TESTE] Erro no job {job_id}: {e}")

        _test_jobs[job_id].update({
            "status": "error",
            "completed_at": datetime.now().isoformat(),
            "result": None,
            "error": str(e)
        })


async def _process_test_extrato(content: bytes, filename: str, file_hash: str) -> dict:
    """
    Processa um extrato em modo teste.

    Args:
        content: Conteúdo do PDF
        filename: Nome do arquivo
        file_hash: Hash do arquivo

    Returns:
        Dicionário com resultado do processamento
    """
    settings = get_settings()

    # 1. Extrai texto do PDF
    logger.info(f"[TESTE] Extraindo texto de {filename}...")
    pdf_service = PDFService()
    text = pdf_service.extract_text(content, filename)

    # 2. Analisa com LLM
    logger.info(f"[TESTE] Analisando com LLM...")
    llm_service = LLMService()
    extraction = llm_service.extract_info_with_fallback(text)

    # 3. Busca na planilha RELAÇÃO EXTRATOS
    logger.info(f"[TESTE] Buscando na planilha RELAÇÃO EXTRATOS...")
    extratos_service = ExtratosService()

    cliente_info = extratos_service.find_cliente_by_info(
        cnpj=extraction.cnpj,
        nome=extraction.cliente_sugerido,
        banco=extraction.banco,
        conta=extraction.conta,
        agencia=extraction.agencia
    )

    # 4. Determina caminho simulado
    if cliente_info:
        # Cliente encontrado - simula onde salvaria
        pasta_cliente = cliente_info.get('pasta', 'DESCONHECIDA')
        base_path = settings.base_path

        # Simula estrutura: BASE_PATH / PASTA_CLIENTE / ANO / MES
        from datetime import datetime
        hoje = datetime.now()
        ano = hoje.year
        mes = hoje.month

        # Caminho simulado
        caminho_simulado = str(base_path / pasta_cliente / str(ano) / f"{mes:02d}" / f"{extraction.tipo_documento}_{extraction.banco}.pdf")

        status = "SUCESSO"
        cliente_nome = cliente_info.get('nome')
        metodo = cliente_info.get('metodo', 'DESCONHECIDO')

    else:
        # Cliente não encontrado
        caminho_simulado = str(settings.unidentified_path / filename)
        status = "NAO_IDENTIFICADO"
        cliente_nome = None
        metodo = "NENHUM"

    # 5. Salva no banco de TESTES
    db_teste_service = get_db_log_teste_service()
    log_entry = db_teste_service.log_extrato_teste(
        arquivo_original=filename,
        status=status,
        arquivo_salvo=caminho_simulado,
        hash_arquivo=file_hash,
        cliente_nome=cliente_nome,
        cliente_cod=cliente_info.get('cod') if cliente_info else None,
        cliente_cnpj=extraction.cnpj,
        banco=extraction.banco,
        tipo_documento=extraction.tipo_documento,
        agencia=extraction.agencia,
        conta=extraction.conta,
        ano=ano,
        mes=mes,
        metodo_identificacao=metodo,
        confianca_ia=extraction.confianca,
        erro=None if cliente_info else "Cliente não encontrado na planilha"
    )

    logger.info(f"[TESTE] Processamento concluído: {filename} -> {status}")

    return {
        "filename": filename,
        "status": status,
        "cliente": cliente_nome,
        "banco": extraction.banco,
        "tipo_documento": extraction.tipo_documento,
        "cnpj": extraction.cnpj,
        "conta": extraction.conta,
        "agencia": extraction.agencia,
        "caminho_simulado": caminho_simulado,
        "metodo_identificacao": metodo,
        "confianca": extraction.confianca,
        "log_id": log_entry.id,
        "message": "Arquivo processado em modo TESTE (não foi salvo)"
    }
