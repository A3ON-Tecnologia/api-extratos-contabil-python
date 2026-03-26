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
    
    def extract_text(self, pdf_data: bytes | BinaryIO, filename: str = "", max_pages: int | None = None) -> str:
        """
        Extrai texto de um arquivo (PDF, CSV, OFX, Excel).

        Args:
            max_pages: Limita a extração às primeiras N páginas do PDF.
                       None = todas as páginas.
        """
        # Converte bytes para file-like se necessário
        if isinstance(pdf_data, bytes):
            pdf_data = io.BytesIO(pdf_data)

        # Garante que a posição do stream está no início antes de qualquer leitura
        pdf_data.seek(0)

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
        text = self._extract_with_pdfplumber(pdf_data, max_pages=max_pages)

        # Se pdfplumber não extraiu nada, tenta PyPDF
        if not text.strip():
            pdf_data.seek(0)
            text = self._extract_with_pypdf(pdf_data, max_pages=max_pages)

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

    def _extract_with_pdfplumber(self, pdf_file: BinaryIO, max_pages: int | None = None) -> str:
        """
        Extrai texto usando pdfplumber.

        Melhor para PDFs com tabelas e layouts complexos.
        """
        try:
            text_parts = []

            with pdfplumber.open(pdf_file) as pdf:
                pages = pdf.pages[:max_pages] if max_pages else pdf.pages
                for page in pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

            return "\n\n".join(text_parts)

        except Exception as e:
            logger.warning(f"Erro ao extrair com pdfplumber: {e}")
            return ""

    def _extract_with_pypdf(self, pdf_file: BinaryIO, max_pages: int | None = None) -> str:
        """
        Extrai texto usando PyPDF.

        Fallback mais robusto para PDFs problemáticos.
        """
        try:
            text_parts = []

            reader = PdfReader(pdf_file)
            pages = reader.pages[:max_pages] if max_pages else reader.pages
            for page in pages:
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

    def extract_first_page_images(self, pdf_data: bytes | BinaryIO) -> list[str]:
        """
        Extrai as imagens da primeira página do PDF e retorna como base64.
        Útil para identificar logos de bancos quando não há texto.
        """
        import base64
        
        if isinstance(pdf_data, bytes):
            pdf_data = io.BytesIO(pdf_data)
        
        pdf_data.seek(0)
        images_base64 = []
        
        try:
            reader = PdfReader(pdf_data)
            if len(reader.pages) > 0:
                page = reader.pages[0]
                # Pega as 3 maiores imagens (logos geralmente são médias/grandes, mas ícones são pequenos, vamos pegar tudo mas limitar qtd)
                # Na verdade, logos de banco em cabeçalho costumam ser a primeira ou segunda imagem.
                
                count = 0
                for image_file_object in page.images:
                    if count >= 3: break
                    
                    # Converte para base64
                    img_b64 = base64.b64encode(image_file_object.data).decode('utf-8')
                    images_base64.append(img_b64)
                    count += 1
                    
        except Exception as e:
            logger.warning(f"Erro ao extrair imagens do PDF: {e}")
            
        return images_base64
