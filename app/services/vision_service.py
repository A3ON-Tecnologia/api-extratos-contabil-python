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

# Prompt para OCR + identificacao de banco
VISION_OCR_PROMPT = """Extraia o texto visivel desta imagem de documento bancario e identifique o banco com base no texto.

Regras:
- Procure termos como "Banco do Brasil", "BB", "SICREDI", "SICOOB", "CRESOL", "CAIXA", "BRADESCO", "ITAU", "SANTANDER", etc.
- Se encontrar um indicio claro, retorne APENAS o nome do banco em UPPERCASE.
- Se nao houver indicio suficiente, retorne apenas: "DESCONHECIDO"

Responda APENAS com o nome do banco, sem explicacoes adicionais."""

# Prompt para OCR de texto (cabecalho)
VISION_TEXT_PROMPT = """Extraia o texto visivel desta imagem de documento bancario.

Regras:
- Retorne o texto bruto exatamente como aparece (sem explicar).
- Preserve linhas quando possivel.
- Se nao houver texto legivel, retorne apenas: "DESCONHECIDO"
"""

# Prompt para extrair numero de contrato de emprestimo
VISION_CONTRACT_PROMPT = """Extraia o NUMERO DO CONTRATO visivel nesta imagem de documento.

Regras:
- Procure o campo "Numero do Contrato" ou "Nº do Contrato"
- O numero costuma estar na mesma linha do rótulo, abaixo do nome do cliente
- Retorne APENAS o numero (somente digitos e/ou separadores como - ou /)
- Se nao encontrar, retorne apenas: "DESCONHECIDO"
"""


class VisionService:
    """Servico de identificacao de logos usando visao computacional."""

    def __init__(self):
        """Inicializa o serviço."""
        settings = get_settings()
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.llm_model

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
    def identify_bank_from_ocr(self, pdf_data: bytes, max_pages: int = 1) -> str | None:
        """
        Identifica o banco a partir do texto visivel (OCR) nas paginas do PDF.

        Args:
            pdf_data: Bytes do arquivo PDF
            max_pages: Numero maximo de paginas para analisar (padrao: 1)

        Returns:
            Nome do banco identificado ou None
        """
        try:
            logger.info("Iniciando identificacao de banco por OCR...")

            pdf_document = fitz.open(stream=pdf_data, filetype="pdf")
            for page_num in range(min(max_pages, len(pdf_document))):
                page = pdf_document[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_data = pix.tobytes("png")

                banco = self._identify_from_image_ocr(img_data)
                if banco and banco != "DESCONHECIDO":
                    logger.info(f"Banco identificado por OCR: {banco}")
                    pdf_document.close()
                    return banco

            pdf_document.close()
            logger.warning("Nao foi possivel identificar o banco via OCR")
            return None
        except Exception as e:
            logger.error(f"Erro ao identificar banco por OCR: {e}")
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

    def _identify_from_image_ocr(self, image_data: bytes) -> str | None:
        """
        Identifica o banco a partir do texto visivel na imagem (OCR via modelo).
        """
        try:
            base64_image = base64.b64encode(image_data).decode('utf-8')
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_OCR_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}",
                                    "detail": "low"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=50,
                temperature=0,
            )

            banco = response.choices[0].message.content.strip().upper()
            if banco and banco != "DESCONHECIDO":
                return banco
            return None
        except Exception as e:
            logger.error(f"Erro ao processar OCR com visao: {e}")
            return None

    def extract_contract_number_from_pdf(self, pdf_data: bytes, max_pages: int = 1) -> str | None:
        """
        Extrai o numero do contrato a partir do texto visivel (OCR) nas paginas do PDF.
        """
        try:
            logger.info("Iniciando OCR para numero do contrato...")

            pdf_document = fitz.open(stream=pdf_data, filetype="pdf")
            for page_num in range(min(max_pages, len(pdf_document))):
                page = pdf_document[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_data = pix.tobytes("png")

                # Tenta primeiro na area superior esquerda (onde fica o cabecalho)
                cropped = self._crop_image_bytes(img_data, left=0.0, top=0.0, right=0.6, bottom=0.45)
                contrato = self._extract_contract_from_image(cropped)
                if contrato and contrato != "DESCONHECIDO":
                    logger.info(f"Contrato identificado por OCR (recorte): {contrato}")
                    pdf_document.close()
                    return contrato

                # Fallback: tenta na pagina inteira
                contrato = self._extract_contract_from_image(img_data)
                if contrato and contrato != "DESCONHECIDO":
                    logger.info(f"Contrato identificado por OCR (pagina inteira): {contrato}")
                    pdf_document.close()
                    return contrato

            pdf_document.close()
            logger.warning("Nao foi possivel identificar contrato via OCR")
            return None
        except Exception as e:
            logger.error(f"Erro ao identificar contrato por OCR: {e}")
            return None

    def extract_header_text_from_pdf(self, pdf_data: bytes, max_pages: int = 1) -> str | None:
        """
        Extrai texto do cabecalho (OCR) das primeiras paginas do PDF.
        """
        try:
            logger.info("Iniciando OCR para texto do cabecalho...")
            pdf_document = fitz.open(stream=pdf_data, filetype="pdf")
            for page_num in range(min(max_pages, len(pdf_document))):
                page = pdf_document[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_data = pix.tobytes("png")

                # Prioriza a parte superior da pagina (cabecalho)
                cropped = self._crop_image_bytes(img_data, left=0.0, top=0.0, right=1.0, bottom=0.5)
                header_text = self._extract_text_from_image(cropped)
                if header_text:
                    pdf_document.close()
                    return header_text

                # Fallback: pagina inteira
                header_text = self._extract_text_from_image(img_data)
                if header_text:
                    pdf_document.close()
                    return header_text

            pdf_document.close()
            logger.warning("Nao foi possivel extrair texto do cabecalho via OCR")
            return None
        except Exception as e:
            logger.error(f"Erro ao extrair texto do cabecalho por OCR: {e}")
            return None

    def _extract_contract_from_image(self, image_data: bytes) -> str | None:
        """Extrai numero do contrato a partir de uma imagem."""
        try:
            base64_image = base64.b64encode(image_data).decode('utf-8')
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_CONTRACT_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}",
                                    "detail": "high"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=100,
                temperature=0,
            )

            contrato = response.choices[0].message.content.strip()
            return contrato
        except Exception as e:
            logger.error(f"Erro ao extrair contrato via visao: {e}")
            return None

    def _extract_text_from_image(self, image_data: bytes) -> str | None:
        """Extrai texto bruto a partir de uma imagem."""
        try:
            base64_image = base64.b64encode(image_data).decode('utf-8')
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_TEXT_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}",
                                    "detail": "low"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=400,
                temperature=0,
            )

            text = response.choices[0].message.content.strip()
            if not text or text.strip().upper() == "DESCONHECIDO":
                return None
            return text
        except Exception as e:
            logger.error(f"Erro ao extrair texto via visao: {e}")
            return None

    def _crop_image_bytes(
        self,
        image_data: bytes,
        *,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> bytes:
        """Recorta a imagem usando porcentagens (0-1) e retorna bytes PNG."""
        try:
            image = Image.open(BytesIO(image_data))
            width, height = image.size
            crop_box = (
                int(width * left),
                int(height * top),
                int(width * right),
                int(height * bottom),
            )
            cropped = image.crop(crop_box)
            buffer = BytesIO()
            cropped.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Erro ao processar OCR com visao: {e}")
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
