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

# Mapeamento de tipos de documento para nomes de arquivo
DOCUMENT_TYPE_MAPPING = {
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
    "CONTA GRÁFICA DETALHADA": "CONTA GRÁFICA DETALHADA",
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
    ) -> str | None:
        """Seleciona a conta respeitando regras por banco."""
        if self._is_cresol(banco) and conta_cadastrada:
            return conta_cadastrada
        return conta_extrato or conta_cadastrada

    def save_file(
        self,
        pdf_data: bytes,
        match_result: MatchResult,
        original_filename: str,
        tipo_documento: str | None = None,
        banco: str | None = None,
        conta_extrato: str | None = None,
        module: str = "make",
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
        module_norm = (module or "make").lower()
        # Usa automaticamente o mês anterior
        ano, mes = self._get_previous_month()

        target_path = None

        if match_result.identificado:
            # Tenta resolver o caminho do cliente validando existência
            client_base_path = self._resolve_client_path(match_result.cliente)

            if client_base_path:
                # Pasta padrão: cliente/Departamento Contabil/ANO/MES
                target_path = self._build_path_structure(client_base_path, ano, mes)

                # Garante que a pasta do mes existe
                month_path = self._build_path_structure(client_base_path, ano, mes)
                if not month_path.exists():
                    logger.info(f"Criando pasta do mes: {month_path}")
                    month_path.mkdir(parents=True, exist_ok=True)

                # Garante que a pasta do mes existe
                if not target_path.exists():
                    logger.info(f"Criando pasta do mes: {target_path}")
                    target_path.mkdir(parents=True, exist_ok=True)

                filename = self._build_filename(
                    banco,
                    tipo_documento,
                    pdf_data,
                    target_path,
                    original_filename
                )
                if module_norm.startswith("extra"):
                    filename = self._ensure_unique_filename_incremental(
                        filename,
                        target_path
                    )
            else:
                logger.warning(
                    f"Estrutura de pastas não encontrada para cliente {match_result.cliente.cod}. "
                    "Salvando em NAO_IDENTIFICADOS."
                )
        
        # Fallback: salva direto na pasta NAO_IDENTIFICADOS (sem subpastas)
        if not target_path:
            target_path = self.get_unidentified_path(module)
            
            # Verifica se a pasta de não identificados existe
            if not target_path.exists():
                logger.warning(f"Pasta NAO_IDENTIFICADOS nao encontrada, criando: {target_path}")
                target_path.mkdir(parents=True, exist_ok=True)
            
            if module_norm.startswith("extra"):
                filename = self._ensure_unique_filename_incremental(
                    original_filename,
                    target_path,
                )
            else:
                filename = self._ensure_unique_filename(
                    original_filename,
                    pdf_data,
                    target_path
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

        Estrutura padrão: cliente/Departamento Contabil/ANO/MES
        NUNCA cria subpastas por banco ou conta.

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

        return base_path
    
    def _build_unidentified_path(self, ano: int, mes: int, module: str = "make") -> Path:
        """
        Constrói o caminho para arquivos não identificados.
        
        Estrutura: NAO_IDENTIFICADOS/ANO/MÊS
        """
        mes_nome = MONTH_NAMES.get(mes, f"MES_{mes}")
        return self.get_unidentified_path(module) / str(ano) / mes_nome

    def get_unidentified_path(self, module: str = "make") -> Path:
        """
        Retorna o caminho base de NAO_IDENTIFICADOS conforme o módulo.
        module: "make" | "extratos"
        """
        module_norm = (module or "make").lower()
        if module_norm.startswith("extra"):
            return self.settings.unidentified_extratos_path
        return self.settings.unidentified_make_path

    
    def _build_filename(
        self,
        banco: str | None,
        tipo_documento: str | None,
        pdf_data: bytes,
        target_path: Path,
        original_filename: str = "",
    ) -> str:
        """
        Constrói o nome do arquivo usando o mapeamento de tipos de documento.

        Formato: NOME_DO_TIPO_DOCUMENTO.pdf
        Ex: EXTRATO DE CONTA CORRENTE.pdf
        """
        # Extensão (pega do original ou assume .pdf)
        ext = Path(original_filename).suffix.lower() if original_filename else ".pdf"
        if not ext:
            ext = ".pdf"

        # Tenta mapear o tipo de documento
        if tipo_documento:
            # Normaliza o tipo para uppercase para buscar no mapeamento
            tipo_upper = tipo_documento.upper()

            # Busca no mapeamento (case-insensitive)
            mapped_name = None
            for key, value in DOCUMENT_TYPE_MAPPING.items():
                if key.upper() == tipo_upper:
                    mapped_name = value
                    break

            # Se encontrou no mapeamento, usa o nome mapeado
            if mapped_name:
                filename = f"{mapped_name}{ext}"
            else:
                # Se não encontrou, usa o tipo original
                filename = f"{tipo_documento}{ext}"
        else:
            # Fallback: usa banco se disponível
            if banco:
                filename = f"EXTRATO_{banco.upper()}{ext}"
            else:
                filename = f"DOCUMENTO{ext}"

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

    def _ensure_unique_filename_incremental(
        self,
        original_filename: str,
        target_path: Path
    ) -> str:
        """
        Garante nome único adicionando sufixo incremental (_1, _2, ...).
        """
        original_path = Path(original_filename)
        stem = original_path.stem or "DOCUMENTO"
        suffix = original_path.suffix.lower() or ".pdf"
        filename = f"{stem}{suffix}"

        if not (target_path / filename).exists():
            return filename

        counter = 1
        while True:
            candidate = f"{stem}_{counter}{suffix}"
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
