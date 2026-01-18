"""
Testes de integração da API.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
def client():
    """Fixture do cliente de teste."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestHealthCheck:
    """Testes do health check."""
    
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        """Testa endpoint de health check."""
        async with client as ac:
            response = await ac.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


class TestRoot:
    """Testes do endpoint raiz."""
    
    @pytest.mark.asyncio
    async def test_root(self, client):
        """Testa endpoint raiz."""
        async with client as ac:
            response = await ac.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Extratos Contábeis API"
        assert "version" in data


class TestUpload:
    """Testes do endpoint de upload."""
    
    @pytest.mark.asyncio
    async def test_upload_empty_file(self, client):
        """Testa upload de arquivo vazio."""
        async with client as ac:
            response = await ac.post(
                "/upload",
                files={"file": ("test.pdf", b"", "application/pdf")},
            )
        
        assert response.status_code == 400
        assert "vazio" in response.json()["detail"].lower()
    
    @pytest.mark.asyncio
    async def test_upload_invalid_file_type(self, client):
        """Testa upload de tipo inválido."""
        async with client as ac:
            response = await ac.post(
                "/upload",
                files={"file": ("test.txt", b"Hello World", "text/plain")},
            )
        
        assert response.status_code == 400
        assert "não suportado" in response.json()["detail"].lower()
