"""
Utilitários para manipulação e normalização de texto.

Funções para limpar e padronizar textos extraídos de documentos.
"""

import re
import unicodedata


def normalize_text(text: str) -> str:
    """
    Normaliza texto removendo acentos e caracteres especiais.
    
    Útil para comparações mais flexíveis.
    
    Args:
        text: Texto original
        
    Returns:
        Texto normalizado em uppercase sem acentos
    """
    if not text:
        return ""
    
    # Remove acentos
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    
    # Converte para uppercase
    text = text.upper()
    
    # Remove caracteres especiais mantendo espaços e alfanuméricos
    text = re.sub(r"[^\w\s]", " ", text)
    
    # Remove espaços múltiplos
    text = re.sub(r"\s+", " ", text).strip()
    
    return text


def extract_cnpj(text: str) -> str | None:
    """
    Extrai CNPJ do texto.
    
    Procura por padrões de CNPJ com ou sem formatação.
    
    Args:
        text: Texto para buscar o CNPJ
        
    Returns:
        CNPJ encontrado (apenas números) ou None
    """
    if not text:
        return None
    
    # Padrão: XX.XXX.XXX/XXXX-XX ou apenas 14 dígitos seguidos
    patterns = [
        r"\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}",  # Com formatação
        r"\d{14}",  # Sem formatação
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            # Remove tudo exceto números
            cnpj = re.sub(r"\D", "", match.group())
            if len(cnpj) == 14:
                return cnpj
    
    return None


def extract_numbers(text: str) -> str:
    """
    Extrai apenas números do texto.
    
    Útil para comparar agência/conta sem formatação.
    
    Args:
        text: Texto original
        
    Returns:
        Apenas os dígitos do texto
    """
    if not text:
        return ""
    return re.sub(r"\D", "", text)


def format_cnpj(cnpj: str) -> str:
    """
    Formata CNPJ para o padrão XX.XXX.XXX/XXXX-XX.
    
    Args:
        cnpj: CNPJ apenas com números (14 dígitos)
        
    Returns:
        CNPJ formatado
    """
    cnpj = extract_numbers(cnpj)
    if len(cnpj) != 14:
        return cnpj
    
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
