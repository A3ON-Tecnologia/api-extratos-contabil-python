"""
Serviço de extração de dados de arquivos Excel (XLS/XLSX).

Extrai informações estruturadas diretamente de planilhas de extratos
sem necessidade de LLM, economizando tokens e aumentando precisão.
"""

import logging
import re
from io import BytesIO
from typing import BinaryIO

import pandas as pd

logger = logging.getLogger(__name__)


class ExcelExtractorService:
    """Serviço para extração de dados estruturados de arquivos Excel."""

    def extract_structured_data(self, file_data: bytes | BinaryIO) -> dict | None:
        """
        Extrai dados estruturados de um arquivo Excel.

        Detecta automaticamente o formato (Sicoob, Sicredi, etc) e extrai:
        - Nome do cliente/associado
        - CNPJ (se disponível)
        - Banco/Cooperativa
        - Conta Corrente
        - Agência
        - Tipo de documento
        - Período (data início/fim)

        Args:
            file_data: Bytes ou file-like object do Excel

        Returns:
            Dicionário com dados extraídos ou None se não conseguir extrair
        """
        try:
            if isinstance(file_data, bytes):
                file_data = BytesIO(file_data)

            # Lê todas as abas do Excel
            dfs = pd.read_excel(file_data, sheet_name=None, engine='openpyxl')

            # Tenta detectar formato Sicoob
            sicoob_data = self._extract_sicoob(dfs)
            if sicoob_data:
                logger.info(f"Dados Sicoob extraídos: {sicoob_data.get('cliente_nome', 'N/A')}")
                return sicoob_data

            # Tenta detectar formato Sicredi
            sicredi_data = self._extract_sicredi(dfs)
            if sicredi_data:
                logger.info(f"Dados Sicredi extraídos: {sicredi_data.get('cliente_nome', 'N/A')}")
                return sicredi_data

            # Adicione outros formatos aqui...

            logger.warning("Formato Excel não reconhecido para extração estruturada")
            return None

        except Exception as e:
            logger.error(f"Erro ao extrair dados estruturados do Excel: {e}")
            return None

    def _extract_sicoob(self, dfs: dict[str, pd.DataFrame]) -> dict | None:
        """
        Extrai dados de extrato do Sicoob.

        Formato esperado:
        - Linha com "Associado:" -> nome do cliente
        - Linha com "Cooperativa:" -> número da cooperativa
        - Linha com "Conta Corrente:" -> número da conta
        - Linha com "Beneficiário" -> pode ter CNPJ
        - Linha com "período DD/MM/YYYY a DD/MM/YYYY" -> datas
        """
        for sheet_name, df in dfs.items():
            try:
                # Converte tudo para string para facilitar busca
                df_str = df.astype(str)

                # Busca campos específicos do Sicoob
                associado = None
                cooperativa = None
                conta_corrente = None
                beneficiario = None
                periodo = None

                # Procura em todas as células
                for idx, row in df_str.iterrows():
                    row_text = ' '.join(row.values).upper()

                    # Extrai Associado (nome do cliente)
                    if 'ASSOCIADO:' in row_text:
                        # Pega o valor na próxima coluna ou mesma linha
                        for col_idx, cell_value in enumerate(row.values):
                            if 'ASSOCIADO:' in str(cell_value).upper():
                                # Tenta pegar valor na próxima coluna
                                if col_idx + 1 < len(row.values):
                                    associado = str(row.values[col_idx + 1]).strip()
                                    if associado and associado != 'nan':
                                        break
                                # Se não, pega depois dos dois pontos
                                text_after = str(cell_value).split(':', 1)
                                if len(text_after) > 1:
                                    associado = text_after[1].strip()
                                    if associado and associado != 'nan':
                                        break

                    # Extrai Cooperativa
                    if 'COOPERATIVA:' in row_text:
                        for col_idx, cell_value in enumerate(row.values):
                            if 'COOPERATIVA:' in str(cell_value).upper():
                                if col_idx + 1 < len(row.values):
                                    cooperativa = str(row.values[col_idx + 1]).strip()
                                    if cooperativa and cooperativa != 'nan':
                                        break
                                text_after = str(cell_value).split(':', 1)
                                if len(text_after) > 1:
                                    cooperativa = text_after[1].strip()
                                    if cooperativa and cooperativa != 'nan':
                                        break

                    # Extrai Conta Corrente
                    if 'CONTA CORRENTE:' in row_text:
                        for col_idx, cell_value in enumerate(row.values):
                            if 'CONTA CORRENTE:' in str(cell_value).upper():
                                if col_idx + 1 < len(row.values):
                                    conta_corrente = str(row.values[col_idx + 1]).strip()
                                    if conta_corrente and conta_corrente != 'nan':
                                        break
                                text_after = str(cell_value).split(':', 1)
                                if len(text_after) > 1:
                                    conta_corrente = text_after[1].strip()
                                    if conta_corrente and conta_corrente != 'nan':
                                        break

                    # Extrai Beneficiário (pode ter CNPJ)
                    if 'BENEFICIÁRIO' in row_text or 'BENEFICIARIO' in row_text:
                        for col_idx, cell_value in enumerate(row.values):
                            cell_str = str(cell_value).upper()
                            if 'BENEFICIÁRIO' in cell_str or 'BENEFICIARIO' in cell_str:
                                # Pega toda a linha do beneficiário (pode ter CNPJ)
                                if idx + 1 < len(df_str):
                                    next_row = df_str.iloc[idx + 1]
                                    beneficiario = ' '.join(str(v) for v in next_row.values if str(v) != 'nan').strip()
                                    break

                    # Extrai período
                    if 'PERÍODO' in row_text or 'PERIODO' in row_text or 'REFERENTES AO' in row_text:
                        # Busca padrão DD/MM/YYYY a DD/MM/YYYY
                        match = re.search(r'(\d{2}/\d{2}/\d{4})\s+[aA]\s+(\d{2}/\d{2}/\d{4})', row_text)
                        if match:
                            periodo = f"{match.group(1)} a {match.group(2)}"

                # Se encontrou pelo menos associado ou conta, retorna dados
                if associado or conta_corrente:
                    # Extrai CNPJ do beneficiário se houver
                    cnpj = None
                    if beneficiario:
                        cnpj_match = re.search(r'(\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2})', beneficiario)
                        if cnpj_match:
                            cnpj = cnpj_match.group(1)
                            # Remove formatação
                            cnpj = re.sub(r'[^\d]', '', cnpj)

                    # Extrai ano e mês do período
                    ano, mes = None, None
                    if periodo:
                        # Pega a segunda data (fim do período)
                        match = re.search(r'(\d{2})/(\d{2})/(\d{4})$', periodo)
                        if match:
                            mes = int(match.group(2))
                            ano = int(match.group(3))

                    return {
                        "cliente_nome": associado or beneficiario or None,
                        "cnpj": cnpj,
                        "banco": "SICOOB",
                        "cooperativa": cooperativa,
                        "conta": self._normalize_conta(conta_corrente),
                        "agencia": cooperativa,  # No Sicoob, cooperativa = agência
                        "tipo_documento": "EXTRATO",
                        "periodo": periodo,
                        "ano": ano,
                        "mes": mes,
                        "confianca": 0.95,  # Alta confiança em dados estruturados
                        "metodo_extracao": "EXCEL_SICOOB",
                    }

            except Exception as e:
                logger.error(f"Erro ao processar aba {sheet_name}: {e}")
                continue

        return None

    def _extract_sicredi(self, dfs: dict[str, pd.DataFrame]) -> dict | None:
        """
        Extrai dados de extrato do Sicredi.

        TODO: Implementar quando necessário
        """
        # Adicionar lógica para Sicredi quando houver exemplos
        return None

    def _normalize_conta(self, conta: str | None) -> str | None:
        """
        Normaliza número de conta removendo caracteres especiais.

        Mantém apenas dígitos e hífen (para dígito verificador).
        """
        if not conta or conta == 'nan':
            return None

        # Remove espaços e mantém apenas dígitos e hífen
        conta = str(conta).strip()
        conta = re.sub(r'[^\d-]', '', conta)

        return conta if conta else None


def get_excel_extractor_service() -> ExcelExtractorService:
    """Factory function para obter instância do serviço."""
    return ExcelExtractorService()
