"""
Serviço de visão para identificar logos de bancos em PDFs.

Usa a API Vision da OpenAI para identificar logos quando o texto não consegue.
"""

import base64
import logging
from io import BytesIO

import fitz  # PyMuPDF
from PIL import Image
from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

# Prompt para identificação de logos
VISION_PROMPT = """Analise esta imagem de um documento bancário e identifique o nome do banco pela logo ou marca visual.

Procure por:
- Logos de bancos
- Marcas registradas
- Símbolos bancários
- Cores características do banco

Retorne APENAS o nome do banco em UPPERCASE, de forma simplificada:
- SICREDI
- SICOOB
- CRESOL (cooperativa de crédito, diferente de SICOOB)
- BRADESCO
- ITAU
- CAIXA
- BANCO DO BRASIL
- SANTANDER
- INTER
- NUBANK
- C6 BANK
- BTG PACTUAL
- SAFRA
- BANRISUL
- etc.

Se não conseguir identificar nenhum banco, retorne apenas: "DESCONHECIDO"

Responda APENAS com o nome do banco, sem explicações adicionais."""


class VisionService:
    """Serviço de identificação de logos usando visão computacional."""

    def __init__(self):
        """Inicializa o serviço."""
        settings = get_settings()
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = "gpt-4o-mini"  # Modelo com suporte a visão

    def identify_bank_from_pdf(self, pdf_data: bytes, max_pages: int = 3) -> str | None:
        """
        Identifica o banco a partir da logo no PDF.

        Extrai imagens das primeiras páginas e tenta identificar o banco.

        Args:
            pdf_data: Bytes do arquivo PDF
            max_pages: Número máximo de páginas para analisar (padrão: 3)

        Returns:
            Nome do banco identificado ou None
        """
        try:
            logger.info("Iniciando identificação de banco por visão...")

            # Abre o PDF
            pdf_document = fitz.open(stream=pdf_data, filetype="pdf")

            # Analisa as primeiras páginas
            for page_num in range(min(max_pages, len(pdf_document))):
                logger.debug(f"Analisando página {page_num + 1}...")

                page = pdf_document[page_num]

                # Renderiza a página como imagem
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom para melhor qualidade
                img_data = pix.tobytes("png")

                # Tenta identificar o banco nesta página
                banco = self._identify_from_image(img_data)

                if banco and banco != "DESCONHECIDO":
                    logger.info(f"Banco identificado por visão: {banco}")
                    pdf_document.close()
                    return banco

            pdf_document.close()
            logger.warning("Não foi possível identificar o banco através da visão")
            return None

        except Exception as e:
            logger.error(f"Erro ao identificar banco por visão: {e}")
            return None

    def _identify_from_image(self, image_data: bytes) -> str | None:
        """
        Identifica o banco a partir de uma imagem.

        Args:
            image_data: Bytes da imagem (PNG/JPEG)

        Returns:
            Nome do banco ou None
        """
        try:
            # Converte para base64
            base64_image = base64.b64encode(image_data).decode('utf-8')

            # Chama a API Vision da OpenAI
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}",
                                    "detail": "low"  # Usa resolução baixa para economizar tokens
                                },
                            },
                        ],
                    }
                ],
                max_tokens=50,
                temperature=0,
            )

            # Extrai o nome do banco
            banco = response.choices[0].message.content.strip().upper()

            if banco and banco != "DESCONHECIDO":
                logger.info(f"Banco identificado: {banco}")
                return banco

            return None

        except Exception as e:
            logger.error(f"Erro ao processar imagem com visão: {e}")
            return None

    def identify_from_first_page(self, pdf_data: bytes) -> str | None:
        """
        Versão rápida que analisa apenas a primeira página.

        Args:
            pdf_data: Bytes do arquivo PDF

        Returns:
            Nome do banco identificado ou None
        """
        return self.identify_bank_from_pdf(pdf_data, max_pages=1)
