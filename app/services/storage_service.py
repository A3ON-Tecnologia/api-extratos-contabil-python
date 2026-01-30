"""
Serviço de armazenamento de arquivos.

Responsável por salvar os PDFs no caminho correto da estrutura
de pastas em rede Windows.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.schemas.client import ClientInfo, MatchResult
from app.utils.hash import short_hash
from app.utils.text import extract_numbers

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

# Mapeamento de tipos de documento para nomes de arquivo
DOCUMENT_TYPE_MAPPING = {
    "EXTRATO EMPR?STIMO": "EXTRATO EMPR?STIMO",
    "EXTRATO EMPRESTIMO": "EXTRATO EMPR?STIMO",
    "EXTRATO DA CONTA CAPITAL": "EXTRATO DA CONTA CAPITAL",
    "EXTRATO DE CONTA CORRENTE": "EXTRATO DE CONTA CORRENTE",
    "EXTRATO CONSOLIDADO RENDA FIXA": "EXTRATO CONSOLIDADO RENDA FIXA",
    "EXTRATO DE FATURA DE CARTÃO DE CRÉDITO": "EXTRATO DE FATURA DE CARTÃO DE CRÉDITO",
    "EXTRATO DE FATURA": "EXTRATO DE FATURA DE CARTÃO DE CRÉDITO",
    "RELATÓRIO - TÍTULOS POR PERÍODO": "REL RECEBIMENTO",
    "RELATORIO TITULOS POR PERIODO": "REL RECEBIMENTO",
    "EXTRATO CONTA POUPANÇA": "EXTRATO CONTA POUPANÇA",
    "SALDO DE APLICAÇÃO": "EXTRATO APLICAÇÃO",
    "EXTRATO APLICAÇÃO": "EXTRATO APLICAÇÃO",
    "CC": "EXTRATO DE CONTA CORRENTE",
    "POUPANÇA": "EXTRATO CONTA POUPANÇA",
    "CARTÃO": "EXTRATO DE FATURA DE CARTÃO DE CRÉDITO",
}


class StorageService:
    """Serviço de armazenamento de arquivos."""
    
    def __init__(self):
        """Inicializa o serviço."""
        self.settings = get_settings()
    
    def _get_previous_month(self) -> tuple[int, int]:
        """
        Retorna o mês anterior ao atual.
        
        Returns:
            Tupla (ano, mes) do mês anterior
        """
        now = datetime.now()
        if now.month == 1:
            return (now.year - 1, 12)
        else:
            return (now.year, now.month - 1)
    
    def _is_cresol(self, banco: str | None) -> bool:
        """Verifica se o banco e Cresol."""
        if not banco:
            return False
        return "CRESOL" in str(banco).upper()

    def _select_account(
        self,
        banco: str | None,
        conta_extrato: str | None,
        conta_cadastrada: str | None,
        tipo_documento: str | None = None,
    ) -> str | None:
        """Seleciona a conta respeitando regras por banco."""
        if self._is_cresol(banco) and conta_cadastrada:
            return conta_cadastrada
        if tipo_documento and "CONTA CAPITAL" in tipo_documento.upper():
            if not conta_extrato or not conta_cadastrada:
                return None
            conta_numbers = extract_numbers(conta_extrato)
            cadastrado_numbers = extract_numbers(conta_cadastrada)
            if conta_numbers and conta_numbers == cadastrado_numbers:
                return conta_extrato
            return None
        return conta_extrato or conta_cadastrada

    def save_file(
        self,
        pdf_data: bytes,
        match_result: MatchResult,
        original_filename: str,
        tipo_documento: str | None = None,
        banco: str | None = None,
        conta_extrato: str | None = None,
        contrato: str | None = None,
    ) -> tuple[str, int, int]:
        """
        Salva o arquivo PDF no caminho correto.

        Usa automaticamente o mês anterior ao processamento como período do documento.
        Estrutura: ANO/MES/BANCO/CONTA/arquivo.pdf
        Cria a hierarquia de pastas quando necessario.

        Args:
            conta_extrato: Número da conta extraído do extrato (prioritário sobre a conta da planilha)

        Returns:
            Tupla (caminho_salvo, ano, mes)
        """
        # Usa automaticamente o mês anterior
        ano, mes = self._get_previous_month()

        target_path = None

        if match_result.identificado:
            # Tenta resolver o caminho do cliente validando existência
            client_base_path = self._resolve_client_path(match_result.cliente)

            if client_base_path:
                # Pasta padrao: cliente/Departamento Contabil/ANO/MES/(BANCO)/(CONTA)
                conta = self._select_account(
                    banco,
                    conta_extrato,
                    match_result.cliente.conta,
                    tipo_documento,
                )
                target_path = self._build_path_structure(client_base_path, ano, mes, banco, conta)

                # Garante que a pasta do destino existe
                if not target_path.exists():
                    logger.info(f"Criando pasta do destino: {target_path}")
                    target_path.mkdir(parents=True, exist_ok=True)

                filename = self._build_filename(
                    banco,
                    tipo_documento,
                    contrato,
                    pdf_data,
                    target_path,
                    original_filename,
                    conta
                )
                filename = self._ensure_incremental_filename(filename, target_path)
            else:
                logger.warning(
                    f"Estrutura de pastas não encontrada para cliente {match_result.cliente.cod}. "
                    "Salvando em NAO_IDENTIFICADOS."
                )
        
        # Fallback: salva direto na pasta NAO_IDENTIFICADOS (sem subpastas)
        if not target_path:
            target_path = self.settings.unidentified_path
            
            # Verifica se a pasta de não identificados existe
            if not target_path.exists():
                logger.warning(f"Pasta NAO_IDENTIFICADOS nao encontrada, criando: {target_path}")
                target_path.mkdir(parents=True, exist_ok=True)
            
            filename = self._ensure_incremental_filename(
                self._ensure_unique_filename(original_filename),
                target_path,
            )
        
        # Salva o arquivo
        full_path = target_path / filename
        full_path.write_bytes(pdf_data)
        
        logger.info(f"Arquivo salvo: {full_path}")
        return (str(full_path), ano, mes)

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
    
    def _build_path_structure(
        self,
        client_base_path: Path,
        ano: int,
        mes: int,
        banco: str | None = None,
        conta: str | None = None,
    ) -> Path:
        """
        Constrói a estrutura de pastas dentro da pasta do cliente.

        Estrutura padrao: cliente/Departamento Contabil/ANO/MES/(BANCO)/(CONTA)

        Aceita tanto "Departamento Contábil" quanto "Departamento Contabil" (sem acento)
        """
        # Mês como número com 2 dígitos (01, 02, ..., 12)
        mes_str = str(mes).zfill(2)

        # Tenta com acento primeiro
        dept_path = client_base_path / "Departamento Contábil"
        if dept_path.exists():
            base_path = dept_path / str(ano) / mes_str
        else:
            # Tenta sem acento
            dept_path_sem_acento = client_base_path / "Departamento Contabil"
            if dept_path_sem_acento.exists():
                base_path = dept_path_sem_acento / str(ano) / mes_str
            else:
                # Retorna o padrão com acento (mesmo que não exista, será tratado depois)
                base_path = client_base_path / "Departamento Contábil" / str(ano) / mes_str

        # Subpastas opcionais por banco/conta (quando informados)
        if banco:
            banco_folder = self._sanitize_folder_name(str(banco).upper().strip())
            if banco_folder:
                base_path = base_path / banco_folder
        if conta:
            conta_folder = self._sanitize_folder_name(str(conta).strip())
            if conta_folder:
                base_path = base_path / conta_folder

        return base_path

    def _sanitize_folder_name(self, value: str) -> str:
        """Remove caracteres invalidos para nomes de pasta no Windows."""
        cleaned = re.sub(r'[<>:"/\\|?*]', "", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

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
        contrato: str | None,
        pdf_data: bytes,
        target_path: Path,
        original_filename: str = "",
        conta: str | None = None,
    ) -> str:
        """
        Constrói o nome do arquivo usando o mapeamento de tipos de documento.

        Formato: NOME_DO_TIPO_DOCUMENTO - CONTA.pdf
        Ex: EXTRATO DE CONTA CORRENTE - 75662.pdf

        O número da conta é usado como identificador único quando disponível.
        """
        # Extensão (pega do original ou assume .pdf)
        ext = Path(original_filename).suffix.lower() if original_filename else ".pdf"
        if not ext:
            ext = ".pdf"

        # Normaliza o número da conta (remove caracteres não numéricos)
        conta_normalizada = None
        if conta:
            conta_normalizada = extract_numbers(conta)

        # Tenta mapear o tipo de documento
        if tipo_documento:
            # Normaliza o tipo para uppercase para buscar no mapeamento
            tipo_upper = tipo_documento.upper()

            if "EMPRESTIMO" in tipo_upper and contrato:
                filename = f"EXTRATO EMPR?STIMO {contrato}{ext}"
                return filename

            # Busca no mapeamento (case-insensitive)
            mapped_name = None
            for key, value in DOCUMENT_TYPE_MAPPING.items():
                if key.upper() == tipo_upper:
                    mapped_name = value
                    break

            # Se encontrou no mapeamento, usa o nome mapeado
            if mapped_name:
                # Adiciona o número da conta se disponível
                if conta_normalizada:
                    filename = f"{mapped_name} - {conta_normalizada}{ext}"
                else:
                    filename = f"{mapped_name}{ext}"
            else:
                # Se não encontrou, usa o tipo original
                if conta_normalizada:
                    filename = f"{tipo_documento} - {conta_normalizada}{ext}"
                else:
                    filename = f"{tipo_documento}{ext}"
        else:
            # Fallback: usa banco se disponível
            if banco:
                if conta_normalizada:
                    filename = f"EXTRATO_{banco.upper()} - {conta_normalizada}{ext}"
                else:
                    filename = f"EXTRATO_{banco.upper()}{ext}"
            else:
                if conta_normalizada:
                    filename = f"DOCUMENTO - {conta_normalizada}{ext}"
                else:
                    filename = f"DOCUMENTO{ext}"

        # Se já existir arquivo com mesmo nome, será sobrescrito
        return filename
    
    def _ensure_unique_filename(self, original_filename: str) -> str:
        """Normaliza o nome base do arquivo mantendo a extensao."""
        name = Path(original_filename).stem
        ext = Path(original_filename).suffix or ".pdf"
        return f"{name}{ext}"

    def _ensure_incremental_filename(self, filename: str, target_path: Path) -> str:
        """
        Garante nome unico no diretorio usando sufixo incremental: -1, -2, ...
        """
        candidate = filename
        if not (target_path / candidate).exists():
            return candidate

        base = Path(filename).stem
        ext = Path(filename).suffix or ".pdf"
        counter = 1
        while True:
            candidate = f"{base}-{counter}{ext}"
            if not (target_path / candidate).exists():
                return candidate
            counter += 1

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
