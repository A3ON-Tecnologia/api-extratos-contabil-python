# Extratos Contábeis - Sistema de Automação Documental

Sistema Python/FastAPI para processamento automático de extratos bancários e documentos contábeis.

## Funcionalidades

- 📄 **Recebimento de arquivos** via HTTP POST (PDF ou ZIP)
- 🔍 **Extração de texto** de PDFs usando pdfplumber/PyPDF
- 🤖 **Análise via LLM** (OpenAI GPT-4o-mini) para identificar cliente, banco, período
- 🎯 **Matching inteligente** de clientes (CNPJ, conta/agência, similaridade de nome)
- 📁 **Organização automática** na estrutura de pastas `J:\JP Digital\...`
- 📊 **Log de auditoria** em planilha Excel

## Estrutura do Projeto

```
extratos-contabil-python/
├── app/
│   ├── main.py              # API FastAPI
│   ├── config.py            # Configurações
│   ├── schemas/             # Schemas Pydantic
│   │   ├── llm_response.py  # Retorno da LLM
│   │   ├── api.py           # Request/Response
│   │   └── client.py        # Cliente e Match
│   ├── services/            # Serviços de negócio
│   │   ├── pdf_service.py   # Extração de PDF
│   │   ├── zip_service.py   # Extração de ZIP
│   │   ├── llm_service.py   # Integração LLM
│   │   ├── client_service.py    # Leitura de clientes
│   │   ├── matching_service.py  # Matching
│   │   ├── storage_service.py   # Salvamento
│   │   └── audit_service.py     # Log Excel
│   └── utils/               # Utilitários
│       ├── hash.py          # Hash para idempotência
│       └── text.py          # Normalização de texto
├── tests/                   # Testes
├── .env.example             # Exemplo de configuração
├── pyproject.toml           # Dependências
└── README.md
```

## Instalação

1. **Clone o projeto** e entre no diretório:
   ```bash
   cd extratos-contabil-python
   ```

2. **Crie um ambiente virtual**:
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   ```

3. **Instale as dependências**:
   ```bash
   pip install -e .
   ```

4. **Configure as variáveis de ambiente**:
   ```bash
   copy .env.example .env
   # Edite o .env com sua chave da OpenAI
   ```

## Configuração

Edite o arquivo `.env`:

```env
# API Key da OpenAI
OPENAI_API_KEY=sk-...

# Caminhos
BASE_PATH=J:\JP Digital
CLIENTS_EXCEL_PATH=J:\JP Digital\000 - AUTOMAÇÕES\RELAÇÃO CLIENTES - CONTA _ AGÊNCIA.xlsx
LOG_EXCEL_PATH=J:\JP Digital\000 - AUTOMAÇÕES\LOGS SUCESSO _ FALHA.xlsx

# Threshold de similaridade para match por nome (0-100)
SIMILARITY_THRESHOLD=85
```

## Uso

### Iniciar o servidor

```bash
uvicorn app.main:app --reload --port 8000
```

Ou execute diretamente:
```bash
python -m app.main
```

### Endpoints

#### `POST /upload`
Recebe um arquivo PDF ou ZIP para processamento.

**Request:**
```bash
curl -X POST "http://localhost:8000/upload" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@extrato.pdf"
```

**Response:**
```json
{
  "sucesso": true,
  "total_arquivos": 1,
  "arquivos_sucesso": 1,
  "arquivos_nao_identificados": 0,
  "arquivos_falha": 0,
  "resultados": [
    {
      "nome_arquivo_original": "extrato.pdf",
      "nome_arquivo_final": "J:\\JP Digital\\098 - JP CONTABIL LTDA\\...",
      "status": "SUCESSO",
      "cliente_identificado": "JP CONTABIL LTDA",
      "metodo_identificacao": "cnpj",
      "tipo_documento": "extrato bancário",
      "ano": 2024,
      "mes": 12
    }
  ]
}
```

#### `GET /health`
Health check do servidor.

#### `POST /reload-clients`
Força recarga da planilha de clientes.

#### `POST /merge-fallback-logs`
Mescla logs de fallback no arquivo principal.

### Documentação Interativa

Após iniciar o servidor, acesse:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Integração com Make

No Make, configure um módulo HTTP com:

- **URL**: `http://seu-servidor:8000/upload`
- **Method**: `POST`
- **Body type**: `Multipart/form-data`
- **Field name**: `file`
- **File**: Arquivo do módulo de e-mail

## Processo de Identificação

A identificação do cliente segue esta ordem de prioridade:

1. **CNPJ exato** - Match direto pelo CNPJ encontrado no documento
2. **Agência + Conta + Banco** - Combinação encontrada na planilha
3. **Nome com similaridade** - Usando rapidfuzz com threshold configurável

Se nenhum método identificar o cliente, o arquivo é salvo em `NAO_IDENTIFICADOS`.

## Estrutura de Pastas

```
J:\JP Digital\
├── 098 - JP CONTABIL LTDA\
│   └── Departamento Contabil\
│       └── 2024\
│           └── JANEIRO\
│               └── 098_2024_01.pdf
├── NAO_IDENTIFICADOS\
│   └── 2024\
│       └── JANEIRO\
│           └── documento_original.pdf
└── 000 - AUTOMAÇÕES\
    ├── RELAÇÃO CLIENTES - CONTA _ AGÊNCIA.xlsx
    └── LOGS SUCESSO _ FALHA.xlsx
```

## Planilha de Clientes

A planilha deve ter as seguintes colunas:

| COD | NOME | CNPJ | BANCO | AGENCIA | Nº CONTA |
|-----|------|------|-------|---------|----------|
| 098 | JP CONTABIL LTDA | 12.345.678/0001-90 | BRADESCO | 1234 | 12345-6 |

## Planilha de LOG

Registro automático com as colunas:

| DATA | NOME DO CLIENTE | TIPO EXTRATO | ANO | MÊS | STATUS | NOME ARQUIVO FINAL |
|------|-----------------|--------------|-----|-----|--------|-------------------|
| 13/01/2024 14:30:00 | JP CONTABIL LTDA | extrato bancário | 2024 | 1 | SUCESSO | J:\...\098_2024_01.pdf |

## Licença

Uso interno.
