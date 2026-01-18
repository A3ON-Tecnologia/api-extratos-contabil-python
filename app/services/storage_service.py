"""
Serviço de armazenamento de arquivos.

Responsável por salvar os PDFs no caminho correto da estrutura
de pastas em rede Windows.
"""

import logging
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.schemas.client import ClientInfo, MatchResult
from app.utils.hash import short_hash

logger = logging.getLogger(__name__)

# Mapeamento de número do mês para nome da pasta
MONTH_NAMES = {
    1: "JANEIRO",
    2: "FEVEREIRO",
    3: "MARÇO",
    4: "ABRIL",
    5: "MAIO",
    6: "JUNHO",
    7: "JULHO",
    8: "AGOSTO",
    9: "SETEMBRO",
    10: "OUTUBRO",
    11: "NOVEMBRO",
    12: "DEZEMBRO",
}


class StorageService:
    """Serviço de armazenamento de arquivos."""
    
    def __init__(self):
        """Inicializa o serviço."""
        self.settings = get_settings()
    
    def save_file(
        self,
        pdf_data: bytes,
        match_result: MatchResult,
        ano: int | None,
        mes: int | None,
        original_filename: str,
        tipo_documento: str | None = None,
        banco: str | None = None,
    ) -> str:
        """
        Salva o arquivo PDF no caminho correto.
        """
        # Usa ano/mês atual se não identificado
        now = datetime.now()
        ano = ano or now.year
        mes = mes or now.month
        
        target_path = None
        
        if match_result.identificado:
            # Validação Temporal: Apenas 12/2025 em diante
            is_date_valid = False
            
            if ano and mes:
                if ano > 2025:
                    is_date_valid = True
                elif ano == 2025 and mes >= 12:
                    is_date_valid = True
            
            if is_date_valid:
                # Tenta resolver o caminho do cliente validando existência
                client_base_path = self._resolve_client_path(match_result.cliente)
                
                if client_base_path:
                    target_path = self._build_path_structure(client_base_path, ano, mes)
                    filename = self._build_filename(
                        banco,
                        tipo_documento,
                        pdf_data,
                        target_path,
                        original_filename
                    )
                else:
                    logger.warning(
                        f"Estrutura de pastas não encontrada para cliente {match_result.cliente.cod}. "
                        "Salvando em NAO_IDENTIFICADOS."
                    )
            else:
                logger.warning(
                    f"Documento identificado ({match_result.cliente.cod}) mas com data anterior ao permitido ({mes}/{ano}). "
                    "Salvando em NAO_IDENTIFICADOS."
                )
        
        # Fallback se não identificado, path inválido ou data antiga
        if not target_path:
            target_path = self._build_unidentified_path(ano, mes)
            path_filename = self._ensure_unique_filename(
                original_filename,
                pdf_data,
                target_path
            )
            filename = path_filename
        
        # Cria apenas subdiretórios (Ano/Mês), nunca a raiz doi cliente
        target_path.mkdir(parents=True, exist_ok=True)
        
        # Salva o arquivo
        full_path = target_path / filename
        full_path.write_bytes(pdf_data)
        
        logger.info(f"Arquivo salvo: {full_path}")
        return str(full_path)

    def _resolve_client_path(self, client: ClientInfo) -> Path | None:
        """
        Resolve o caminho base do cliente, verificando se existe.
        
        Tenta encontrar a pasta do cliente no formato "COD - NOME".
        
        Returns:
            Path da pasta do cliente ou None se não encontrada
        """
        # Formato padrão: "098 - NOME DO CLIENTE"
        folder_name = f"{client.cod} - {client.nome}"
        client_path = self.settings.base_path / folder_name
        
        if client_path.exists():
            return client_path
        
        # Tenta buscar por código se o nome exato não bater
        pattern = f"{client.cod} - *"
        matches = list(self.settings.base_path.glob(pattern))
        
        if matches:
            return matches[0]
        
        return None
    
    def _build_path_structure(self, client_base_path: Path, ano: int, mes: int) -> Path:
        """
        Constrói a estrutura de pastas dentro da pasta do cliente.
        
        Estrutura: cliente/Departamento Contábil/ANO/MÊS
        Ex: cliente/Departamento Contábil/2025/12
        """
        # Mês como número com 2 dígitos (01, 02, ..., 12)
        mes_str = str(mes).zfill(2)
        return client_base_path / "Departamento Contábil" / str(ano) / mes_str
    
    def _build_unidentified_path(self, ano: int, mes: int) -> Path:
        """
        Constrói o caminho para arquivos não identificados.
        
        Estrutura: NAO_IDENTIFICADOS/ANO/MÊS
        """
        mes_nome = MONTH_NAMES.get(mes, f"MES_{mes}")
        return self.settings.unidentified_path / str(ano) / mes_nome

    
    def _build_filename(
        self,
        banco: str | None,
        tipo_documento: str | None,
        pdf_data: bytes,
        target_path: Path,
        original_filename: str = "",
    ) -> str:
        """
        Constrói o nome do arquivo no formato padrão.
        
        Formato: TIPOEXTRATO_BANCO.ext
        Ex: CC_SICREDI.pdf
        """
        # Tipo do extrato (padrão DOC se não informado)
        safe_tipo = "DOC"
        if tipo_documento:
            # Pega apenas letras e números, uppercase
            safe_tipo = "".join(c for c in tipo_documento if c.isalnum() or c == "_").upper()
        
        # Banco (padrão BANCO se não informado)
        safe_banco = "BANCO"
        if banco:
            # Pega apenas letras e números, uppercase
            safe_banco = "".join(c for c in banco if c.isalnum() or c in " ._-").strip()
            safe_banco = safe_banco.replace(" ", "_").upper()
             
        # Extensão (pega do original ou assume .pdf)
        ext = Path(original_filename).suffix.lower() if original_filename else ".pdf"
        if not ext:
            ext = ".pdf"
            
        base_name = f"{safe_tipo}_{safe_banco}"
        filename = f"{base_name}{ext}"
        
        # Se já existir arquivo com mesmo nome, será sobrescrito
        return filename
    
    def _ensure_unique_filename(
        self,
        original_filename: str,
        pdf_data: bytes,
        target_path: Path
    ) -> str:
        """
        Garante que o nome do arquivo seja único no diretório.
        
        Se já existir arquivo com mesmo nome, adiciona sufixo.
        """
        # Remove extensão e adiciona de volta .pdf
        name = Path(original_filename).stem
        filename = f"{name}.pdf"
        
        if (target_path / filename).exists():
            hash_suffix = short_hash(pdf_data)
            filename = f"{name}_{hash_suffix}.pdf"
        
        return filename
    
    def check_folder_exists(self, client: ClientInfo) -> bool:
        """
        Verifica se a pasta do cliente existe.
        
        Útil para validação antes do salvamento.
        """
        client_path = self.settings.base_path / client.folder_name
        return client_path.exists()
    
    def find_client_folder(self, cod: str) -> Path | None:
        """
        Procura a pasta do cliente pelo código.
        
        Busca pastas que começam com o código especificado.
        Útil quando o nome exato não é conhecido.
        
        Args:
            cod: Código do cliente (ex: "098")
            
        Returns:
            Path da pasta encontrada ou None
        """
        pattern = f"{cod} - *"
        matches = list(self.settings.base_path.glob(pattern))
        
        if matches:
            return matches[0]
        
        return None
