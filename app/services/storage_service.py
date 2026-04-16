"""
Servico de armazenamento de arquivos.

Responsavel por salvar os PDFs no caminho correto da estrutura
de pastas em rede Windows.
"""

import logging
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.schemas.client import ClientInfo, MatchResult

logger = logging.getLogger(__name__)

# Mapeamento de numero do mes para nome da pasta
MONTH_NAMES = {
    1: "JANEIRO",
    2: "FEVEREIRO",
    3: "MARCO",
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
    "EXTRATO DE FATURA DE CARTAO DE CREDITO": "EXTRATO DE FATURA DE CARTAO DE CREDITO",
    "EXTRATO DE FATURA": "EXTRATO DE FATURA DE CARTAO DE CREDITO",
    "RELATORIO - TITULOS POR PERIODO": "REL RECEBIMENTO",
    "RELATORIO TITULOS POR PERIODO": "REL RECEBIMENTO",
    "EXTRATO CONTA POUPANCA": "EXTRATO CONTA POUPANCA",
    "SALDO DE APLICACAO": "EXTRATO APLICACAO",
    "EXTRATO APLICACAO": "EXTRATO APLICACAO",
    "CONTA GRAFICA DETALHADA": "CONTA GRAFICA DETALHADA",
    "CONTA GRAFICA SIMPLIFICADA": "CONTA GRAFICA SIMPLIFICADA",
    "CC": "EXTRATO DE CONTA CORRENTE",
    "POUPANCA": "EXTRATO CONTA POUPANCA",
    "CARTAO": "EXTRATO DE FATURA DE CARTAO DE CREDITO",
    "EXTRATO EMPRESTIMO": "EXTRATO EMPRESTIMO",
    "EXTRATO CONSORCIO": "EXTRATO CONSORCIO",
    "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS": "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS LIQUIDADAS",
    "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO": "PAR - RELATORIO SELECAO DE OPERACOES PARCELAS EM ABERTO",
}


class StorageService:
    """Servico de armazenamento de arquivos."""

    def __init__(self):
        """Inicializa o servico."""
        self.settings = get_settings()

    def _get_previous_month(self) -> tuple[int, int]:
        """
        Retorna o mes anterior ao atual.

        Returns:
            Tupla (ano, mes) do mes anterior
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
        ano: int | None = None,
        mes: int | None = None,
        source: str = "local",
    ) -> tuple[str, int, int]:
        """
        Salva o arquivo PDF no caminho correto.

        Usa automaticamente o mes anterior ao processamento como periodo do documento,
        a menos que ano e mes sejam fornecidos explicitamente.
        Estrutura: ANO/MES/BANCO/CONTA/arquivo.pdf
        Cria a hierarquia de pastas quando necessario.

        Args:
            conta_extrato: Numero da conta extraido do extrato (prioritario sobre a conta da planilha)
            ano: Ano do documento (opcional)
            mes: Mes do documento (opcional)

        Returns:
            Tupla (caminho_salvo, ano, mes)
        """
        if ano is None or mes is None:
            prev_ano, prev_mes = self._get_previous_month()
            ano = ano if ano is not None else prev_ano
            mes = mes if mes is not None else prev_mes

        target_path = None
        filename = None

        if match_result.identificado:
            client_base_path = self._resolve_client_path(match_result.cliente)

            if client_base_path:
                conta = self._select_account(banco, conta_extrato, match_result.cliente.conta)
                target_path = self._build_path_structure(client_base_path, ano, mes, banco, conta)
                if not target_path.exists():
                    logger.info(f"Criando pasta: {target_path}")
                    target_path.mkdir(parents=True, exist_ok=True)

                filename = self._build_filename(
                    banco,
                    tipo_documento,
                    pdf_data,
                    target_path,
                    original_filename,
                )
            else:
                logger.warning(
                    f"Estrutura de pastas nao encontrada para cliente {match_result.cliente.cod}. "
                    "Salvando em NAO_IDENTIFICADOS."
                )

        if not target_path:
            target_path = self.get_unidentified_path(module, source)
            if not target_path.exists():
                logger.warning(f"Pasta NAO_IDENTIFICADOS nao encontrada, criando: {target_path}")
                target_path.mkdir(parents=True, exist_ok=True)
            filename = original_filename

        filename = filename or "DOCUMENTO.pdf"
        filename, full_path = self._write_bytes_unique(target_path, filename, pdf_data)

        logger.info(f"Arquivo salvo: {full_path}")
        return (str(full_path), ano, mes)

    def _resolve_client_path(self, client: ClientInfo) -> Path | None:
        """
        Resolve o caminho base do cliente, verificando se existe.

        Tenta encontrar a pasta do cliente no formato "COD - NOME".
        """
        folder_name = f"{client.cod} - {client.nome}"
        client_path = self.settings.base_path / folder_name

        if client_path.exists():
            return client_path

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
        Constroi a estrutura de pastas dentro da pasta do cliente.

        Estrutura: cliente/Departamento Contabil/ANO/MES/BANCO
        """
        mes_str = str(mes).zfill(2)

        dept_path = client_base_path / "Departamento Contábil"
        if dept_path.exists():
            base_path = dept_path / str(ano) / mes_str
        else:
            dept_path_sem_acento = client_base_path / "Departamento Contabil"
            if dept_path_sem_acento.exists():
                base_path = dept_path_sem_acento / str(ano) / mes_str
            else:
                base_path = client_base_path / "Departamento Contábil" / str(ano) / mes_str

        if banco:
            base_path = base_path / banco.upper().strip()

        return base_path

    def _build_unidentified_path(self, ano: int, mes: int, module: str = "make") -> Path:
        """Constroi o caminho para arquivos nao identificados."""
        mes_nome = MONTH_NAMES.get(mes, f"MES_{mes}")
        return self.get_unidentified_path(module) / str(ano) / mes_nome

    def get_unidentified_path(self, module: str = "make", source: str = "local") -> Path:
        """
        Retorna o caminho base de NAO_IDENTIFICADOS conforme o modulo e fonte.

        Args:
            module: "extratos", "make", etc
            source: "gmail" para arquivos do Gmail, "local" para outros
        """
        module_norm = (module or "make").lower()
        source_norm = (source or "local").lower()

        # Se vem do Gmail, usa pasta específica
        if source_norm == "gmail":
            return self.settings.unidentified_gmail_path

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
        Constroi o nome do arquivo usando o mapeamento de tipos de documento.

        Formato: NOME_DO_TIPO_DOCUMENTO.pdf
        """
        ext = Path(original_filename).suffix.lower() if original_filename else ".pdf"
        if not ext:
            ext = ".pdf"

        if tipo_documento:
            tipo_upper = tipo_documento.upper()
            mapped_name = None
            for key, value in DOCUMENT_TYPE_MAPPING.items():
                if key.upper() == tipo_upper:
                    mapped_name = value
                    break

            if mapped_name:
                filename = f"{mapped_name}{ext}"
            else:
                filename = f"{tipo_documento}{ext}"
        else:
            if banco:
                filename = f"EXTRATO_{banco.upper()}{ext}"
            else:
                filename = f"DOCUMENTO{ext}"

        return filename

    def _write_bytes_unique(
        self,
        target_path: Path,
        desired_filename: str,
        data: bytes,
        max_attempts: int = 1000,
    ) -> tuple[str, Path]:
        """
        Salva bytes garantindo nome unico com sufixo incremental (_1, _2, ...).
        """
        target_path.mkdir(parents=True, exist_ok=True)
        desired_path = Path(desired_filename)
        stem = desired_path.stem or "DOCUMENTO"
        suffix = desired_path.suffix or ".pdf"

        counter = 0
        while counter < max_attempts:
            name = f"{stem}{suffix}" if counter == 0 else f"{stem}_{counter}{suffix}"
            full_path = target_path / name
            try:
                with full_path.open("xb") as handle:
                    handle.write(data)
                return name, full_path
            except FileExistsError:
                counter += 1

        raise RuntimeError(
            f"Nao foi possivel criar arquivo unico apos {max_attempts} tentativas. "
            f"Base: {stem}{suffix}"
        )

    def check_folder_exists(self, client: ClientInfo) -> bool:
        """Verifica se a pasta do cliente existe."""
        client_path = self.settings.base_path / client.folder_name
        return client_path.exists()

    def find_client_folder(self, cod: str) -> Path | None:
        """Procura a pasta do cliente pelo codigo."""
        pattern = f"{cod} - *"
        matches = list(self.settings.base_path.glob(pattern))

        if matches:
            return matches[0]

        return None
