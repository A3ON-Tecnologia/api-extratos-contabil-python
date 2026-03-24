"""
Servico de extracao de arquivos ZIP.

Processa arquivos ZIP e extrai PDFs e OFX contidos.
"""

import io
import logging
import zipfile
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFile:
    """Arquivo extraido do ZIP."""

    filename: str
    """Nome original do arquivo dentro do ZIP."""

    data: bytes
    """Conteudo do arquivo em bytes."""


@dataclass
class ZIPExtractionReport:
    """Resumo auditavel da extracao do ZIP."""

    arquivos_no_zip: int = 0
    extraidos: int = 0
    ignorados: int = 0
    ignorados_detalhes: list[dict[str, str]] = field(default_factory=list)
    erros_extracao: int = 0

    def to_dict(self) -> dict:
        return {
            "arquivos_no_zip": self.arquivos_no_zip,
            "extraidos": self.extraidos,
            "ignorados": self.ignorados,
            "ignorados_detalhes": self.ignorados_detalhes,
            "erros_extracao": self.erros_extracao,
        }


@dataclass
class ZIPExtractionResult:
    """Resultado da extracao com arquivos e relatorio."""

    extracted_files: list[ExtractedFile]
    report: ZIPExtractionReport


class ZIPService:
    """Servico para extracao de PDFs e OFX de arquivos ZIP."""

    def extract_with_report(self, zip_data: bytes) -> ZIPExtractionResult:
        """
        Extrai PDFs/OFX de um ZIP e devolve relatorio de auditoria.

        Raises:
            ValueError: Se o ZIP estiver corrompido ou sem arquivos validos.
        """
        try:
            zip_file = zipfile.ZipFile(io.BytesIO(zip_data))
        except zipfile.BadZipFile:
            raise ValueError("Arquivo ZIP corrompido ou invalido")

        extracted_files: list[ExtractedFile] = []
        report = ZIPExtractionReport()

        for file_info in zip_file.filelist:
            if file_info.is_dir():
                continue

            report.arquivos_no_zip += 1
            filename = file_info.filename.split("/")[-1]

            if file_info.filename.startswith("__"):
                report.ignorados += 1
                report.ignorados_detalhes.append({"arquivo": filename, "motivo": "pasta_sistema"})
                continue

            if filename.startswith("."):
                report.ignorados += 1
                report.ignorados_detalhes.append({"arquivo": filename, "motivo": "arquivo_oculto"})
                continue

            lower_name = filename.lower()
            is_pdf = lower_name.endswith(".pdf")
            is_ofx = lower_name.endswith(".ofx")
            if not (is_pdf or is_ofx):
                report.ignorados += 1
                report.ignorados_detalhes.append({"arquivo": filename, "motivo": "extensao_nao_suportada"})
                logger.debug("Ignorando arquivo nao-PDF/OFX: %s", filename)
                continue

            try:
                data = zip_file.read(file_info.filename)

                if is_pdf and not data.startswith(b"%PDF-"):
                    report.ignorados += 1
                    report.ignorados_detalhes.append({"arquivo": filename, "motivo": "pdf_magic_bytes_invalidos"})
                    logger.warning("Arquivo %s tem extensao .pdf mas nao e um PDF valido", filename)
                    continue

                extracted_files.append(ExtractedFile(filename=filename, data=data))
                report.extraidos += 1
                logger.info("Arquivo extraido do ZIP: %s", filename)

            except Exception as e:
                report.erros_extracao += 1
                report.ignorados += 1
                report.ignorados_detalhes.append({"arquivo": filename, "motivo": "erro_extracao"})
                logger.error("Erro ao extrair %s: %s", filename, e)

        if not extracted_files:
            raise ValueError("Nenhum arquivo PDF ou OFX encontrado no ZIP")

        logger.info(
            "Extracao ZIP concluida - no_zip=%s extraidos=%s ignorados=%s erros=%s",
            report.arquivos_no_zip,
            report.extraidos,
            report.ignorados,
            report.erros_extracao,
        )
        return ZIPExtractionResult(extracted_files=extracted_files, report=report)

    def extract_pdfs(self, zip_data: bytes) -> list[ExtractedFile]:
        """
        Compatibilidade com chamadas antigas.

        Retorna apenas os arquivos extraidos.
        """
        return self.extract_with_report(zip_data).extracted_files

    def is_valid_zip(self, data: bytes) -> bool:
        """
        Verifica se os dados representam um ZIP valido.

        Args:
            data: Bytes do arquivo

        Returns:
            True se for um ZIP valido
        """
        if not data.startswith(b"PK"):
            return False

        try:
            zipfile.ZipFile(io.BytesIO(data))
            return True
        except zipfile.BadZipFile:
            return False
