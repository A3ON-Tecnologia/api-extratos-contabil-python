"""
Sistema de eventos para acompanhamento em tempo real.

Gerencia conexões WebSocket e broadcast de eventos de processamento.
"""

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Any
from dataclasses import dataclass, field, asdict
import json

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Tipos de eventos do sistema."""
    
    # Conexão
    CONNECTED = "connected"
    
    # Upload
    FILE_RECEIVED = "file_received"
    ZIP_EXTRACTING = "zip_extracting"
    ZIP_EXTRACTED = "zip_extracted"
    
    # Processamento de PDF
    PDF_PROCESSING_START = "pdf_processing_start"
    PDF_TEXT_EXTRACTING = "pdf_text_extracting"
    PDF_TEXT_EXTRACTED = "pdf_text_extracted"
    
    # Modo teste - eventos simplificados
    PROCESSING_STARTED = "processing_started"
    EXTRACTING_TEXT = "extracting_text"
    ANALYZING = "analyzing"
    MATCHING = "matching"
    SAVING = "saving"
    
    # LLM
    LLM_ANALYZING = "llm_analyzing"
    LLM_COMPLETED = "llm_completed"
    
    # Matching
    MATCHING_START = "matching_start"
    MATCHING_COMPLETED = "matching_completed"
    
    # Storage
    FILE_SAVING = "file_saving"
    FILE_SAVED = "file_saved"
    
    # Log
    LOG_WRITING = "log_writing"
    LOG_WRITTEN = "log_written"
    
    # Resultado final
    PROCESSING_COMPLETED = "processing_completed"
    PROCESSING_ERROR = "processing_error"
    
    # Estatísticas
    STATS_UPDATE = "stats_update"


@dataclass
class ProcessingEvent:
    """Evento de processamento."""
    
    event_type: EventType
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    filename: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    progress: int | None = None  # 0-100
    
    def to_dict(self) -> dict:
        """Converte para dicionário."""
        data = asdict(self)
        data["event_type"] = self.event_type.value
        return data
    
    def to_json(self) -> str:
        """Converte para JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EventManager:
    """Gerenciador de eventos e conexões WebSocket."""
    
    def __init__(self):
        """Inicializa o gerenciador."""
        self.connections: list[WebSocket] = []
        self.event_history: list[ProcessingEvent] = []
        self.max_history = 100  # Mantém últimos 100 eventos
        
        # Estatísticas
        self.stats = {
            "total_processados": 0,
            "sucesso": 0,
            "nao_identificados": 0,
            "falhas": 0,
            "em_processamento": 0,
        }
    
    async def connect(self, websocket: WebSocket):
        """
        Aceita uma nova conexão WebSocket.
        
        Envia histórico recente e estatísticas atuais.
        """
        await websocket.accept()
        self.connections.append(websocket)
        
        logger.info(f"Nova conexão WebSocket. Total: {len(self.connections)}")
        
        # Envia evento de conexão
        await self._send_to_socket(websocket, ProcessingEvent(
            event_type=EventType.CONNECTED,
            message="Conectado ao sistema de monitoramento",
            details={"stats": self.stats}
        ))
        
        # Envia histórico recente
        for event in self.event_history[-20:]:  # Últimos 20 eventos
            await self._send_to_socket(websocket, event)
    
    def disconnect(self, websocket: WebSocket):
        """Remove uma conexão WebSocket."""
        if websocket in self.connections:
            self.connections.remove(websocket)
            logger.info(f"Conexão WebSocket fechada. Total: {len(self.connections)}")
    
    async def emit(self, event: ProcessingEvent):
        """
        Emite um evento para todas as conexões.
        
        Também armazena no histórico.
        """
        # Adiciona ao histórico
        self.event_history.append(event)
        if len(self.event_history) > self.max_history:
            self.event_history = self.event_history[-self.max_history:]
        
        # Broadcast para todas as conexões
        disconnected = []
        for websocket in self.connections:
            try:
                await self._send_to_socket(websocket, event)
            except Exception as e:
                logger.warning(f"Erro ao enviar evento: {e}")
                disconnected.append(websocket)
        
        # Remove conexões mortas
        for ws in disconnected:
            self.disconnect(ws)
    
    async def _send_to_socket(self, websocket: WebSocket, event: ProcessingEvent):
        """Envia evento para um socket específico."""
        await websocket.send_text(event.to_json())
    
    
    def update_stats(self, sucesso: bool = False, nao_identificado: bool = False, falha: bool = False):
        """Atualiza estatísticas."""
        self.stats["total_processados"] += 1
        if sucesso:
            self.stats["sucesso"] += 1
        if nao_identificado:
            self.stats["nao_identificados"] += 1
        if falha:
            self.stats["falhas"] += 1

    def decrement_stats(self, sucesso: int = 0, nao_identificado: int = 0, falha: int = 0):
        """Decrementa estatísticas (usado na reversão)."""
        total_revertido = sucesso + nao_identificado + falha
        self.stats["total_processados"] = max(0, self.stats["total_processados"] - total_revertido)
        self.stats["sucesso"] = max(0, self.stats["sucesso"] - sucesso)
        self.stats["nao_identificados"] = max(0, self.stats["nao_identificados"] - nao_identificado)
        self.stats["falhas"] = max(0, self.stats["falhas"] - falha)
    
    def start_processing(self):
        """Marca início de processamento."""
        self.stats["em_processamento"] += 1
    
    def end_processing(self):
        """Marca fim de processamento."""
        self.stats["em_processamento"] = max(0, self.stats["em_processamento"] - 1)
    
    async def emit_stats(self):
        """Emite evento de atualização de estatísticas."""
        await self.emit(ProcessingEvent(
            event_type=EventType.STATS_UPDATE,
            message="Estatísticas atualizadas",
            details={"stats": self.stats}
        ))


# Instância global do gerenciador de eventos (PRODUÇÃO)
event_manager = EventManager()

# Instância global do gerenciador de eventos (TESTE)
test_event_manager = EventManager()

# Instancia global do gerenciador de eventos (EXTRATOS BAIXADOS - PRODUCAO)
extratos_event_manager = EventManager()

# Instancia global do gerenciador de eventos (EXTRATOS BAIXADOS - TESTE)
extratos_test_event_manager = EventManager()


def get_event_manager() -> EventManager:
    """Retorna a instância global do gerenciador de eventos de PRODUÇÃO."""
    return event_manager


def get_test_event_manager() -> EventManager:
    """Retorna a instância global do gerenciador de eventos de TESTE."""
    return test_event_manager



def get_extratos_event_manager() -> EventManager:
    """Retorna a instancia global do gerenciador de eventos de extratos baixados (PRODUCAO)."""
    return extratos_event_manager

def get_extratos_test_event_manager() -> EventManager:
    """Retorna a instancia global do gerenciador de eventos de extratos baixados (TESTE)."""
    return extratos_test_event_manager
