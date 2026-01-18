"""
Serviço de extração de texto de arquivos PDF.

Utiliza pdfplumber como extrator principal com fallback para PyPDF.
"""

import io
import logging
from typing import BinaryIO

import pdfplumber
from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PDFService:
    """Serviço para extração de texto de PDFs."""
    
    def extract_text(self, pdf_data: bytes | BinaryIO, filename: str = "") -> str:
        """
        Extrai texto de um arquivo (PDF, CSV, OFX, Excel).
        """
        # Converte bytes para file-like se necessário
        if isinstance(pdf_data, bytes):
            pdf_data = io.BytesIO(pdf_data)
        
        # Detecção por extensão
        ext = ""
        if filename:
            ext = "." + filename.split(".")[-1].lower() if "." in filename else ""
        
        # Despacha para o extrator correto
        if ext in [".xlsx", ".xls", ".ods"]:
            return self._extract_from_excel(pdf_data)
        elif ext in [".csv", ".txt", ".ofx", ".html", ".xml", ".json"]:
            return self._extract_from_text(pdf_data)
        
        # Padrão: Tenta como PDF
        text = self._extract_with_pdfplumber(pdf_data)
        
        # Se pdfplumber não extraiu nada, tenta PyPDF
        if not text.strip():
            pdf_data.seek(0)
            text = self._extract_with_pypdf(pdf_data)
        
        # Se ainda falhar, e for uma extensão desconhecida, tenta ler como texto puro
        if not text.strip():
             try:
                 pdf_data.seek(0)
                 return self._extract_from_text(pdf_data)
             except:
                 pass
        
        if not text.strip():
            raise ValueError("Não foi possível extrair texto do arquivo")
        
        return self._normalize_text(text)

    def _extract_from_excel(self, file_data: BinaryIO) -> str:
        """Extrai texto de todos as abas de um Excel."""
        try:
            import pandas as pd
            dfs = pd.read_excel(file_data, sheet_name=None)
            text_parts = []
            
            for sheet_name, df in dfs.items():
                text_parts.append(f"--- Aba: {sheet_name} ---")
                # Converte para string CSV-like para o LLM entender a estrutura
                text_parts.append(df.to_string(index=False))
            
            return "\n\n".join(text_parts)
        except Exception as e:
            logger.error(f"Erro ao ler Excel: {e}")
            return ""

    def _extract_from_text(self, file_data: BinaryIO) -> str:
        """Lê arquivos de texto puro (CSV, OFX, etc)."""
        try:
            content = file_data.read()
            # Tenta decodificar com utf-8, depois latin-1
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                return content.decode('latin-1')
        except Exception as e:
            logger.error(f"Erro ao ler texto: {e}")
            return ""

    def _extract_with_pdfplumber(self, pdf_file: BinaryIO) -> str:
        """
        Extrai texto usando pdfplumber.
        
        Melhor para PDFs com tabelas e layouts complexos.
        """
        try:
            text_parts = []
            
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            
            return "\n\n".join(text_parts)
            
        except Exception as e:
            logger.warning(f"Erro ao extrair com pdfplumber: {e}")
            return ""
    
    def _extract_with_pypdf(self, pdf_file: BinaryIO) -> str:
        """
        Extrai texto usando PyPDF.
        
        Fallback mais robusto para PDFs problemáticos.
        """
        try:
            text_parts = []
            
            reader = PdfReader(pdf_file)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            
            return "\n\n".join(text_parts)
            
        except Exception as e:
            logger.warning(f"Erro ao extrair com PyPDF: {e}")
            return ""
    
    def _normalize_text(self, text: str) -> str:
        """
        Normaliza o texto extraído.
        
        Remove quebras de linha excessivas e espaços duplicados.
        """
        # Remove linhas em branco excessivas
        lines = text.split("\n")
        normalized_lines = []
        prev_empty = False
        
        for line in lines:
            line = line.strip()
            is_empty = not line
            
            # Pula linhas em branco consecutivas
            if is_empty and prev_empty:
                continue
            
            normalized_lines.append(line)
            prev_empty = is_empty
        
        return "\n".join(normalized_lines)
    
    def is_valid_pdf(self, data: bytes) -> bool:
        """
        Verifica se os dados representam um PDF válido.
        
        Args:
            data: Bytes do arquivo
            
        Returns:
            True se for um PDF válido
        """
        # PDF começa com %PDF-
        if not data.startswith(b"%PDF-"):
            return False
        
        try:
            reader = PdfReader(io.BytesIO(data))
            # Tenta acessar o número de páginas para validar
            _ = len(reader.pages)
            return True
        except Exception:
            return False
