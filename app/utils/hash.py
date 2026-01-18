"""
Utilitários para geração de hash.

Usado para idempotência e identificação única de arquivos.
"""

import hashlib


def compute_hash(data: bytes) -> str:
    """
    Calcula o hash SHA256 dos dados.
    
    Args:
        data: Bytes do arquivo
        
    Returns:
        Hash SHA256 em formato hexadecimal (64 caracteres)
    """
    return hashlib.sha256(data).hexdigest()


def short_hash(data: bytes, length: int = 8) -> str:
    """
    Calcula um hash curto dos dados.
    
    Útil para sufixos de nomes de arquivos.
    
    Args:
        data: Bytes do arquivo
        length: Quantidade de caracteres do hash (default: 8)
        
    Returns:
        Primeiros N caracteres do hash SHA256
    """
    return compute_hash(data)[:length]
