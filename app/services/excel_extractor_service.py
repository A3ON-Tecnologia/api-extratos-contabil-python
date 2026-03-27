"""
Extração estruturada de dados de arquivos Excel bancários.

Evita chamar a LLM para arquivos cujos campos já estão em células fixas e previsíveis.
Suporta: Sicredi (relatorioTitulos / Relatório de Boletos).

Fluxo:
    excel_extractor.extract(file_data, filename)
        → detecta formato
        → extrai campos diretamente via pandas
        → retorna LLMExtractionResult (confiança 0.95) ou None (fallback para LLM)
"""

import io
import logging
import re
import unicodedata
from typing import BinaryIO

import pandas as pd

from app.schemas.llm_response import LLMExtractionResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Remove acentos, normaliza espaços e converte para maiúsculo."""
    nfkd = unicodedata.normalize("NFKD", str(text))
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_str).strip().upper()


def _find_label_value(df: pd.DataFrame, label: str) -> str | None:
    """
    Procura 'label' em qualquer célula da coluna 0 e retorna o primeiro valor
    não-nulo nas colunas 1 ou 2 da mesma linha (suporte a células mescladas).
    Usa comparação normalizada para ignorar encoding e acentos.
    """
    label_norm = _normalize(label)
    for idx in range(min(len(df), 30)):
        cell = df.iloc[idx, 0]
        if pd.isna(cell):
            continue
        if _normalize(str(cell)).startswith(label_norm.rstrip(":")):
            # Tenta col 1, depois col 2 (células mescladas ficam na col seguinte)
            for col in range(1, min(4, df.shape[1])):
                val = df.iloc[idx, col]
                if val is not None and not pd.isna(val):
                    return str(val).strip()
    return None


def _find_cell_containing(df: pd.DataFrame, keyword: str) -> str | None:
    """Busca keyword (normalizada) em qualquer célula das primeiras 20 linhas."""
    kw_norm = _normalize(keyword)
    for idx in range(min(len(df), 20)):
        for col in range(min(df.shape[1], 3)):
            cell = df.iloc[idx, col]
            if pd.isna(cell):
                continue
            if kw_norm in _normalize(str(cell)):
                return str(cell).strip()
    return None


def _parse_period(text: str) -> tuple[int | None, int | None]:
    """
    Extrai (mes, ano) de strings como:
      "Dados referentes ao período 01/02/2026 a 28/02/2026."
      "Período: 01/01/2026 a 31/01/2026"
    Retorna o mês/ano da data final (data de referência do extrato).
    """
    dates = re.findall(r"(\d{2})/(\d{2})/(\d{4})", text)
    if not dates:
        return None, None
    # Usa a última data encontrada (data final do período)
    day, month, year = dates[-1]
    return int(month), int(year)


# ---------------------------------------------------------------------------
# Detectores de formato
# ---------------------------------------------------------------------------

def _is_sicredi_boletos(df: pd.DataFrame) -> bool:
    """
    Detecta o formato 'Relatório de Boletos' do Sicredi.

    Critérios:
    - Contém labels "Associado:" e "Cooperativa:" e "Conta Corrente:"
    - Contém texto "Relatório de Boletos" ou "Relatorio de Boletos" nas primeiras linhas
    """
    has_associado = _find_label_value(df, "Associado:") is not None
    has_cooperativa = _find_label_value(df, "Cooperativa:") is not None
    has_conta = _find_label_value(df, "Conta Corrente:") is not None
    has_titulo = _find_cell_containing(df, "RELATORIO DE BOLETOS") is not None

    return has_associado and has_cooperativa and has_conta and has_titulo


def _is_cooperativa_extrato(df: pd.DataFrame) -> bool:
    """
    Detecta extratos de cooperativas (Sicoob, Sicredi, etc.) que usam
    as labels padrão: Associado, Cooperativa e Conta Corrente.

    Não exige título específico — as 3 labels já identificam o formato.
    """
    has_associado = _find_label_value(df, "Associado:") is not None
    has_cooperativa = _find_label_value(df, "Cooperativa:") is not None
    has_conta = _find_label_value(df, "Conta Corrente:") is not None
    return has_associado and has_cooperativa and has_conta


# ---------------------------------------------------------------------------
# Extratores por formato
# ---------------------------------------------------------------------------

def _extract_sicredi_boletos(df: pd.DataFrame) -> LLMExtractionResult:
    """Extrai campos estruturados do Relatório de Boletos do Sicredi."""

    cliente = _find_label_value(df, "Associado:")
    cooperativa = _find_label_value(df, "Cooperativa:")  # = agência no Sicredi
    conta = _find_label_value(df, "Conta Corrente:")

    # Período
    periodo_raw = _find_cell_containing(df, "PERIODO") or _find_cell_containing(df, "DADOS REFERENTES")
    mes, ano = _parse_period(periodo_raw) if periodo_raw else (None, None)

    # Tipo: verifica situação do boleto para mapear o tipo canônico
    situacao = _find_label_value(df, "Situação do Boleto:") or _find_label_value(df, "Situacao do Boleto:")
    tipo = "REL RECEBIMENTO"  # padrão para Relatório de Boletos

    logger.info(
        "[EXCEL_EXTRACTOR] Sicredi Boletos | cliente=%s | cooperativa=%s | conta=%s | "
        "mes=%s ano=%s | situacao=%s | tipo=%s",
        cliente, cooperativa, conta, mes, ano, situacao, tipo,
    )

    return LLMExtractionResult(
        cliente_sugerido=cliente,
        cnpj=None,
        banco="SICREDI",
        agencia=cooperativa,
        conta=conta,
        contrato=None,
        tipo_documento=tipo,
        confianca=0.95,
    )


def _detect_banco_cooperativa(df: pd.DataFrame, filename: str) -> str:
    """Tenta identificar o banco a partir do conteúdo ou nome do arquivo."""
    filename_upper = filename.upper()
    if "SICOOB" in filename_upper:
        return "SICOOB"
    if "SICREDI" in filename_upper:
        return "SICREDI"
    # Busca nas primeiras células
    for row in range(min(len(df), 10)):
        for col in range(min(df.shape[1], 4)):
            cell = str(df.iloc[row, col]) if not pd.isna(df.iloc[row, col]) else ""
            cell_up = _normalize(cell)
            if "SICOOB" in cell_up:
                return "SICOOB"
            if "SICREDI" in cell_up:
                return "SICREDI"
    return "COOPERATIVA"  # genérico se não identificar


def _extract_cooperativa_extrato(df: pd.DataFrame, filename: str) -> LLMExtractionResult:
    """Extrai campos de extrato de cooperativa usando as labels padrão."""

    cliente = _find_label_value(df, "Associado:")
    cooperativa = _find_label_value(df, "Cooperativa:")  # = agência
    conta = _find_label_value(df, "Conta Corrente:")
    banco = _detect_banco_cooperativa(df, filename)

    # Período
    periodo_raw = _find_cell_containing(df, "PERIODO") or _find_cell_containing(df, "DADOS REFERENTES")
    mes, ano = _parse_period(periodo_raw) if periodo_raw else (None, None)

    # Tipo: tenta identificar pelo título, senão usa padrão
    tipo = "EXTRATO"
    for keyword, tipo_canonico in [
        ("RELATORIO DE BOLETOS", "REL RECEBIMENTO"),
        ("EXTRATO DE CONTA", "EXTRATO CC"),
        ("EXTRATO CAPITAL", "EXTRATO CAPITAL"),
        ("RELATORIO DE TITULOS", "REL RECEBIMENTO"),
    ]:
        if _find_cell_containing(df, keyword) is not None:
            tipo = tipo_canonico
            break

    logger.info(
        "[EXCEL_EXTRACTOR] Cooperativa Extrato | banco=%s | cliente=%s | cooperativa=%s | "
        "conta=%s | mes=%s ano=%s | tipo=%s",
        banco, cliente, cooperativa, conta, mes, ano, tipo,
    )

    return LLMExtractionResult(
        cliente_sugerido=cliente,
        cnpj=None,
        banco=banco,
        agencia=cooperativa,
        conta=conta,
        contrato=None,
        tipo_documento=tipo,
        confianca=0.95,
    )


# ---------------------------------------------------------------------------
# Entry point público
# ---------------------------------------------------------------------------

class ExcelExtractorService:
    """
    Tenta extrair campos estruturados de arquivos Excel bancários sem chamar a LLM.
    Retorna None se o formato não for reconhecido (fallback para LLM).
    """

    def extract(
        self,
        file_data: bytes | BinaryIO,
        filename: str = "",
    ) -> LLMExtractionResult | None:
        """
        Tenta extração estruturada.

        Returns:
            LLMExtractionResult com confiança 0.95 se reconhecido,
            None se o formato não for suportado (caller deve usar LLM).
        """
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if ext not in (".xls", ".xlsx", ".ods"):
            return None

        try:
            if isinstance(file_data, bytes):
                file_data = io.BytesIO(file_data)

            # Tenta openpyxl primeiro (funciona para .xlsx e para .xls salvos no formato moderno).
            # Fallback para xlrd apenas se openpyxl falhar (arquivos BIFF legados).
            try:
                xls = pd.ExcelFile(file_data, engine="openpyxl")
            except Exception:
                if isinstance(file_data, io.BytesIO):
                    file_data.seek(0)
                xls = pd.ExcelFile(file_data, engine="xlrd")

            # Tenta a aba "Relatorio" primeiro, depois qualquer aba
            sheet_name = "Relatorio" if "Relatorio" in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet_name, header=None)

            if _is_sicredi_boletos(df):
                return _extract_sicredi_boletos(df)

            if _is_cooperativa_extrato(df):
                return _extract_cooperativa_extrato(df, filename)

            # Log diagnóstico: mostra as primeiras células para identificar novo formato
            logger.warning(
                "[EXCEL_EXTRACTOR] Formato não reconhecido para '%s' (%d linhas, %d colunas) — "
                "primeiras células: %s",
                filename,
                len(df),
                df.shape[1],
                str(df.iloc[:8, :4].values.tolist()),
            )
            return None

        except Exception as e:
            logger.warning(
                "[EXCEL_EXTRACTOR] Erro ao tentar extração estruturada de '%s': %s",
                filename, e,
            )
            return None


_excel_extractor: ExcelExtractorService | None = None


def get_excel_extractor_service() -> ExcelExtractorService:
    global _excel_extractor
    if _excel_extractor is None:
        _excel_extractor = ExcelExtractorService()
    return _excel_extractor
