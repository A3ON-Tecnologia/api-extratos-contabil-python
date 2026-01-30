"""
Serviço de extração de arquivos ZIP.

Processa arquivos ZIP e extrai PDFs e OFX contidos.
"""

import io
import logging
import zipfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFile:
    """Arquivo extraído do ZIP."""
    
    filename: str
    """Nome original do arquivo dentro do ZIP."""
    
    data: bytes
    """Conteúdo do arquivo em bytes."""


class ZIPService:
    """Serviço para extração de PDFs/OFX de arquivos ZIP."""
    
    def extract_pdfs(self, zip_data: bytes) -> list[ExtractedFile]:
        """
        Extrai todos os PDFs/OFX de um arquivo ZIP.
        
        Ignora arquivos que não sejam PDFs/OFX e arquivos em pastas
        que comecem com "__" (como __MACOSX).
        
        Args:
            zip_data: Bytes do arquivo ZIP
            
        Returns:
            Lista de arquivos PDF/OFX extraídos
            
        Raises:
            ValueError: Se o ZIP estiver corrompido ou vazio
        """
        try:
            zip_file = zipfile.ZipFile(io.BytesIO(zip_data))
        except zipfile.BadZipFile:
            raise ValueError("Arquivo ZIP corrompido ou inválido")
        
        extracted_files: list[ExtractedFile] = []
        
        for file_info in zip_file.filelist:
            # Ignora diretórios
            if file_info.is_dir():
                continue
            
            # Ignora arquivos em pastas de sistema (ex: __MACOSX)
            if file_info.filename.startswith("__"):
                continue
            
            # Ignora arquivos ocultos (começam com .)
            filename = file_info.filename.split("/")[-1]
            if filename.startswith("."):
                continue
            
            # Verifica se é PDF ou OFX pela extensão
            is_pdf = filename.lower().endswith(".pdf")
            is_ofx = filename.lower().endswith(".ofx")
            if not (is_pdf or is_ofx):
                logger.debug(f"Ignorando arquivo não suportado: {filename}")
                continue
            
            try:
                data = zip_file.read(file_info.filename)
                
                # Validação adicional para PDF
                if is_pdf and not data.startswith(b"%PDF-"):
                    logger.warning(
                        f"Arquivo {filename} tem extensão .pdf mas não é um PDF válido"
                    )
                    continue
                
                extracted_files.append(ExtractedFile(
                    filename=filename,
                    data=data
                ))
                
                logger.info(f"Arquivo extraído do ZIP: {filename}")
                
            except Exception as e:
                logger.error(f"Erro ao extrair {filename}: {e}")
                continue
        
        if not extracted_files:
            raise ValueError("Nenhum arquivo PDF/OFX encontrado no ZIP")
        
        logger.info(f"Total de arquivos extraídos: {len(extracted_files)}")
        return extracted_files
    
    def is_valid_zip(self, data: bytes) -> bool:
        """
        Verifica se os dados representam um ZIP válido.
        
        Args:
            data: Bytes do arquivo
            
        Returns:
            True se for um ZIP válido
        """
        # ZIP começa com PK (0x50 0x4B)
        if not data.startswith(b"PK"):
            return False
        
        try:
            zipfile.ZipFile(io.BytesIO(data))
            return True
        except zipfile.BadZipFile:
            return False
