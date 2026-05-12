from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pyodbc
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from pydantic import BaseModel, Field

try:
    import google.generativeai as genai
except Exception:
    genai = None

# =========================================================
# CONFIG
# =========================================================
SECRET_KEY = os.getenv("APP_SECRET_KEY", "TROQUE_ESTA_CHAVE")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.getenv("APP_TOKEN_EXPIRE_HOURS", "12"))

SQL_SERVER = os.getenv("SQL_SERVER", "172.16.137.100")
SQL_DATABASE = os.getenv("SQL_DATABASE", "sapiens")
SQL_USER = os.getenv("SQL_USER", "sapiens")
SQL_PASSWORD = os.getenv("SQL_PASSWORD",  "0n%lV'g0F94")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
EMPRESA_PADRAO = int(os.getenv("EMPRESA_PADRAO", "1"))

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8090"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_DISPONIVEL = bool(GEMINI_API_KEY and genai is not None)
if GEMINI_DISPONIVEL:
    genai.configure(api_key=GEMINI_API_KEY)

USERS = {
    "ADMIN": os.getenv("APP_USER_ADMIN", "123"),
    "RENATO": os.getenv("APP_USER_RENATO", "123"),
    "TRIBUTOS": os.getenv("APP_USER_TRIBUTOS", "123456"),
}

SESSOES: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------
# CACHE DE CONTEXTO DO ERP (carregado na inicialização)
# ---------------------------------------------------------
ERP_CACHE: Dict[str, Any] = {
    "usu_ia_disponivel": False,    # True se o schema usu_ia foi encontrado
    "familias": [],               # [{codfam, desfam, deppad, qtd}]
    "origens": [],                # [{codori, desori, deppad, ctrsep, qtd}]
    "tipos_produto": {},          # {TIPPRO: qtd}
    "qtd_servicos": 0,
    "exemplos_uso_consumo": [],   # produtos ativos com CODFAM='CONSUM'
    "exemplos_ativos": [],        # amostra de produtos ativos
    "contexto_setor": [],         # usu_ia.CONTEXTO_CADASTRO (novo) ou USU_IA_CONTEXTO_SETOR (legado)
    "contexto_cadastro": [],      # usu_ia.CONTEXTO_CADASTRO completo
    "politica_reaproveitamento": [],  # usu_ia.POLITICA_REAPROVEITAMENTO
    "carregado_em": None,
    "erro_carga": None,
}

# ---------------------------------------------------------
# CONTEXTO MESTRE DA FÁBRICA (CENI)
# ---------------------------------------------------------
CONTEXTO_FABRICA = {
    "empresa": "CENI",
    "objetivo_assistente": "Guiar o usuario no cadastro correto de produtos e servicos, evitando duplicidade e respeitando o processo interno da fabrica.",
    "principios": [
        "Sempre buscar similares no ERP antes de sugerir novo cadastro",
        "Nunca repetir a mesma pergunta se o campo ja foi respondido",
        "Perguntar de forma natural, olhando para o processo interno",
        "Separar claramente produto de servico",
        "Sugerir familia, origem e unidade com base em similares e regras internas",
        "Marcar pendencias fiscais quando nao houver seguranca",
    ],
    "erp_tabelas": {
        "produto": "E075PRO",
        "derivacao": "E075DER",
        "familia": "E012FAM",
        "servico": "E080SER",
    },
}

# ---------------------------------------------------------
# DICIONÁRIO DE INTENÇÃO DA FÁBRICA
# ---------------------------------------------------------
DICIONARIO_INTENCAO: Dict[str, List[str]] = {
    "uso_consumo_manutencao": [
        "consumo interno", "manutencao", "manutenção", "ferramenta", "epi",
        "limpeza", "pneu", "peca de reposicao", "peça de reposição",
        "uso da maquina", "empilhadeira", "correia", "filtro", "rolamento",
    ],
    "uso_consumo_admin": [
        "papel", "caneta", "cartucho", "escritorio", "administrativo",
        "material de escritorio", "higiene",
    ],
    "materia_prima": [
        "chapa", "tubo", "perfil", "aco", "aço", "barra",
        "insumo", "material para fabricar", "bobina", "vergalhao",
    ],
    "produto_produzido": [
        "conjunto", "estrutura", "equipamento",
        "produto final", "item fabricado", "produto acabado",
    ],
    "servico": [
        "frete", "instalacao", "instalação", "manutencao terceirizada",
        "calibracao", "calibração", "consultoria", "locacao", "locação",
        "montagem", "servico", "serviço",
    ],
}

# Palavras-chave para detecção por texto
PALAVRAS_SERVICO = DICIONARIO_INTENCAO["servico"]
PALAVRAS_USO_CONSUMO = DICIONARIO_INTENCAO["uso_consumo_manutencao"] + DICIONARIO_INTENCAO["uso_consumo_admin"]
PALAVRAS_MATERIA_PRIMA = DICIONARIO_INTENCAO["materia_prima"]
PALAVRAS_PRODUZIDO = DICIONARIO_INTENCAO["produto_produzido"]

# ---------------------------------------------------------
# SCHEMA COMPLETO DE SLOTS
# ---------------------------------------------------------
DEFAULT_SLOTS: Dict[str, Any] = {
    "tipo_cadastro": None,
    "setor": None,
    "roteiro": None,
    "finalidade": None,
    "aplicacao_item": None,
    "equipamento": None,
    "descricao_base": None,
    "especificacao_principal": None,
    "medida": None,
    "unidade": None,
    "familia": None,
    "origem": None,
    "fornecedor": None,
    "ncm": None,
    "derivacao": None,
    "similar_escolhido": None,
    "motivo_rejeicao_similar": None,
    "caracteristica": None,
}

# ---------------------------------------------------------
# ROTEIROS POR PROCESSO
# ---------------------------------------------------------
ROTEIROS: Dict[str, List[str]] = {
    "uso_consumo_manutencao": [
        "equipamento",
        "finalidade",
        "especificacao_principal",
        "medida",
        "unidade",
        "familia",
        "origem",
        "fornecedor",
    ],
    "uso_consumo_admin": [
        "descricao_base",
        "finalidade",
        "unidade",
        "familia",
        "fornecedor",
    ],
    "materia_prima": [
        "descricao_base",
        "especificacao_principal",
        "medida",
        "unidade",
        "familia",
        "origem",
        "ncm",
    ],
    "produto_produzido": [
        "descricao_base",
        "familia",
        "origem",
        "unidade",
        "derivacao",
    ],
    "servico": [
        "descricao_base",
        "finalidade",
        "fornecedor",
        "familia",
    ],
}

# Perguntas contextuais por campo e roteiro
PERGUNTAS_ROTEIRO: Dict[str, Dict[str, str]] = {
    "uso_consumo_manutencao": {
        "equipamento": "Esse item é para qual equipamento, máquina ou área? Ex.: empilhadeira, torno, prensa.",
        "finalidade": "É item de consumo interno, reposição ou compra pontual?",
        "especificacao_principal": "Qual a especificação principal dele? Medida, modelo, aro ou capacidade?",
        "medida": "Qual a medida exata? Ex.: 650x10, 18mm, 1/2 pol.",
        "unidade": "Unidade de compra: UN, PC, KG, MT?",
        "familia": "Qual família esse item pertence no ERP? Se não souber, eu sugiro.",
        "origem": "Qual origem/categoria? Ex.: USO E CONSUMO, MANUTENÇÃO.",
        "fornecedor": "Existe fornecedor ou marca de referência?",
    },
    "uso_consumo_admin": {
        "descricao_base": "Qual é o nome exato do item?",
        "finalidade": "É consumo administrativo ou apoio operacional?",
        "unidade": "Unidade de compra? Ex.: UN, CX, PC.",
        "familia": "Família no ERP. Posso sugerir se não souber.",
        "fornecedor": "Existe marca ou fornecedor obrigatório?",
    },
    "materia_prima": {
        "descricao_base": "Qual o nome e formato do material? Ex.: CHAPA XADREZ, TUBO REDONDO.",
        "especificacao_principal": "Qual o formato principal? Chapa, tubo, perfil, barra?",
        "medida": "Qual a medida/bitola/espessura?",
        "unidade": "Unidade: KG, MT, PC, UN?",
        "familia": "Família no ERP.",
        "origem": "Origem do material.",
        "ncm": "Você tem o NCM ou classificação fiscal?",
    },
    "produto_produzido": {
        "descricao_base": "Qual é o nome do produto que será fabricado?",
        "familia": "A qual linha ou conjunto pertence?",
        "origem": "Origem do produto.",
        "unidade": "Unidade de produção?",
        "derivacao": "Esse item precisa de derivação? Se sim, qual?",
    },
    "servico": {
        "descricao_base": "Qual é a natureza do serviço?",
        "finalidade": "Esse serviço é tomado ou prestado?",
        "fornecedor": "Existe fornecedor ou prestador recorrente?",
        "familia": "Família do serviço no ERP.",
    },
}

# Fallback genérico
PERGUNTAS: Dict[str, str] = {
    "tipo_cadastro": "Esse item será cadastrado como uso e consumo, matéria-prima, produto produzido ou serviço?",
    "descricao_base": "Como você quer descrever o item de forma objetiva?",
    "finalidade": "Qual a finalidade desse item no processo?",
    "equipamento": "Esse item é para qual equipamento ou área?",
    "especificacao_principal": "Qual a especificação principal? Medida, modelo ou capacidade?",
    "medida": "Qual a medida, tamanho, espessura ou especificação principal?",
    "unidade": "Qual a unidade de medida? Ex.: UN, KG, MT, PC.",
    "familia": "Qual a família mais adequada para esse item? Se não souber, eu sugiro com base em similares.",
    "origem": "Qual a origem/categoria do item no seu padrão interno?",
    "ncm": "Você possui NCM ou classificação fiscal para esse item?",
    "derivacao": "Esse item precisa derivação? Se sim, qual?",
    "fornecedor": "Existe fornecedor ou marca de referência?",
    "aplicacao_item": "Onde ou em quê esse item será aplicado?",
}

TIPOS_CADASTRO = ["uso_consumo", "materia_prima", "produto_produzido", "servico"]
MAP_TIPO_LABEL = {
    "uso_consumo": "Uso e consumo",
    "materia_prima": "Matéria-prima",
    "produto_produzido": "Produto produzido",
    "servico": "Serviço",
}

CAMPOS_POR_TIPO = {
    "uso_consumo": ["descricao_base", "finalidade", "unidade", "familia"],
    "materia_prima": ["descricao_base", "medida", "unidade", "familia", "origem"],
    "produto_produzido": ["descricao_base", "familia", "origem", "unidade"],
    "servico": ["descricao_base", "finalidade", "familia"],
}

ETAPAS = [
    "Buscar similares no ERP",
    "Confirmar se item existente atende",
    "Coletar tipo, medida e finalidade",
    "Sugerir família, origem e unidade",
    "Validar regras fiscais",
    "Gerar proposta e relatório",
]

# ---------------------------------------------------------
# PROMPT DE SISTEMA — IA como analista interno CENI
# ---------------------------------------------------------
SYSTEM_PROMPT_CENI = """
Você é um analista interno de cadastro do CENI.

Sua função é guiar o usuário no cadastro correto de produtos e serviços no ERP Senior.
Você deve agir como alguém que conhece o processo interno da fábrica, não como um formulário genérico.

Contexto do ERP:
- Produtos e derivações ficam em E075PRO e E075DER
- Famílias em E012FAM (herança de parâmetros e contas)
- Serviços ficam em E080SER (trilha própria e separada)
- Validações fiscais incluem PIS/COFINS, CST e base de crédito

Regras obrigatórias:
1. Sempre procure similares antes de sugerir novo cadastro.
2. NUNCA repita uma pergunta já respondida — verifique o histórico.
3. Sempre trate a próxima resposta como preenchimento do campo em coleta (campo_em_coleta).
4. Faça perguntas naturais e curtas orientadas ao processo interno.
5. Se o tipo já foi informado, não pergunte novamente.
6. Se houver indício de manutenção, máquina interna ou empilhadeira → roteiro uso_consumo_manutencao.
7. Se houver indício de serviço → trilha servico (E080SER).
8. Sugira família, origem e unidade com base em similares encontrados.
9. Quando não houver segurança fiscal, sinalize pendência em vez de inventar.
10. Ao final, entregue proposta de cadastro completa em JSON + relatório.

Dicionário de intenção da fábrica (use para inferir contexto):
- uso_consumo_manutencao: empilhadeira, pneu, filtro, correia, rolamento, ferramenta, EPI, manutenção
- uso_consumo_admin: papel, caneta, cartucho, material de escritório
- materia_prima: chapa, tubo, perfil, aço, barra, bobina, insumo
- produto_produzido: conjunto, estrutura, equipamento fabricado
- servico: frete, instalação, calibração, consultoria, locação

Membrane de sessão que você DEVE respeitar:
- tipo_cadastro, roteiro_atual, campo_em_coleta, setor, equipamento
- Slots já preenchidos NÃO devem ser perguntados novamente
- Se campo_em_coleta está definido, a resposta do usuário preenche esse campo
"""

# =========================================================
# APP
# =========================================================
security = HTTPBearer(auto_error=False)
app = FastAPI(title="Cadastro Guiado de Produtos - ERP Senior/CENI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# MODELS
# =========================================================
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str


class IniciarCadastroRequest(BaseModel):
    texto_inicial: str = Field(..., min_length=2)
    empresa: int = EMPRESA_PADRAO


class ResponderCadastroRequest(BaseModel):
    sessao_id: str
    resposta: str = Field(..., min_length=1)


class ItemSimilar(BaseModel):
    codigo: str
    descricao: str
    familia: Optional[str] = None
    origem: Optional[str] = None
    tipo: Optional[str] = None
    tem_derivacao: Optional[bool] = None
    score: Optional[int] = None


class CadastroSugerido(BaseModel):
    tipo_cadastro: str
    codigo_sugerido: Optional[str] = None
    descricao: Optional[str] = None
    descricao_complementar: Optional[str] = None
    familia: Optional[str] = None
    origem: Optional[str] = None
    unidade: Optional[str] = None
    derivacao: Optional[str] = None
    ncm: Optional[str] = None
    finalidade: Optional[str] = None
    parametros_fiscais_sugeridos: List[str] = []
    pendencias: List[str] = []
    alertas: List[str] = []


class SessaoResponse(BaseModel):
    sessao_id: str
    etapa_atual: str
    mensagem: str
    pergunta: Optional[str] = None
    similares: List[ItemSimilar] = []
    cadastro_sugerido: Optional[CadastroSugerido] = None
    slots: Dict[str, Any] = {}
    progresso: int = 0
    roteiro_atual: Optional[str] = None
    campo_em_coleta: Optional[str] = None


class ValidacaoResponse(BaseModel):
    sessao_id: str
    status: str
    pendencias: List[str]
    alertas: List[str]
    cadastro_sugerido: CadastroSugerido


class RelatorioResponse(BaseModel):
    sessao_id: str
    relatorio_texto: str
    relatorio_json: Dict[str, Any]


# =========================================================
# AUTH
# =========================================================
def create_token(username: str) -> TokenResponse:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {"sub": username.upper(), "exp": expire}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return TokenResponse(access_token=token, expires_at=expire.isoformat())



def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Token não informado")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Token inválido")
        return str(username)
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")


# =========================================================
# DB
# =========================================================
def get_connection() -> pyodbc.Connection:
    try:
        return pyodbc.connect(
            f"DRIVER={{{SQL_DRIVER}}};"
            f"SERVER={SQL_SERVER},1433;"
            f"DATABASE={SQL_DATABASE};"
            f"UID={SQL_USER};"
            f"PWD={SQL_PASSWORD};"
            "Encrypt=no;"
            "TrustServerCertificate=yes;",
            timeout=20,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro conexão SQL: {exc}")



def row_to_dict(cursor: pyodbc.Cursor, row: pyodbc.Row) -> Dict[str, Any]:
    cols = [c[0] for c in cursor.description]
    out: Dict[str, Any] = {}
    for i, col in enumerate(cols):
        val = row[i]
        if isinstance(val, Decimal):
            val = float(val)
        elif isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, str):
            val = val.strip()
        out[col] = val
    return out



def execute_query(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [row_to_dict(cur, row) for row in rows]
    finally:
        conn.close()


def execute_query_safe(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    """Como execute_query mas retorna [] em vez de levantar exceção — para uso no cache."""
    try:
        return execute_query(sql, params)
    except Exception:
        return []


# =========================================================
# DIAGNÓSTICO DE BAIXO NÍVEL (SQL helpers)
# =========================================================
def db_scalar(sql: str, params: Tuple[Any, ...] = ()) -> Any:
    """Executa query retornando apenas o primeiro valor da primeira linha."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def object_exists_full(name: str, object_type: str) -> bool:
    """Verifica se um objeto existe no sys.objects pelo nome qualificado e tipo."""
    try:
        return db_scalar(
            "SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(?) AND type = ?",
            (name, object_type)
        ) == 1
    except Exception:
        return False


def table_exists_simple(name: str) -> bool:
    """Verifica se uma tabela base (sem schema) existe."""
    try:
        return db_scalar("SELECT 1 FROM sys.tables WHERE name = ?", (name,)) == 1
    except Exception:
        return False


def schema_exists(name: str) -> bool:
    """Verifica se um schema existe."""
    try:
        return db_scalar("SELECT 1 FROM sys.schemas WHERE name = ?", (name,)) == 1
    except Exception:
        return False


def safe_count(sql: str, params: Tuple[Any, ...] = ()) -> Dict[str, Any]:
    """Executa SELECT COUNT e retorna {ok, qtd, erro}."""
    try:
        qtd = db_scalar(sql, params)
        return {"ok": True, "qtd": int(qtd or 0), "erro": ""}
    except Exception as e:
        return {"ok": False, "qtd": 0, "erro": str(e)}


_OBJETOS_ERP_BASE = [
    ("E075PRO", "U"),
    ("E075DER", "U"),
    ("E012FAM", "U"),
    ("E083ORI", "U"),
    ("E080SER", "U"),
]

_OBJETOS_IA = [
    ("usu_ia.CONTEXTO_CADASTRO",            "U"),
    ("usu_ia.EXEMPLOS_CADASTRO",             "U"),
    ("usu_ia.SESSAO_CHATBOT",                "U"),
    ("usu_ia.SESSAO_CHATBOT_MSG",            "U"),
    ("usu_ia.POLITICA_REAPROVEITAMENTO",     "U"),
    ("usu_ia.VW_PRODUTOS_BASE",              "V"),
    ("usu_ia.VW_SERVICOS_BASE",              "V"),
    ("usu_ia.VW_DASH_TIPOS_CADASTRO",        "V"),
    ("usu_ia.VW_DASH_FAMILIAS",              "V"),
    ("usu_ia.VW_DASH_ORIGENS",               "V"),
    ("usu_ia.VW_ITENS_EXIGEM_REVISAO_FISCAL", "V"),
    ("usu_ia.VW_SIMILARES_PRODUTO",          "V"),
    ("usu_ia.VW_SIMILARES_SERVICO",          "V"),
]


def diagnostico_api_erp() -> Dict[str, Any]:
    """
    Diagnóstico completo: conexão SQL, tabelas do ERP, objetos usu_ia.*,
    contagens e testes funcionais de similares/família/origem/fiscal.
    Espelha a lógica do script usu_ia_conferencia.sql.
    """
    resultado: Dict[str, Any] = {
        "status_geral": "ok",
        "conexao_sql": {"ok": False, "erro": ""},
        "schema_usu_ia": False,
        "objetos_erp_base": [],
        "objetos_ia": [],
        "contagens": {},
        "testes_funcionais": {},
        "falhas": [],
    }

    # 1. Conexão SQL
    try:
        ping = db_scalar("SELECT 1")
        resultado["conexao_sql"]["ok"] = (ping == 1)
    except Exception as e:
        resultado["conexao_sql"]["erro"] = str(e)
        resultado["status_geral"] = "falhou"
        resultado["falhas"].append(f"Falha conexão SQL: {e}")
        return resultado

    # 2. Schema usu_ia
    resultado["schema_usu_ia"] = schema_exists("usu_ia")
    if not resultado["schema_usu_ia"]:
        resultado["falhas"].append("Schema usu_ia não encontrado — execute usu_ia_base_auxiliar.sql")

    # 3. Tabelas ERP base
    for nome, tipo in _OBJETOS_ERP_BASE:
        ok = table_exists_simple(nome)
        resultado["objetos_erp_base"].append({"objeto": nome, "tipo": tipo, "ok": ok})
        if not ok:
            resultado["falhas"].append(f"Tabela ERP não encontrada: {nome}")

    # 4. Objetos usu_ia
    for nome, tipo in _OBJETOS_IA:
        ok = object_exists_full(nome, tipo)
        resultado["objetos_ia"].append({"objeto": nome, "tipo": tipo, "ok": ok})
        if not ok:
            resultado["falhas"].append(f"Objeto IA não encontrado: {nome}")

    # 5. Contagens ERP base
    contagens: Dict[str, Any] = {
        "e075pro": safe_count("SELECT COUNT(*) FROM E075PRO"),
        "e075der": safe_count("SELECT COUNT(*) FROM E075DER"),
        "e012fam": safe_count("SELECT COUNT(*) FROM E012FAM"),
        "e083ori": safe_count("SELECT COUNT(*) FROM E083ORI"),
        "e080ser": safe_count("SELECT COUNT(*) FROM E080SER"),
    }

    # 5b. Contagens usu_ia (somente se existirem)
    _contagens_ia = [
        ("usu_ia.CONTEXTO_CADASTRO",         "U", "SELECT COUNT(*) FROM usu_ia.CONTEXTO_CADASTRO"),
        ("usu_ia.EXEMPLOS_CADASTRO",          "U", "SELECT COUNT(*) FROM usu_ia.EXEMPLOS_CADASTRO"),
        ("usu_ia.POLITICA_REAPROVEITAMENTO",  "U",
         "SELECT COUNT(*) FROM usu_ia.POLITICA_REAPROVEITAMENTO WHERE ATIVO='S'"),
        ("usu_ia.VW_PRODUTOS_BASE",           "V", "SELECT COUNT(*) FROM usu_ia.VW_PRODUTOS_BASE"),
        ("usu_ia.VW_SERVICOS_BASE",           "V", "SELECT COUNT(*) FROM usu_ia.VW_SERVICOS_BASE"),
        ("usu_ia.VW_ITENS_EXIGEM_REVISAO_FISCAL", "V",
         "SELECT COUNT(*) FROM usu_ia.VW_ITENS_EXIGEM_REVISAO_FISCAL"),
    ]
    for nome, tipo, sql in _contagens_ia:
        if object_exists_full(nome, tipo):
            contagens[nome] = safe_count(sql)

    resultado["contagens"] = contagens

    # 6. Testes funcionais
    testes: Dict[str, Any] = {}

    # 6.1 Famílias com produtos
    try:
        rows = execute_query(
            "SELECT TOP 5 F.CODFAM AS codigo, F.DESFAM AS descricao "
            "FROM E012FAM F "
            "WHERE F.CODEMP = ? "
            "  AND EXISTS (SELECT 1 FROM E075PRO P WHERE P.CODEMP=F.CODEMP AND P.CODFAM=F.CODFAM) "
            "ORDER BY F.CODFAM",
            (EMPRESA_PADRAO,)
        )
        testes["familias_com_produtos"] = {"ok": len(rows) > 0, "qtd": len(rows), "amostra": rows}
        if not rows:
            resultado["falhas"].append("Nenhuma família com produto retornada")
    except Exception as e:
        testes["familias_com_produtos"] = {"ok": False, "erro": str(e)}
        resultado["falhas"].append(f"Falha teste famílias: {e}")

    # 6.2 Origens com produtos
    try:
        rows = execute_query(
            "SELECT TOP 5 O.CODORI AS codigo, O.DESORI AS descricao "
            "FROM E083ORI O "
            "WHERE O.CODEMP = ? "
            "  AND EXISTS (SELECT 1 FROM E075PRO P WHERE P.CODEMP=O.CODEMP AND P.CODORI=O.CODORI) "
            "ORDER BY O.CODORI",
            (EMPRESA_PADRAO,)
        )
        testes["origens_com_produtos"] = {"ok": len(rows) > 0, "qtd": len(rows), "amostra": rows}
        if not rows:
            resultado["falhas"].append("Nenhuma origem com produto retornada")
    except Exception as e:
        testes["origens_com_produtos"] = {"ok": False, "erro": str(e)}
        resultado["falhas"].append(f"Falha teste origens: {e}")

    # 6.3 Similares produto (usa view se disponível, senão E075PRO direto)
    try:
        if object_exists_full("usu_ia.VW_SIMILARES_PRODUTO", "V"):
            rows = execute_query(
                "SELECT TOP 5 * FROM usu_ia.VW_SIMILARES_PRODUTO "
                "WHERE CODEMP=? "
                "  AND (UPPER(DESPRO) LIKE '%PNEU%' OR UPPER(DESPRO) LIKE '%EMPILHADEIRA%') "
                "ORDER BY DESPRO",
                (EMPRESA_PADRAO,)
            )
        else:
            rows = execute_query(
                "SELECT TOP 5 P.CODPRO AS codigo, P.DESPRO AS descricao, "
                "P.CODFAM AS familia, P.CODORI AS origem, P.UNIMED AS unidade "
                "FROM E075PRO P "
                "WHERE P.CODEMP=? "
                "  AND (UPPER(P.DESPRO) LIKE '%PNEU%' OR UPPER(P.DESPRO) LIKE '%EMPILHADEIRA%') "
                "ORDER BY P.DESPRO",
                (EMPRESA_PADRAO,)
            )
        testes["similar_produto_pneu"] = {"ok": len(rows) > 0, "qtd": len(rows), "amostra": rows}
    except Exception as e:
        testes["similar_produto_pneu"] = {"ok": False, "erro": str(e)}
        resultado["falhas"].append(f"Falha teste similares produto: {e}")

    # 6.4 Similares serviço (usa view se disponível, senão E080SER direto)
    try:
        if object_exists_full("usu_ia.VW_SIMILARES_SERVICO", "V"):
            rows = execute_query(
                "SELECT TOP 5 * FROM usu_ia.VW_SIMILARES_SERVICO "
                "WHERE CODEMP=? "
                "  AND (UPPER(DESSER) LIKE '%MANUTENCAO%' OR UPPER(DESSER) LIKE '%FRETE%') "
                "ORDER BY DESSER",
                (EMPRESA_PADRAO,)
            )
        else:
            rows = execute_query(
                "SELECT TOP 5 S.CODSER AS codigo, S.DESSER AS descricao, "
                "S.CODFAM AS familia, S.UNIMED AS unidade "
                "FROM E080SER S "
                "WHERE S.CODEMP=? "
                "  AND (UPPER(S.DESSER) LIKE '%MANUTENCAO%' OR UPPER(S.DESSER) LIKE '%FRETE%') "
                "ORDER BY S.DESSER",
                (EMPRESA_PADRAO,)
            )
        testes["similar_servico"] = {"ok": len(rows) > 0, "qtd": len(rows), "amostra": rows}
    except Exception as e:
        testes["similar_servico"] = {"ok": False, "erro": str(e)}
        resultado["falhas"].append(f"Falha teste similares serviço: {e}")

    # 6.5 Revisão fiscal
    try:
        if object_exists_full("usu_ia.VW_ITENS_EXIGEM_REVISAO_FISCAL", "V"):
            rows = execute_query(
                "SELECT TOP 5 * FROM usu_ia.VW_ITENS_EXIGEM_REVISAO_FISCAL ORDER BY TIPO_REGISTRO, CODIGO"
            )
            testes["revisao_fiscal"] = {"ok": True, "qtd": len(rows), "amostra": rows}
        else:
            testes["revisao_fiscal"] = {"ok": False, "erro": "View VW_ITENS_EXIGEM_REVISAO_FISCAL não existe"}
    except Exception as e:
        testes["revisao_fiscal"] = {"ok": False, "erro": str(e)}
        resultado["falhas"].append(f"Falha teste revisão fiscal: {e}")

    resultado["testes_funcionais"] = testes
    if resultado["falhas"]:
        resultado["status_geral"] = "atencao"

    return resultado


def carregar_contexto_erp(empresa: int = EMPRESA_PADRAO) -> None:
    """
    Carrega contexto do ERP em memória.
    Prioridade:
    1) usu_ia.* (views/tabelas do schema auxiliar, se existir)
    2) Fallback direto nas tabelas do ERP (E075PRO, E012FAM, E083ORI, E080SER)
    """
    try:
        # ── Detecta se o schema usu_ia foi criado ────────────────────────────
        rows_schema = execute_query_safe(
            "SELECT OBJECT_ID('usu_ia.SESSAO_CHATBOT', 'U') AS OID"
        )
        usu_ia_ok = bool(rows_schema and rows_schema[0].get("OID") or rows_schema[0].get("oid"))
        ERP_CACHE["usu_ia_disponivel"] = usu_ia_ok

        # ── Tipos de produto ─────────────────────────────────────────────────
        if usu_ia_ok:
            rows = execute_query_safe(
                """
                SELECT TIPO_CADASTRO_INFERIDO AS TIPPRO, COUNT(*) AS QTD
                FROM usu_ia.VW_SIMILARES_PRODUTO
                WHERE CODEMP = ? GROUP BY TIPO_CADASTRO_INFERIDO
                """,
                (empresa,),
            )
        else:
            rows = execute_query_safe(
                "SELECT TIPPRO, COUNT(*) AS QTD FROM E075PRO WHERE CODEMP = ? GROUP BY TIPPRO ORDER BY QTD DESC",
                (empresa,),
            )
        ERP_CACHE["tipos_produto"] = {
            clean_str(r.get("TIPPRO") or r.get("tippro")): int(r.get("QTD") or r.get("qtd") or 0)
            for r in rows
        }

        # ── Quantidade de serviços ────────────────────────────────────────────
        if usu_ia_ok:
            rows = execute_query_safe(
                "SELECT COUNT(*) AS QTD_SERVICOS FROM usu_ia.VW_SIMILARES_SERVICO WHERE CODEMP = ?",
                (empresa,),
            )
        else:
            rows = execute_query_safe(
                "SELECT COUNT(*) AS QTD_SERVICOS FROM E080SER WHERE CODEMP = ?",
                (empresa,),
            )
        ERP_CACHE["qtd_servicos"] = int(
            (rows[0].get("QTD_SERVICOS") or rows[0].get("qtd_servicos") or 0) if rows else 0
        )

        # ── Famílias (com DEPPAD de E012FAM) ─────────────────────────────────
        if usu_ia_ok:
            rows = execute_query_safe(
                """
                SELECT CODFAM, QTD_PRODUTOS AS QTD, DEPPAD
                FROM usu_ia.VW_DASH_FAMILIAS
                WHERE CODEMP = ?
                ORDER BY QTD DESC, CODFAM
                """,
                (empresa,),
            )
            ERP_CACHE["familias"] = [
                {
                    "codfam": clean_str(r.get("CODFAM") or r.get("codfam")),
                    "desfam": "",
                    "deppad": clean_str(r.get("DEPPAD") or r.get("deppad")),
                    "qtd": int(r.get("QTD") or r.get("qtd") or 0),
                }
                for r in rows
            ]
        else:
            rows = execute_query_safe(
                """
                SELECT F.CODFAM, F.DESFAM, F.DEPPAD, COUNT(P.CODPRO) AS QTD
                FROM E012FAM F
                LEFT JOIN E075PRO P ON P.CODEMP = F.CODEMP AND P.CODFAM = F.CODFAM
                WHERE F.CODEMP = ?
                GROUP BY F.CODFAM, F.DESFAM, F.DEPPAD
                ORDER BY QTD DESC, F.CODFAM
                """,
                (empresa,),
            )
            ERP_CACHE["familias"] = [
                {
                    "codfam": clean_str(r.get("CODFAM") or r.get("codfam")),
                    "desfam": clean_str(r.get("DESFAM") or r.get("desfam")),
                    "deppad": clean_str(r.get("DEPPAD") or r.get("deppad")),
                    "qtd": int(r.get("QTD") or r.get("qtd") or 0),
                }
                for r in rows
            ]

        # ── Origens (com CTRSEP de E083ORI) ──────────────────────────────────
        if usu_ia_ok:
            rows = execute_query_safe(
                """
                SELECT CODORI, QTD_PRODUTOS AS QTD, DEPPAD, CTRSEP
                FROM usu_ia.VW_DASH_ORIGENS
                WHERE CODEMP = ?
                ORDER BY QTD DESC, CODORI
                """,
                (empresa,),
            )
            ERP_CACHE["origens"] = [
                {
                    "codori": clean_str(r.get("CODORI") or r.get("codori")),
                    "desori": "",
                    "deppad": clean_str(r.get("DEPPAD") or r.get("deppad")),
                    "ctrsep": clean_str(r.get("CTRSEP") or r.get("ctrsep")),
                    "qtd": int(r.get("QTD") or r.get("qtd") or 0),
                    "exige_serie": clean_str(r.get("CTRSEP") or r.get("ctrsep")) == "S",
                }
                for r in rows
            ]
        else:
            rows = execute_query_safe(
                """
                SELECT O.CODORI, O.DESORI, O.DEPPAD, O.CTRSEP, COUNT(P.CODPRO) AS QTD
                FROM E083ORI O
                LEFT JOIN E075PRO P ON P.CODEMP = O.CODEMP AND P.CODORI = O.CODORI
                WHERE O.CODEMP = ?
                GROUP BY O.CODORI, O.DESORI, O.DEPPAD, O.CTRSEP
                ORDER BY QTD DESC, O.CODORI
                """,
                (empresa,),
            )
            ERP_CACHE["origens"] = [
                {
                    "codori": clean_str(r.get("CODORI") or r.get("codori")),
                    "desori": clean_str(r.get("DESORI") or r.get("desori")),
                    "deppad": clean_str(r.get("DEPPAD") or r.get("deppad")),
                    "ctrsep": clean_str(r.get("CTRSEP") or r.get("ctrsep")),
                    "qtd": int(r.get("QTD") or r.get("qtd") or 0),
                    "exige_serie": clean_str(r.get("CTRSEP") or r.get("ctrsep")) == "S",
                }
                for r in rows
            ]

        # ── Exemplos de uso e consumo ─────────────────────────────────────────
        if usu_ia_ok:
            rows = execute_query_safe(
                """
                SELECT TOP 200 CODPRO AS codpro, DESCRICAO AS despro, CODFAM AS codfam,
                               CODORI AS codori, '' AS tippro, UNIMED AS unimed
                FROM usu_ia.EXEMPLOS_CADASTRO
                WHERE EMPRESA = ? AND TIPO_CADASTRO = 'uso_consumo'
                  AND STATUS_EXEMPLO = 'APROVADO'
                ORDER BY DESCRICAO
                """,
                (empresa,),
            )
        else:
            rows = execute_query_safe(
                """
                SELECT TOP 200 P.CODPRO, P.DESPRO, P.CODFAM, P.CODORI, P.TIPPRO, P.UNIMED
                FROM E075PRO P
                WHERE P.CODEMP = ? AND P.SITPRO = 'A'
                  AND (P.CODFAM = 'CONSUM' OR UPPER(P.DESPRO) LIKE '%CONSUM%')
                ORDER BY P.DESPRO
                """,
                (empresa,),
            )
        ERP_CACHE["exemplos_uso_consumo"] = [
            {
                "codpro": clean_str(r.get("CODPRO") or r.get("codpro")),
                "despro": clean_str(r.get("DESPRO") or r.get("despro")),
                "codfam": clean_str(r.get("CODFAM") or r.get("codfam")),
                "codori": clean_str(r.get("CODORI") or r.get("codori")),
                "tippro": clean_str(r.get("TIPPRO") or r.get("tippro")),
                "unimed": clean_str(r.get("UNIMED") or r.get("unimed")),
            }
            for r in rows
        ]

        # ── Amostra de produtos ativos ────────────────────────────────────────
        if usu_ia_ok:
            rows = execute_query_safe(
                """
                SELECT TOP 500 CODPRO AS codpro, DESCRICAO AS despro, CODFAM AS codfam,
                               CODORI AS codori, '' AS tippro, UNIMED AS unimed
                FROM usu_ia.EXEMPLOS_CADASTRO
                WHERE EMPRESA = ? AND STATUS_EXEMPLO = 'APROVADO'
                ORDER BY CODFAM, CODORI, DESCRICAO
                """,
                (empresa,),
            )
        else:
            rows = execute_query_safe(
                """
                SELECT TOP 500 P.CODPRO, P.DESPRO, P.CODFAM, P.CODORI, P.TIPPRO, P.UNIMED
                FROM E075PRO P
                WHERE P.CODEMP = ? AND P.SITPRO = 'A'
                ORDER BY P.CODFAM, P.CODORI, P.DESPRO
                """,
                (empresa,),
            )
        ERP_CACHE["exemplos_ativos"] = [
            {
                "codpro": clean_str(r.get("CODPRO") or r.get("codpro")),
                "despro": clean_str(r.get("DESPRO") or r.get("despro")),
                "codfam": clean_str(r.get("CODFAM") or r.get("codfam")),
                "codori": clean_str(r.get("CODORI") or r.get("codori")),
                "tippro": clean_str(r.get("TIPPRO") or r.get("tippro")),
                "unimed": clean_str(r.get("UNIMED") or r.get("unimed")),
            }
            for r in rows
        ]

        # ── Contexto por setor ────────────────────────────────────────────────
        # Prioridade: usu_ia.CONTEXTO_CADASTRO > legado USU_IA_CONTEXTO_SETOR
        if usu_ia_ok:
            rows = execute_query_safe(
                """
                SELECT SETOR, TIPO_CADASTRO, CODFAM, CODORI, PRIORIDADE,
                       REGRA_NEGOCIO, EXIGE_APROVACAO_FISCAL,
                       PODE_HERDAR_FAMILIA, PODE_HERDAR_ORIGEM
                FROM usu_ia.CONTEXTO_CADASTRO
                WHERE EMPRESA = ? AND ATIVO = 'S'
                ORDER BY SETOR, PRIORIDADE
                """,
                (empresa,),
            )
            ERP_CACHE["contexto_cadastro"] = [
                {
                    "setor": clean_str(r.get("SETOR") or r.get("setor")),
                    "tipo_cadastro": clean_str(r.get("TIPO_CADASTRO") or r.get("tipo_cadastro")),
                    "codfam": clean_str(r.get("CODFAM") or r.get("codfam")),
                    "codori": clean_str(r.get("CODORI") or r.get("codori")),
                    "prioridade": int(r.get("PRIORIDADE") or r.get("prioridade") or 99),
                    "regra_negocio": clean_str(r.get("REGRA_NEGOCIO") or r.get("regra_negocio")),
                    "exige_aprovacao_fiscal": clean_str(r.get("EXIGE_APROVACAO_FISCAL") or r.get("exige_aprovacao_fiscal")) == "S",
                    "pode_herdar_familia": clean_str(r.get("PODE_HERDAR_FAMILIA") or r.get("pode_herdar_familia")) != "N",
                    "pode_herdar_origem": clean_str(r.get("PODE_HERDAR_ORIGEM") or r.get("pode_herdar_origem")) != "N",
                }
                for r in rows
            ]
            ERP_CACHE["contexto_setor"] = ERP_CACHE["contexto_cadastro"]
        else:
            rows = execute_query_safe(
                "SELECT SETOR, TIPO_CADASTRO, CODFAM, CODORI, PRIORIDADE FROM USU_IA_CONTEXTO_SETOR ORDER BY SETOR, PRIORIDADE"
            )
            ERP_CACHE["contexto_cadastro"] = []
            ERP_CACHE["contexto_setor"] = [
                {
                    "setor": clean_str(r.get("SETOR") or r.get("setor")),
                    "tipo_cadastro": clean_str(r.get("TIPO_CADASTRO") or r.get("tipo_cadastro")),
                    "codfam": clean_str(r.get("CODFAM") or r.get("codfam")),
                    "codori": clean_str(r.get("CODORI") or r.get("codori")),
                    "prioridade": int(r.get("PRIORIDADE") or r.get("prioridade") or 99),
                    "regra_negocio": "",
                    "exige_aprovacao_fiscal": False,
                    "pode_herdar_familia": True,
                    "pode_herdar_origem": True,
                }
                for r in rows
            ]

        # ── Política de reaproveitamento ──────────────────────────────────────
        if usu_ia_ok:
            rows = execute_query_safe(
                """
                SELECT TIPO_REGISTRO, TIPO_CADASTRO, SCORE_MINIMO,
                       EXIGE_MESMA_FAMILIA, EXIGE_MESMA_ORIGEM, EXIGE_MESMA_UNIDADE,
                       ACAO_SE_ATENDER, ACAO_SE_NAO_ATENDER
                FROM usu_ia.POLITICA_REAPROVEITAMENTO
                WHERE EMPRESA = ? AND ATIVO = 'S'
                """,
                (empresa,),
            )
            ERP_CACHE["politica_reaproveitamento"] = [
                {
                    "tipo_registro": clean_str(r.get("TIPO_REGISTRO") or r.get("tipo_registro")),
                    "tipo_cadastro": clean_str(r.get("TIPO_CADASTRO") or r.get("tipo_cadastro")),
                    "score_minimo": int(r.get("SCORE_MINIMO") or r.get("score_minimo") or 85),
                    "exige_mesma_familia": clean_str(r.get("EXIGE_MESMA_FAMILIA") or r.get("exige_mesma_familia")) == "S",
                    "exige_mesma_origem": clean_str(r.get("EXIGE_MESMA_ORIGEM") or r.get("exige_mesma_origem")) == "S",
                    "exige_mesma_unidade": clean_str(r.get("EXIGE_MESMA_UNIDADE") or r.get("exige_mesma_unidade")) == "S",
                    "acao_se_atender": clean_str(r.get("ACAO_SE_ATENDER") or r.get("acao_se_atender")),
                }
                for r in rows
            ]
        else:
            ERP_CACHE["politica_reaproveitamento"] = []

        ERP_CACHE["carregado_em"] = datetime.now().isoformat()
        ERP_CACHE["erro_carga"] = None

    except Exception as exc:
        ERP_CACHE["erro_carga"] = str(exc)
        ERP_CACHE["carregado_em"] = datetime.now().isoformat()


def familia_sugerida_erp(tipo_cadastro: str, setor: Optional[str] = None) -> str:
    """
    Sugere família com base no contexto do ERP.
    Prioridade: 1) contexto_setor, 2) família mais usada por tipo, 3) primeira família ativa.
    """
    # 1. Tabela de contexto por setor
    if setor and ERP_CACHE["contexto_setor"]:
        setor_norm = normalize_text(setor)
        for row in ERP_CACHE["contexto_setor"]:
            if normalize_text(row["setor"]) == setor_norm and row["tipo_cadastro"] == tipo_cadastro:
                return row["codfam"]

    # 2. Família CONSUM para uso_consumo
    if tipo_cadastro in ("uso_consumo", "uso_consumo_manutencao", "uso_consumo_admin"):
        for f in ERP_CACHE["familias"]:
            if f["codfam"] == "CONSUM" and f["qtd"] > 0:
                return "CONSUM"

    # 3. Família mais usada (primeira da lista por qtd desc)
    if ERP_CACHE["familias"]:
        return ERP_CACHE["familias"][0]["codfam"]

    return ""


def origem_sugerida_erp(tipo_cadastro: str, setor: Optional[str] = None) -> str:
    """
    Sugere origem com base no contexto do ERP.
    Prioridade: 1) contexto_setor, 2) produtos de uso_consumo ativos.
    """
    # 1. Tabela de contexto por setor
    if setor and ERP_CACHE["contexto_setor"]:
        setor_norm = normalize_text(setor)
        for row in ERP_CACHE["contexto_setor"]:
            if normalize_text(row["setor"]) == setor_norm and row["tipo_cadastro"] == tipo_cadastro:
                return row["codori"]

    # 2. Origem mais comum nos exemplos de uso_consumo
    if tipo_cadastro in ("uso_consumo", "uso_consumo_manutencao", "uso_consumo_admin"):
        contagem: Dict[str, int] = {}
        for ex in ERP_CACHE["exemplos_uso_consumo"]:
            ori = ex["codori"]
            if ori:
                contagem[ori] = contagem.get(ori, 0) + 1
        if contagem:
            return max(contagem, key=lambda k: contagem[k])

    # 3. Origem mais usada no geral
    if ERP_CACHE["origens"]:
        return ERP_CACHE["origens"][0]["codori"]

    return ""


def origem_exige_fiscal(codori: str) -> bool:
    """
    Retorna True se a origem NÃO é '100' ou exige revisão fiscal.
    Baseado na regra GER-075DERAO01: origem != '100' → enviar e-mail de revisão fiscal.
    """
    if not codori:
        return True  # sem origem = pendência
    if codori.strip() == "100":
        return False
    # Verifica se a origem exige número de série (CTRSEP = 'S')
    for ori in ERP_CACHE["origens"]:
        if ori["codori"] == codori.strip():
            return True  # qualquer outra origem que não seja 100 requer revisão
    return True  # origem desconhecida → sinaliza revisão


# =========================================================
# HELPERS
# =========================================================
def normalize_text(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()



def clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()



def extract_measure(text: str) -> str:
    base = clean_str(text)
    patterns = [
        r"\b\d+[\.,]?\d*\s*(pol|polegada|polegadas)\b",
        r"\b\d+[\.,]?\d*\s*(mm|cm|m|kg|g|l|lt|mts|mt)\b",
        r"\b\d+x\d+(x\d+)?\s*(mm|cm|m)?\b",
    ]
    for pattern in patterns:
        found = re.search(pattern, base, re.IGNORECASE)
        if found:
            return found.group(0).upper().replace("POLEGADAS", "POL").replace("POLEGADA", "POL")
    return ""



def extract_ncm(text: str) -> str:
    found = re.search(r"\b(\d{4}\.\d{2}\.\d{2}|\d{8})\b", clean_str(text))
    return found.group(1).replace(".", "") if found else ""



def choose_type_by_text(text: str) -> str:
    norm = normalize_text(text)
    if any(p in norm for p in PALAVRAS_SERVICO):
        return "servico"
    if any(p in norm for p in PALAVRAS_MATERIA_PRIMA):
        return "materia_prima"
    if any(p in norm for p in PALAVRAS_PRODUZIDO):
        return "produto_produzido"
    if any(p in norm for p in PALAVRAS_USO_CONSUMO):
        return "uso_consumo"
    return "uso_consumo"



def yes_answer(text: str) -> bool:
    return normalize_text(text) in {"sim", "s", "serve", "pode ser", "ok", "isso", "esse serve"}



def no_answer(text: str) -> bool:
    return normalize_text(text) in {"nao", "não", "n", "nao serve", "não serve", "esse nao", "esse não", "nao atende", "não atende"}



def merge_slots(slots: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        slots[key] = value
    return slots



def infer_unit(text: str, tipo_cadastro: str) -> str:
    norm = normalize_text(text)
    if any(x in norm for x in ["kg", "quilo"]):
        return "KG"
    if any(x in norm for x in ["metro", "mts", " mt", "mt "]):
        return "MT"
    if any(x in norm for x in ["litro", " lt", "lt "]):
        return "LT"
    if tipo_cadastro == "servico":
        return "SV"
    return "UN"



def json_from_gemini(prompt: str) -> Dict[str, Any]:
    if not GEMINI_DISPONIVEL:
        return {}
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        text = clean_str(getattr(response, "text", ""))
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(match.group(0)) if match else {}
    except Exception:
        return {}



def similarity_score(a: str, b: str) -> int:
    aa = set(re.split(r"\W+", normalize_text(a)))
    bb = set(re.split(r"\W+", normalize_text(b)))
    aa.discard("")
    bb.discard("")
    if not aa or not bb:
        return 0
    return int((len(aa.intersection(bb)) / len(aa.union(bb))) * 100)



def calc_progress(sessao: Dict[str, Any]) -> int:
    etapa = clean_str(sessao.get("etapa_atual"))
    mapping = {
        "confirmar_similar": 16,
        "coletar_dados": 33,
        "proposta_pronta": 66,
        "validado": 83,
        "relatorio_gerado": 100,
        "encerrado_por_reaproveitamento": 100,
    }
    if etapa.startswith("aguardando_"):
        return 50
    return mapping.get(etapa, 10)


# =========================================================
# BUSCA ERP  (usa usu_ia.VW_SIMILARES_* se disponível)
# =========================================================
def _get_politica(tipo_cadastro: str, tipo_registro: str = "PRODUTO") -> Dict[str, Any]:
    """Retorna a política de reaproveitamento para o tipo, ou defaults do CENI."""
    for pol in ERP_CACHE.get("politica_reaproveitamento", []):
        if pol["tipo_registro"] == tipo_registro and pol["tipo_cadastro"] == tipo_cadastro:
            return pol
    return {"score_minimo": 85, "exige_mesma_familia": True, "exige_mesma_origem": False, "exige_mesma_unidade": True}


def find_similar_products(empresa: int, descricao: str, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Busca similares em produtos.
    - Se usu_ia.VW_SIMILARES_PRODUTO disponível: usa a view (já inclui herança de depósito e CTRSEP)
    - Fallback: consulta direta E075PRO com SITPRO='A'
    Em ambos os casos retorna ativos primeiro, ordenados por score descendente.
    """
    descricao_limpa = clean_str(descricao)
    if not descricao_limpa:
        return []

    termos = [t for t in re.split(r"\W+", descricao_limpa.upper()) if len(t) >= 3][:5] or [descricao_limpa.upper()]

    if ERP_CACHE.get("usu_ia_disponivel"):
        where_parts = ["UPPER(DESPRO) LIKE ?" for _ in termos]
        params: List[Any] = [empresa] + [f"%{t}%" for t in termos]
        sql = f"""
            SELECT TOP {limit}
                CODPRO   AS codigo,
                DESPRO   AS descricao,
                CODFAM   AS familia,
                CODORI   AS origem,
                TIPPRO   AS tipo,
                UNIMED   AS unimed,
                SITPRO   AS sitpro,
                CODDER   AS codder,
                CTRSEP   AS ctrsep,
                DEP_PADRAO_HERDADO AS deposito_padrao,
                TIPO_CADASTRO_INFERIDO,
                CASE WHEN CODDER IS NOT NULL THEN 1 ELSE 0 END AS tem_derivacao
            FROM usu_ia.VW_SIMILARES_PRODUTO
            WHERE CODEMP = ?
              AND SITPRO = 'A'
              AND ({' OR '.join(where_parts)})
            ORDER BY LEN(DESPRO), DESPRO
        """
        try:
            rows = execute_query(sql, tuple(params))
        except Exception:
            rows = []
        # fallback sem SITPRO se vazio
        if not rows:
            sql2 = sql.replace("AND SITPRO = 'A'\n", "").replace(
                "ORDER BY LEN(DESPRO)",
                "ORDER BY CASE WHEN SITPRO='A' THEN 0 ELSE 1 END, LEN(DESPRO)"
            )
            try:
                rows = execute_query(sql2, tuple(params))
            except Exception:
                rows = []
    else:
        where_parts = ["UPPER(P.DESPRO) LIKE ?" for _ in termos]
        params_q: List[Any] = [empresa] + [f"%{t}%" for t in termos]
        sql_a = f"""
            SELECT TOP {limit}
                P.CODPRO AS codigo, P.DESPRO AS descricao,
                P.CODFAM AS familia, P.CODORI AS origem,
                P.TIPPRO AS tipo, P.UNIMED AS unimed, P.SITPRO AS sitpro,
                NULL AS ctrsep, NULL AS deposito_padrao,
                NULL AS tipo_cadastro_inferido,
                CASE WHEN EXISTS (
                    SELECT 1 FROM E075DER D
                    WHERE D.CODEMP=P.CODEMP AND D.CODPRO=P.CODPRO AND D.SITDER='A'
                ) THEN 1 ELSE 0 END AS tem_derivacao,
                COALESCE(
                    (SELECT TOP 1 D2.DEPPAD FROM E075DER D2
                     WHERE D2.CODEMP=P.CODEMP AND D2.CODPRO=P.CODPRO AND D2.SITDER='A'),
                    P.DEPPAD,
                    (SELECT TOP 1 F.DEPPAD FROM E012FAM F WHERE F.CODEMP=P.CODEMP AND F.CODFAM=P.CODFAM),
                    (SELECT TOP 1 O.DEPPAD FROM E083ORI O WHERE O.CODEMP=P.CODEMP AND O.CODORI=P.CODORI)
                ) AS deposito_padrao
            FROM E075PRO P
            WHERE P.CODEMP=? AND P.SITPRO='A' AND ({' OR '.join(where_parts)})
            ORDER BY LEN(P.DESPRO), P.DESPRO
        """
        try:
            rows = execute_query(sql_a, tuple(params_q))
        except Exception:
            rows = []
        if not rows:
            sql_b = sql_a.replace("AND P.SITPRO='A' ", "")\
                         .replace("ORDER BY LEN(P.DESPRO)", "ORDER BY CASE WHEN P.SITPRO='A' THEN 0 ELSE 1 END, LEN(P.DESPRO)")
            try:
                rows = execute_query(sql_b, tuple(params_q))
            except Exception:
                rows = []

    for row in rows:
        row["score"] = similarity_score(descricao_limpa, clean_str(row.get("descricao")))
        row["tem_derivacao"] = bool(row.get("tem_derivacao"))
        row["inativo"] = clean_str(row.get("sitpro") or row.get("SITPRO")) != "A"

    rows.sort(key=lambda x: (
        int(x.get("inativo", False)),
        -int(x.get("score", 0)),
        len(clean_str(x.get("descricao"))),
    ))
    return rows[:limit]



def find_similar_services(empresa: int, descricao: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Busca similares em serviços.
    - Se usu_ia.VW_SIMILARES_SERVICO disponível: usa a view (inclui RECPIS, RECCOF, CST)
    - Fallback: consulta direta E080SER
    """
    descricao_limpa = clean_str(descricao)
    if not descricao_limpa:
        return []

    termos = [t for t in re.split(r"\W+", descricao_limpa.upper()) if len(t) >= 3][:5] or [descricao_limpa.upper()]

    if ERP_CACHE.get("usu_ia_disponivel"):
        where_parts = ["UPPER(DESSER) LIKE ?" for _ in termos]
        params: List[Any] = [empresa] + [f"%{t}%" for t in termos]
        sql = f"""
            SELECT TOP {limit}
                CODSER    AS codigo,
                DESSER    AS descricao,
                CODFAM    AS familia,
                NULL      AS origem,
                'SERVICO' AS tipo,
                UNIMED    AS unimed,
                0         AS tem_derivacao
            FROM usu_ia.VW_SIMILARES_SERVICO
            WHERE CODEMP = ? AND ({' OR '.join(where_parts)})
            ORDER BY LEN(DESSER), DESSER
        """
    else:
        where_parts = ["UPPER(S.DESSER) LIKE ?" for _ in termos]
        params = [empresa] + [f"%{t}%" for t in termos]
        sql = f"""
            SELECT TOP {limit}
                S.CODSER AS codigo, S.DESSER AS descricao,
                CAST(NULL AS VARCHAR(40)) AS familia,
                CAST(NULL AS VARCHAR(40)) AS origem,
                'SERVICO' AS tipo, NULL AS unimed, 0 AS tem_derivacao
            FROM E080SER S
            WHERE S.CODEMP = ? AND ({' OR '.join(where_parts)})
            ORDER BY LEN(S.DESSER), S.DESSER
        """

    try:
        rows = execute_query(sql, tuple(params))
    except Exception:
        rows = []

    for row in rows:
        row["score"] = similarity_score(descricao_limpa, clean_str(row.get("descricao")))
        row["inativo"] = False

    rows.sort(key=lambda x: (-int(x.get("score", 0)), len(clean_str(x.get("descricao")))))
    return rows[:limit]



def suggest_code_from_similars(similares: List[Dict[str, Any]]) -> Optional[str]:
    codigos = [clean_str(x.get("codigo")) for x in similares if clean_str(x.get("codigo"))]
    numericos = [c for c in codigos if c.isdigit()]
    if not numericos:
        return None
    return str(max(int(c) for c in numericos) + 1).zfill(max(len(c) for c in numericos))



def family_from_similars(similares: List[Dict[str, Any]]) -> str:
    contagem: Dict[str, int] = {}
    for item in similares:
        familia = clean_str(item.get("familia"))
        if familia:
            contagem[familia] = contagem.get(familia, 0) + 1
    return sorted(contagem.items(), key=lambda x: (-x[1], x[0]))[0][0] if contagem else ""



def origem_from_similars(similares: List[Dict[str, Any]]) -> str:
    contagem: Dict[str, int] = {}
    for item in similares:
        origem = clean_str(item.get("origem"))
        if origem:
            contagem[origem] = contagem.get(origem, 0) + 1
    return sorted(contagem.items(), key=lambda x: (-x[1], x[0]))[0][0] if contagem else ""


# =========================================================
# MOTOR DE IA / REGRAS
# =========================================================
def basic_fiscal_checks(tipo_cadastro: str, slots: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """
    Validações fiscais com base no que o ERP realmente usa:
    - NCM ausente → pendência
    - Família ausente → pendência
    - Origem != '100' → alerta (regra GER-075DERAO01: envia e-mail para revisão fiscal)
    - CST PIS/COFINS → alertas padrão ERP
    - CTRSEP = 'S' na origem → alerta de controle de série
    """
    pendencias: List[str] = []
    alertas: List[str] = []
    codori = clean_str(slots.get("origem"))

    if tipo_cadastro != "servico" and not clean_str(slots.get("ncm")):
        pendencias.append("NCM/classificação fiscal não informada — confirmar antes da emissão de NF.")

    if not clean_str(slots.get("familia")):
        pendencias.append("Família não definida — necessária para herança de contas em E012FAM.")

    if tipo_cadastro in {"materia_prima", "produto_produzido"} and not codori:
        pendencias.append("Origem do item não definida — obrigatória para E075PRO.")

    if tipo_cadastro == "servico":
        alertas.append("Serviço: valide tributação e cadastro correspondente em E080SER.")
        alertas.append("Verificar base de crédito PIS/COFINS na transação de serviço (E440ISC).")
    else:
        alertas.append("Validar CST PIS/COFINS e base de crédito conforme parametrização do ERP (E440IPC).")
        alertas.append("Confirmar contas e parâmetros herdados da família em E012FAM.")

        # Regra GER-075DERAO01: origem != '100' → pendência fiscal obrigatória
        if codori and origem_exige_fiscal(codori):
            pendencias.append(
                f"Origem '{codori}' requer revisão dos parâmetros fiscais do produto "
                "(regra GER-075DERAO01 — verificar CST, PIS/COFINS e base de crédito)."
            )

        # CTRSEP = 'S' → produto exige número de série
        for ori in ERP_CACHE.get("origens", []):
            if ori["codori"] == codori and ori.get("exige_serie"):
                alertas.append(
                    f"Origem '{codori}' exige controle de número de série (CTRSEP=S em E083ORI)."
                )
                break

        # Herança de depósito: se família não tem depósito padrão, alertar
        familia = clean_str(slots.get("familia"))
        if familia:
            for fam in ERP_CACHE.get("familias", []):
                if fam["codfam"] == familia and not clean_str(fam.get("deppad")):
                    alertas.append(
                        f"Família '{familia}' não possui depósito padrão em E012FAM — "
                        "confirmar cadeia de herança (derivação → produto → família → origem)."
                    )
                    break

    return pendencias, alertas



def definir_roteiro(slots: Dict[str, Any]) -> str:
    """Define o roteiro de perguntas com base nos dados já coletados."""
    tipo = clean_str(slots.get("tipo_cadastro") or "")
    equipamento = normalize_text(clean_str(slots.get("equipamento") or ""))
    finalidade = normalize_text(clean_str(slots.get("finalidade") or ""))
    descricao = normalize_text(clean_str(slots.get("descricao_base") or ""))
    texto_completo = f"{equipamento} {finalidade} {descricao}"

    if tipo == "servico":
        return "servico"
    if tipo == "materia_prima":
        return "materia_prima"
    if tipo == "produto_produzido":
        return "produto_produzido"

    # Para uso_consumo, decide sub-roteiro
    kws_manut = DICIONARIO_INTENCAO["uso_consumo_manutencao"]
    if any(normalize_text(kw) in texto_completo for kw in kws_manut):
        return "uso_consumo_manutencao"

    kws_admin = DICIONARIO_INTENCAO["uso_consumo_admin"]
    if any(normalize_text(kw) in texto_completo for kw in kws_admin):
        return "uso_consumo_admin"

    return "uso_consumo_manutencao"  # default para uso_consumo


def next_campo_roteiro(roteiro: str, slots: Dict[str, Any]) -> Optional[str]:
    """Retorna o próximo campo a coletar, respeitando o roteiro e campos já preenchidos."""
    campos = ROTEIROS.get(roteiro, ROTEIROS["uso_consumo_manutencao"])
    for campo in campos:
        valor = slots.get(campo)
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            return campo
    return None


def pergunta_para_campo(campo: str, roteiro: str) -> str:
    """Retorna a pergunta contextualizada para o campo e roteiro."""
    roteiro_perguntas = PERGUNTAS_ROTEIRO.get(roteiro, {})
    return roteiro_perguntas.get(campo) or PERGUNTAS.get(campo) or f"Qual o valor para '{campo}'?"


def inferir_slots_do_texto(texto: str, slots_atuais: Dict[str, Any], campo_em_coleta: Optional[str]) -> Dict[str, Any]:
    """
    Infere slots do texto do usuário.
    Se campo_em_coleta está definido, a resposta preenche esse campo diretamente.
    Também extrai dados adicionais que aparecem no texto.
    """
    norm = normalize_text(texto)
    novos: Dict[str, Any] = {}

    # 1. Preenchimento direto do campo em coleta
    if campo_em_coleta:
        novos[campo_em_coleta] = clean_str(texto)

    # 2. Detecção de tipo de cadastro (se ainda não definido)
    if not slots_atuais.get("tipo_cadastro") and not novos.get("tipo_cadastro"):
        novos["tipo_cadastro"] = choose_type_by_text(texto)

    # Sobrescreve tipo se mencionado explicitamente
    if "servico" in norm or "serviço" in norm:
        novos["tipo_cadastro"] = "servico"
    elif "materia prima" in norm or "matéria prima" in norm:
        novos["tipo_cadastro"] = "materia_prima"
    elif "produto produzido" in norm or "fabricado" in norm or "produzido" in norm:
        novos["tipo_cadastro"] = "produto_produzido"
    elif "uso e consumo" in norm or "uso consumo" in norm:
        novos["tipo_cadastro"] = "uso_consumo"

    # 3. Detecção de equipamento
    kws_equip = ["empilhadeira", "torno", "prensa", "compressor", "carrinho", "maquina", "máquina"]
    for kw in kws_equip:
        if kw in norm and not slots_atuais.get("equipamento") and campo_em_coleta != "equipamento":
            novos["equipamento"] = kw.upper()
            break

    # 4. Detecção de finalidade
    kws_finalidade = ["consumo interno", "reposicao", "reposição", "manutencao", "manutenção", "compra pontual"]
    for kw in kws_finalidade:
        if kw in norm and not slots_atuais.get("finalidade") and campo_em_coleta != "finalidade":
            novos["finalidade"] = kw
            break

    # 5. Extração de medida
    medida = extract_measure(texto)
    if medida and not slots_atuais.get("medida") and campo_em_coleta not in {"medida", "especificacao_principal"}:
        novos["medida"] = medida
        novos["especificacao_principal"] = medida

    # 6. Extração de NCM
    ncm = extract_ncm(texto)
    if ncm:
        novos["ncm"] = ncm

    # 7. Unidade
    if not slots_atuais.get("unidade") and campo_em_coleta != "unidade":
        if re.search(r"\b(un|unidade|peca|peça)\b", norm):
            novos["unidade"] = "UN"
        elif re.search(r"\b(kg|quilo)\b", norm):
            novos["unidade"] = "KG"
        elif re.search(r"\b(mt|metro|mts)\b", norm):
            novos["unidade"] = "MT"

    # 8. Características
    if "philips" in norm:
        novos["caracteristica"] = "PHILIPS"
    elif "fenda" in norm:
        novos["caracteristica"] = "FENDA"
    elif "torx" in norm:
        novos["caracteristica"] = "TORX"
    if "magnet" in norm:
        base = clean_str(novos.get("caracteristica") or slots_atuais.get("caracteristica") or "")
        novos["caracteristica"] = f"{base} MAGNÉTICA".strip()

    # 9. Descrição base (se ainda não definida e não é o campo em coleta)
    if not slots_atuais.get("descricao_base") and campo_em_coleta != "descricao_base":
        novos.setdefault("descricao_base", clean_str(texto))

    # 10. Extração via Gemini (enriquece sem sobrescrever slots já preenchidos)
    if GEMINI_DISPONIVEL:
        slots_combinados = {**slots_atuais, **{k: v for k, v in novos.items() if v}}
        campos_faltantes = [c for c in [
            "tipo_cadastro", "descricao_base", "finalidade", "medida",
            "unidade", "familia", "origem", "ncm", "derivacao", "fornecedor",
            "equipamento", "especificacao_principal",
        ] if not clean_str(slots_combinados.get(c))]

        if campos_faltantes:
            prompt = (
                f"Você é analista de cadastro da fábrica CENI.\n"
                f"Texto do usuário: '{texto}'\n"
                f"Contexto já coletado: {json.dumps(slots_combinados, ensure_ascii=False, default=str)}\n"
                f"Extraia APENAS campos ainda não preenchidos: {campos_faltantes}\n"
                f"Responda SOMENTE com JSON válido, sem explicações.\n"
                "JSON:"
            )
            extraidos = json_from_gemini(prompt)
            if isinstance(extraidos, dict):
                for k, v in extraidos.items():
                    if v and k in campos_faltantes:
                        novos.setdefault(k, v)

    return novos


# Mantém compatibilidade com chamadas anteriores
def extract_slots_from_text(text: str, slots_atuais: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return inferir_slots_do_texto(text, slots_atuais or {}, None)



def next_missing_field(tipo_cadastro: str, slots: Dict[str, Any]) -> Optional[str]:
    """Fallback: usa CAMPOS_POR_TIPO — usar next_campo_roteiro quando roteiro disponível."""
    for campo in CAMPOS_POR_TIPO.get(tipo_cadastro, CAMPOS_POR_TIPO["uso_consumo"]):
        if not clean_str(slots.get(campo)):
            return campo
    return None



def build_description(slots: Dict[str, Any]) -> Tuple[str, str]:
    tipo = clean_str(slots.get("tipo_cadastro"))
    base = clean_str(slots.get("descricao_base")).upper()
    caract = clean_str(slots.get("caracteristica")).upper()
    medida = clean_str(slots.get("medida")).upper()
    marca = clean_str(slots.get("fornecedor")).upper()

    descricao = re.sub(r"\s+", " ", " ".join([p for p in [base, caract, medida] if p])).strip()

    complemento_parts = []
    if MAP_TIPO_LABEL.get(tipo):
        complemento_parts.append(f"Tipo: {MAP_TIPO_LABEL[tipo]}")
    if clean_str(slots.get("finalidade")):
        complemento_parts.append(f"Finalidade: {clean_str(slots['finalidade'])}")
    if marca:
        complemento_parts.append(f"Fornecedor/Marca ref.: {marca}")
    if clean_str(slots.get("ncm")):
        complemento_parts.append(f"NCM: {clean_str(slots['ncm'])}")

    return descricao, " | ".join(complemento_parts)



def build_suggestion(sessao: Dict[str, Any]) -> CadastroSugerido:
    slots = sessao["slots"]
    tipo = slots.get("tipo_cadastro") or choose_type_by_text(slots.get("descricao_base", ""))
    similares = sessao.get("similares", [])
    setor = clean_str(slots.get("setor"))

    # Prioridade para família: slot > similares > cache ERP > vazio
    if not clean_str(slots.get("familia")):
        familia_sim = family_from_similars(similares)
        slots["familia"] = familia_sim or familia_sugerida_erp(tipo, setor or None)

    # Prioridade para origem: slot > similares > cache ERP > vazio
    if not clean_str(slots.get("origem")):
        origem_sim = origem_from_similars(similares)
        slots["origem"] = origem_sim or origem_sugerida_erp(tipo, setor or None)

    # Unidade: slot > UNIMED do similar mais aderente > inferência
    if not clean_str(slots.get("unidade")):
        unimed_similar = ""
        for s in similares:
            unimed_similar = clean_str(s.get("unimed") or s.get("UNIMED"))
            if unimed_similar:
                break
        slots["unidade"] = unimed_similar or infer_unit(slots.get("descricao_base", ""), tipo)

    descricao, descricao_complementar = build_description(slots)
    pendencias, alertas = basic_fiscal_checks(tipo, slots)

    # Alerta se similar inativo foi o mais próximo encontrado
    for s in similares[:1]:
        if s.get("inativo"):
            alertas.insert(0, f"Similar mais próximo ({clean_str(s.get('codigo'))}) está INATIVO no ERP — não utilizar como base sem reativação.")

    # Parâmetros fiscais detalhados por tipo
    if tipo == "servico":
        parametros_fiscais = [
            "Validar tributação de serviço em E080SER.",
            "Verificar base de crédito PIS/COFINS na transação (E440ISC).",
            "Conferir CST PIS x CST COFINS e recupera PIS x recupera COFINS.",
        ]
    else:
        parametros_fiscais = [
            "Conferir NCM e classificação fiscal antes da emissão de NF.",
            "Conferir CST PIS/COFINS e base de crédito (E440IPC).",
            "Conferir contas e parâmetros herdados da família em E012FAM.",
            "Verificar cadeia de depósito: derivação → produto → família → origem.",
        ]
        # Origem não é 100 → já está em pendências, mas reforça nos parâmetros
        codori = clean_str(slots.get("origem"))
        if codori and codori != "100":
            parametros_fiscais.append(
                f"Origem '{codori}': enviar para fiscal verificar parâmetros (GER-075DERAO01)."
            )

    return CadastroSugerido(
        tipo_cadastro=tipo,
        codigo_sugerido=suggest_code_from_similars(similares),
        descricao=descricao,
        descricao_complementar=descricao_complementar,
        familia=clean_str(slots.get("familia")) or None,
        origem=clean_str(slots.get("origem")) or None,
        unidade=clean_str(slots.get("unidade")) or None,
        derivacao=clean_str(slots.get("derivacao")) or None,
        ncm=clean_str(slots.get("ncm")) or None,
        finalidade=clean_str(slots.get("finalidade")) or None,
        parametros_fiscais_sugeridos=parametros_fiscais,
        pendencias=pendencias,
        alertas=alertas,
    )



def relatorio_final(sessao: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    cadastro = build_suggestion(sessao)
    sessao["cadastro_sugerido"] = cadastro.model_dump()
    similares = sessao.get("similares", [])
    slots = sessao.get("slots", {})
    roteiro = clean_str(sessao.get("roteiro_atual") or slots.get("roteiro") or "")
    equipamento = clean_str(slots.get("equipamento"))
    motivo_rejeicao = clean_str(slots.get("motivo_rejeicao_similar"))

    linhas = [
        "RELATÓRIO DE CADASTRO GUIADO — CENI",
        f"Sessão: {sessao['sessao_id']}",
        f"Usuário: {sessao['usuario']}",
        f"Solicitação inicial: {sessao['texto_inicial']}",
        f"Roteiro usado: {roteiro or '-'}",
        "",
        "1. Resumo da classificação",
        f"- Tipo de cadastro: {MAP_TIPO_LABEL.get(cadastro.tipo_cadastro, cadastro.tipo_cadastro)}",
        f"- Roteiro: {roteiro or '-'}",
        f"- Descrição sugerida: {cadastro.descricao or '-'}",
        f"- Código sugerido: {cadastro.codigo_sugerido or 'definir conforme padrão interno'}",
        f"- Família sugerida: {cadastro.familia or '-'}",
        f"- Origem sugerida: {cadastro.origem or '-'}",
        f"- Unidade sugerida: {cadastro.unidade or '-'}",
        f"- Derivação sugerida: {cadastro.derivacao or '-'}",
        f"- NCM informado/sugerido: {cadastro.ncm or '-'}",
        f"- Equipamento: {equipamento or '-'}",
        f"- Finalidade: {clean_str(slots.get('finalidade')) or '-'}",
        "",
        "2. Produtos/serviços similares encontrados",
    ]

    if similares:
        for item in similares[:8]:
            linhas.append(
                f"- {clean_str(item.get('codigo'))} | {clean_str(item.get('descricao'))} "
                f"| família={clean_str(item.get('familia')) or '-'} "
                f"| origem={clean_str(item.get('origem')) or '-'} "
                f"| score={item.get('score', 0)}"
            )
    else:
        linhas.append("- Nenhum similar localizado pela busca inicial.")

    if motivo_rejeicao:
        linhas += ["", f"Motivo de novo cadastro: {motivo_rejeicao}"]

    linhas += ["", "3. Pendências"]
    linhas += ([f"- {x}" for x in cadastro.pendencias]
               or ["- Nenhuma pendência obrigatória identificada pelo motor básico."])
    linhas += ["", "4. Alertas fiscais"] + [f"- {x}" for x in cadastro.alertas]
    linhas += ["", "5. Parâmetros fiscais sugeridos"] + [f"- {x}" for x in cadastro.parametros_fiscais_sugeridos]
    linhas += ["", "6. Dados coletados na entrevista"]

    for chave, valor in slots.items():
        if clean_str(str(valor or "")):
            linhas.append(f"- {chave}: {valor}")

    # JSON estruturado conforme entregável especificado
    rel_json = {
        "sessao_id": sessao["sessao_id"],
        "usuario": sessao["usuario"],
        "solicitacao_inicial": sessao["texto_inicial"],
        "tipo_cadastro": cadastro.tipo_cadastro,
        "roteiro_usado": roteiro,
        "descricao_sugerida": cadastro.descricao,
        "familia_sugerida": cadastro.familia,
        "origem_sugerida": cadastro.origem,
        "unidade_sugerida": cadastro.unidade,
        "finalidade": clean_str(slots.get("finalidade")) or None,
        "equipamento": equipamento or None,
        "similares_encontrados": [clean_str(s.get("descricao")) for s in similares if s.get("descricao")],
        "motivo_novo_cadastro": motivo_rejeicao or None,
        "pendencias": cadastro.pendencias,
        "alertas": cadastro.alertas,
        "cadastro_sugerido": cadastro.model_dump(),
        "slots": slots,
        "historico": sessao.get("historico", []),
    }

    return "\n".join(linhas), rel_json


# =========================================================
# SESSÃO — persistência em usu_ia.SESSAO_CHATBOT (se disponível)
# =========================================================
def db_salvar_sessao(sessao: Dict[str, Any]) -> None:
    """Persiste/atualiza sessao no banco. Silencioso em caso de falha."""
    if not ERP_CACHE.get("usu_ia_disponivel"):
        return
    try:
        dados_json = json.dumps(sessao, ensure_ascii=False, default=str)
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                MERGE usu_ia.SESSAO_CHATBOT AS tgt
                USING (SELECT ? AS SESSAO_ID) AS src ON tgt.SESSAO_ID = src.SESSAO_ID
                WHEN MATCHED THEN
                    UPDATE SET
                        STATUS_SESSAO   = ?,
                        ROTEIRO_ATUAL   = ?,
                        CAMPO_EM_COLETA = ?,
                        TIPO_CADASTRO   = ?,
                        DADOS_JSON      = ?,
                        DATALT          = GETDATE()
                WHEN NOT MATCHED THEN
                    INSERT (SESSAO_ID, EMPRESA, USUARIO, STATUS_SESSAO, ROTEIRO_ATUAL,
                            CAMPO_EM_COLETA, TIPO_CADASTRO, DADOS_JSON)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    sessao["sessao_id"],
                    # UPDATE
                    sessao.get("etapa_atual", "aberta"),
                    sessao.get("roteiro_atual"),
                    sessao.get("campo_em_coleta"),
                    sessao.get("slots", {}).get("tipo_cadastro"),
                    dados_json,
                    # INSERT
                    sessao["sessao_id"],
                    sessao.get("empresa", EMPRESA_PADRAO),
                    sessao.get("usuario", ""),
                    sessao.get("etapa_atual", "aberta"),
                    sessao.get("roteiro_atual"),
                    sessao.get("campo_em_coleta"),
                    sessao.get("slots", {}).get("tipo_cadastro"),
                    dados_json,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # nunca interrompe o fluxo principal


def db_carregar_sessao(sessao_id: str) -> Optional[Dict[str, Any]]:
    """Carrega sessao do banco. Retorna None se não encontrar."""
    if not ERP_CACHE.get("usu_ia_disponivel"):
        return None
    try:
        rows = execute_query(
            "SELECT DADOS_JSON FROM usu_ia.SESSAO_CHATBOT WHERE SESSAO_ID = ?",
            (sessao_id,),
        )
        if not rows:
            return None
        dados_raw = rows[0].get("DADOS_JSON") or rows[0].get("dados_json") or ""
        return json.loads(dados_raw) if dados_raw else None
    except Exception:
        return None


def db_log_msg(sessao_id: str, origem: str, mensagem: str, etapa: Optional[str] = None) -> None:
    """Grava mensagem no log de sessão. Silencioso em caso de falha."""
    if not ERP_CACHE.get("usu_ia_disponivel"):
        return
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO usu_ia.SESSAO_CHATBOT_MSG
                    (SESSAO_ID, ORIGEM, ETAPA, MENSAGEM)
                VALUES (?, ?, ?, ?)
                """,
                (sessao_id, origem.upper(), etapa, mensagem),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def get_or_load_sessao(sessao_id: str) -> Optional[Dict[str, Any]]:
    """
    Busca sessão em memória primeiro; se não encontrar, tenta carregar do banco.
    Se carregar do banco, repopula o dict em memória.
    """
    if sessao_id in SESSOES:
        return SESSOES[sessao_id]
    sessao = db_carregar_sessao(sessao_id)
    if sessao:
        SESSOES[sessao_id] = sessao
    return sessao


def create_session(username: str, empresa: int, texto_inicial: str) -> Dict[str, Any]:
    sessao_id = uuid.uuid4().hex

    slots: Dict[str, Any] = dict(DEFAULT_SLOTS)
    novos = inferir_slots_do_texto(texto_inicial, slots, None)
    merge_slots(slots, novos)

    tipo = clean_str(slots.get("tipo_cadastro") or choose_type_by_text(texto_inicial))
    slots["tipo_cadastro"] = tipo

    roteiro = definir_roteiro(slots)
    slots["roteiro"] = roteiro
    campo_em_coleta = next_campo_roteiro(roteiro, slots)

    similares = (
        find_similar_services(empresa, texto_inicial)
        if tipo == "servico"
        else find_similar_products(empresa, texto_inicial)
    )

    sessao: Dict[str, Any] = {
        "sessao_id": sessao_id,
        "empresa": empresa,
        "usuario": username,
        "texto_inicial": texto_inicial,
        "etapa_atual": "confirmar_similar" if similares else "coletar_dados",
        "aguardando_confirmacao_similar": bool(similares),
        "roteiro_atual": roteiro,
        "campo_em_coleta": campo_em_coleta,
        "slots": slots,
        "similares": similares,
        "cadastro_sugerido": None,
        "historico": [
            {
                "origem": "assistant",
                "mensagem": "Olá. Sou o assistente de cadastro guiado do CENI. Descreva o item que você precisa cadastrar — vou pesquisar similares no ERP e te conduzir até uma proposta completa.",
                "data_hora": datetime.now().isoformat(),
            },
            {
                "origem": "user",
                "mensagem": texto_inicial,
                "data_hora": datetime.now().isoformat(),
            },
        ],
        "criado_em": datetime.now().isoformat(),
    }

    SESSOES[sessao_id] = sessao
    # Persiste no banco (não bloqueia em caso de falha)
    db_salvar_sessao(sessao)
    db_log_msg(sessao_id, "user", texto_inicial, etapa="inicio")
    return sessao



def session_to_response(sessao: Dict[str, Any], mensagem: str, pergunta: Optional[str] = None) -> SessaoResponse:
    cadastro = CadastroSugerido(**sessao["cadastro_sugerido"]) if sessao.get("cadastro_sugerido") else None
    return SessaoResponse(
        sessao_id=sessao["sessao_id"],
        etapa_atual=sessao["etapa_atual"],
        mensagem=mensagem,
        pergunta=pergunta,
        similares=[ItemSimilar(**item) for item in sessao.get("similares", [])],
        cadastro_sugerido=cadastro,
        slots=sessao.get("slots", {}),
        progresso=calc_progress(sessao),
        roteiro_atual=sessao.get("roteiro_atual"),
        campo_em_coleta=sessao.get("campo_em_coleta"),
    )


# =========================================================
# UI HTML
# =========================================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Cadastro Guiado CENI</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0d1117;
      --card: rgba(22,27,34,0.95);
      --card2: rgba(30,38,50,0.9);
      --border: rgba(48,54,65,0.8);
      --accent: #3b82f6;
      --accent2: #6366f1;
      --success: #22c55e;
      --warn: #f59e0b;
      --danger: #ef4444;
      --text: #e6edf3;
      --muted: #8b949e;
      --radius: 12px;
      --radius-sm: 8px;
      --shadow: 0 8px 32px rgba(0,0,0,0.4);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text);
           min-height: 100vh; display: flex; flex-direction: column; }

    /* ── HEADER ── */
    .header { background: var(--card); border-bottom: 1px solid var(--border);
              padding: 12px 24px; display: flex; align-items: center; gap: 16px;
              backdrop-filter: blur(12px); position: sticky; top: 0; z-index: 100; }
    .logo { font-size: 18px; font-weight: 700;
            background: linear-gradient(90deg, var(--accent), var(--accent2));
            -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .header-right { margin-left: auto; display: flex; gap: 10px; align-items: center; }
    .badge { font-size: 11px; background: var(--card2); border: 1px solid var(--border);
             padding: 3px 10px; border-radius: 20px; color: var(--muted); }
    .badge.ok   { border-color: var(--success); color: var(--success); }
    .badge.warn { border-color: var(--warn);    color: var(--warn); }
    .badge.err  { border-color: var(--danger);  color: var(--danger); }

    /* ── TABS ── */
    .tabs { display: flex; gap: 2px; padding: 0 24px;
            background: var(--card); border-bottom: 1px solid var(--border); }
    .tab { padding: 12px 20px; font-size: 13px; font-weight: 500; cursor: pointer;
           color: var(--muted); border-bottom: 2px solid transparent;
           transition: all .2s; white-space: nowrap; }
    .tab:hover { color: var(--text); }
    .tab.active { color: var(--accent); border-color: var(--accent); }
    .tab-sep { width: 1px; background: var(--border); margin: 8px 0; }

    /* ── LAYOUT ── */
    .main { flex: 1; display: flex; overflow: hidden; height: calc(100vh - 95px); }
    .panel { display: none; flex: 1; overflow-y: auto; padding: 20px 24px; flex-direction: column; }
    .panel.active { display: flex; }

    /* ── COMPONENTS ── */
    .card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
            padding: 18px; margin-bottom: 14px; }
    .card-title { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase;
                  letter-spacing: .5px; margin-bottom: 12px; }
    input, textarea, select {
      background: rgba(255,255,255,.05); border: 1px solid var(--border); border-radius: var(--radius-sm);
      color: var(--text); font-family: inherit; font-size: 14px;
      padding: 10px 14px; width: 100%; outline: none; transition: border .2s;
    }
    input:focus, textarea:focus { border-color: var(--accent); }
    textarea { resize: vertical; min-height: 80px; }
    button {
      background: var(--accent); color: #fff; border: none; border-radius: var(--radius-sm);
      padding: 9px 18px; font-family: inherit; font-size: 13px; font-weight: 600;
      cursor: pointer; transition: all .2s; white-space: nowrap;
    }
    button:hover { opacity: .85; transform: translateY(-1px); }
    button:active { transform: translateY(0); }
    button.sec  { background: var(--card2); border: 1px solid var(--border); color: var(--text); }
    button.warn { background: var(--warn); }
    button.danger { background: var(--danger); }
    button.sm { padding: 6px 12px; font-size: 12px; }
    .btn-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }

    /* ── CHAT ── */
    .chat-wrap { display: flex; flex-direction: column; height: 100%; }
    .chat-msgs { flex: 1; overflow-y: auto; display: flex; flex-direction: column;
                 gap: 10px; padding: 4px 0 16px; }
    .msg { max-width: 80%; padding: 10px 14px; border-radius: var(--radius); font-size: 14px;
           line-height: 1.55; }
    .msg.user { background: var(--accent); color: #fff; align-self: flex-end;
                border-bottom-right-radius: 4px; }
    .msg.bot  { background: var(--card2); border: 1px solid var(--border);
                align-self: flex-start; border-bottom-left-radius: 4px; }
    .msg.system { background: rgba(100,200,100,.1); border: 1px solid rgba(100,200,100,.3);
                  color: var(--success); font-size: 12px; align-self: center;
                  padding: 6px 14px; border-radius: 20px; }
    .msg.err { background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.3);
               color: var(--danger); align-self: center; font-size: 12px;
               padding: 6px 14px; border-radius: 20px; }
    .chat-input-row { display: flex; gap: 8px; padding-top: 12px;
                      border-top: 1px solid var(--border); }
    .chat-input-row input { flex: 1; }
    .pergunta-box { background: rgba(59,130,246,.1); border: 1px solid rgba(59,130,246,.35);
                   border-radius: var(--radius-sm); padding: 10px 14px; font-size: 13px;
                   color: #93c5fd; margin-bottom: 8px; }

    /* ── SIMILARES ── */
    .similar-item { background: var(--card2); border: 1px solid var(--border);
                   border-radius: var(--radius-sm); padding: 12px; margin-bottom: 8px;
                   transition: border-color .2s; }
    .similar-item:hover { border-color: var(--accent); }
    .similar-cod { font-size: 11px; font-weight: 700; color: var(--accent); margin-bottom: 4px; }
    .similar-des { font-size: 14px; margin-bottom: 6px; }
    .similar-tags { display: flex; gap: 6px; flex-wrap: wrap; }
    .tag { font-size: 10px; background: rgba(255,255,255,.07); border: 1px solid var(--border);
           padding: 2px 8px; border-radius: 20px; color: var(--muted); }

    /* ── PROPOSTA ── */
    .prop-field { display: flex; gap: 12px; justify-content: space-between;
                  padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 14px; }
    .prop-field:last-child { border: none; }
    .prop-label { color: var(--muted); font-size: 12px; font-weight: 500;
                  min-width: 140px; text-transform: uppercase; }
    .prop-val   { flex: 1; text-align: right; }
    .pendencia  { font-size: 12px; color: var(--warn); padding: 4px 0; }
    .alerta     { font-size: 12px; color: var(--danger); padding: 4px 0; }

    /* ── PROGRESSO ── */
    .progress-bar { background: var(--card2); border-radius: 50px; height: 6px; overflow: hidden; margin-bottom: 16px; }
    .progress-fill { background: linear-gradient(90deg, var(--accent), var(--accent2));
                     height: 100%; border-radius: 50px; transition: width .4s ease; }
    .step-dots { display: flex; gap: 8px; }
    .step-dot { width: 8px; height: 8px; border-radius: 50%;
                background: var(--border); transition: background .3s; }
    .step-dot.done { background: var(--accent); }
    .step-dot.active { background: var(--accent2); box-shadow: 0 0 6px var(--accent2); }

    /* ── DIAG ── */
    .diag-item { display: flex; gap: 12px; padding: 8px 0;
                 border-bottom: 1px solid rgba(255,255,255,.05); font-size: 13px; align-items: center; }
    .diag-item:last-child { border: none; }
    .diag-status { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; min-width: 58px;
                   text-align: center; }
    .diag-status.PASSOU  { background: rgba(34,197,94,.15);  color: var(--success); border: 1px solid rgba(34,197,94,.3); }
    .diag-status.FALHOU  { background: rgba(239,68,68,.15);  color: var(--danger);  border: 1px solid rgba(239,68,68,.3); }
    .diag-status.AVISO   { background: rgba(245,158,11,.15); color: var(--warn);    border: 1px solid rgba(245,158,11,.3); }
    .diag-status.INFO    { background: rgba(99,102,241,.15); color: #a5b4fc;        border: 1px solid rgba(99,102,241,.3); }
    .diag-item-name  { flex: 1; }
    .diag-item-detail { color: var(--muted); font-size: 12px; min-width: 220px; text-align: right; }

    /* ── SQL ── */
    pre { background: rgba(0,0,0,.4); border: 1px solid var(--border); border-radius: var(--radius-sm);
          padding: 14px; font-family: 'Fira Code', 'Courier New', monospace; font-size: 12px;
          line-height: 1.6; overflow-x: auto; color: #a5f3fc; white-space: pre-wrap; margin-bottom: 10px; }
    .sql-block { margin-bottom: 18px; }
    .sql-title { font-size: 12px; font-weight: 600; color: var(--muted);
                 text-transform: uppercase; letter-spacing: .5px; margin-bottom: 8px; }

    /* ── RELATÓRIO ── */
    #reportOutput { background: rgba(0,0,0,.4); border: 1px solid var(--border);
                   border-radius: var(--radius-sm); padding: 16px;
                   font-family: 'Fira Code', monospace; font-size: 13px;
                   line-height: 1.7; white-space: pre-wrap; color: #d2f4ea;
                   min-height: 200px; flex: 1; }

    /* ── LOGIN ── */
    .login-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.7);
                     display: flex; align-items: center; justify-content: center; z-index: 999; }
    .login-box { background: var(--card); border: 1px solid var(--border); border-radius: 16px;
                 padding: 32px; width: 340px; box-shadow: var(--shadow); }
    .login-title { font-size: 20px; font-weight: 700; margin-bottom: 6px; }
    .login-sub { font-size: 13px; color: var(--muted); margin-bottom: 24px; }

    /* ── JSON viewer ── */
    #jsonViewer { background: rgba(0,0,0,.4); border: 1px solid var(--border);
                  border-radius: var(--radius-sm); padding: 14px;
                  font-family: monospace; font-size: 12px; white-space: pre-wrap;
                  color: #c3e4f0; max-height: 60vh; overflow-y: auto; }

    .scroll-y { overflow-y: auto; }
    ::-webkit-scrollbar { width: 6px; } 
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  </style>
</head>
<body>

<!-- LOGIN OVERLAY -->
<div class="login-overlay" id="loginOverlay">
  <div class="login-box">
    <div class="login-title">Cadastro Guiado CENI</div>
    <div class="login-sub">Assistente de cadastro de produtos e serviços</div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">USUÁRIO</label>
      <input type="text" id="loginUser" placeholder="ADMIN" />
    </div>
    <div style="margin-bottom:20px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">SENHA</label>
      <input type="password" id="loginPass" placeholder="••••••" onkeydown="if(event.key==='Enter')doLogin()" />
    </div>
    <button id="loginBtn" onclick="doLogin()" style="width:100%">Entrar</button>
    <div id="loginErr" style="color:var(--danger);font-size:12px;margin-top:10px;text-align:center"></div>
  </div>
</div>

<!-- HEADER -->
<div class="header">
  <div class="logo">⚙ CENI · Cadastro Guiado</div>
  <div class="header-right">
    <span class="badge" id="sessionBadge">Sem sessão</span>
    <span class="badge" id="erpcacheBadge">ERP cache: —</span>
    <span class="badge" id="usu_ia_badge">usu_ia: —</span>
    <span class="badge" id="userBadge">—</span>
    <button class="sec sm" onclick="doLogout()">Sair</button>
  </div>
</div>

<!-- TABS -->
<div class="tabs">
  <div class="tab active" onclick="showTab('chat')"   id="tab-chat">💬 Chat</div>
  <div class="tab"        onclick="showTab('sim')"    id="tab-sim">🔍 Similares ERP</div>
  <div class="tab"        onclick="showTab('prop')"   id="tab-prop">📋 Proposta</div>
  <div class="tab"        onclick="showTab('rel')"    id="tab-rel">📄 Relatório</div>
  <div class="tab-sep"></div>
  <div class="tab"        onclick="showTab('diag')"   id="tab-diag">🩺 Diagnóstico</div>
  <div class="tab"        onclick="showTab('sqlerp')" id="tab-sqlerp">🗄 SQL ERP</div>
</div>

<!-- PANELS -->
<div class="main">

  <!-- ══ CHAT ══ -->
  <div class="panel active" id="panel-chat">
    <div class="card" style="flex-shrink:0">
      <div class="card-title">Nova Solicitação</div>
      <textarea id="textoInicial" placeholder="Descreva o item que você precisa cadastrar. Ex: pneu 275/80R22.5 para empilhadeira da produção"></textarea>
      <div class="btn-row" style="margin-top:10px">
        <button onclick="iniciarCadastro()">Iniciar</button>
        <button class="sec" onclick="limparSessao()">Nova Sessão</button>
      </div>
      <div class="progress-bar" id="progressBar"><div class="progress-fill" id="progressFill" style="width:0"></div></div>
      <div class="step-dots" id="stepDots"></div>
    </div>
    <div class="chat-wrap" style="flex:1;min-height:0">
      <div id="perguntaBox" class="pergunta-box" style="display:none"></div>
      <div class="chat-msgs" id="chatMsgs"></div>
      <div class="chat-input-row">
        <input type="text" id="chatInput" placeholder="Responda aqui..." onkeydown="if(event.key==='Enter')enviarResposta()" />
        <button onclick="enviarResposta()">Enviar</button>
        <button class="sec" onclick="showTab('rel');gerarRelatorio()">📄 Relatório</button>
        <button class="sec" onclick="showTab('prop');renderizarProposta()">📋 Proposta</button>
      </div>
    </div>
  </div>

  <!-- ══ SIMILARES ══ -->
  <div class="panel" id="panel-sim">
    <div class="card">
      <div class="card-title">Similares encontrados no ERP</div>
      <div id="simList"><span style="color:var(--muted);font-size:13px">Inicie uma sessão para ver similares.</span></div>
    </div>
  </div>

  <!-- ══ PROPOSTA ══ -->
  <div class="panel" id="panel-prop">
    <div class="card">
      <div class="card-title">Proposta de Cadastro</div>
      <div id="propostaArea"><span style="color:var(--muted);font-size:13px">Proposta ainda não gerada.</span></div>
    </div>
    <div class="card">
      <div class="card-title">Validação</div>
      <button onclick="validarCadastro()">Validar</button>
      <div id="validacaoResult" style="margin-top:12px;font-size:13px"></div>
    </div>
  </div>

  <!-- ══ RELATÓRIO ══ -->
  <div class="panel" id="panel-rel" style="flex-direction:column">
    <div class="card" style="flex-shrink:0">
      <div class="card-title">Relatório Final</div>
      <button onclick="gerarRelatorio()">Gerar Relatório</button>
    </div>
    <div id="reportOutput">Relatório ainda não gerado.</div>
  </div>

  <!-- ══ DIAGNÓSTICO ══ -->
  <div class="panel" id="panel-diag">
    <div class="card">
      <div class="card-title">Diagnóstico da Infraestrutura</div>
      <div class="btn-row">
        <button onclick="loadDiag()">Carregar Diagnóstico</button>
        <button class="sec" onclick="loadHealth()">Health Check</button>
        <button class="sec" onclick="loadContexto()">Contexto ERP</button>
        <button class="sec" onclick="recarregarContexto()">↻ Recarregar Cache</button>
      </div>
      <div id="diagSummary" style="margin-bottom:12px"></div>
      <div id="diagItems"></div>
    </div>
    <div class="card">
      <div class="card-title">Famílias (E012FAM)</div>
      <div class="btn-row">
        <button class="sec" onclick="loadFamilias()">Carregar Famílias</button>
      </div>
      <div id="familiasArea" style="font-size:13px"></div>
    </div>
    <div class="card">
      <div class="card-title">Origens (E083ORI)</div>
      <div class="btn-row">
        <button class="sec" onclick="loadOrigens()">Carregar Origens</button>
      </div>
      <div id="origensArea" style="font-size:13px"></div>
    </div>
    <div class="card">
      <div class="card-title">Resposta JSON</div>
      <pre id="jsonViewer">—</pre>
    </div>
  </div>

  <!-- ══ SQL ERP ══ -->
  <div class="panel" id="panel-sqlerp">
    <div class="card">
      <div class="card-title">SQLs de Conferência do ERP</div>
      <button onclick="loadSQLs()">Carregar SQLs</button>
    </div>
    <div id="sqlArea"></div>
  </div>

</div>

<script>
  /* ════════════════════════════════════════════════════
     STATE
  ════════════════════════════════════════════════════ */
  let token    = localStorage.getItem('cadastro_guiado_token')  || '';
  let sessaoId = localStorage.getItem('cadastro_guiado_sessao') || '';
  let sessaoData = null;

  /* ════════════════════════════════════════════════════
     TABS
  ════════════════════════════════════════════════════ */
  function showTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-'   + name).classList.add('active');
    document.getElementById('panel-' + name).classList.add('active');
  }

  /* ════════════════════════════════════════════════════
     API HELPER
  ════════════════════════════════════════════════════ */
  async function api(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const r = await fetch(path, { headers, ...opts });
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      throw Object.assign(new Error(e.detail || r.statusText), { status: r.status });
    }
    return r.json();
  }

  /* ════════════════════════════════════════════════════
     AUTH
  ════════════════════════════════════════════════════ */
  async function doLogin() {
    const user = document.getElementById('loginUser').value.trim();
    const pass = document.getElementById('loginPass').value;
    if (!user || !pass) { document.getElementById('loginErr').textContent = 'Preencha usuário e senha.'; return; }
    document.getElementById('loginBtn').textContent = 'Aguarde...';
    try {
      const d = await api('/auth/login', { method: 'POST', body: JSON.stringify({ username: user, password: pass }) });
      token = d.access_token;
      localStorage.setItem('cadastro_guiado_token', token);
      document.getElementById('loginOverlay').style.display = 'none';
      document.getElementById('userBadge').textContent = user;
      await validateStoredSession();
      loadHealth();
    } catch(e) {
      document.getElementById('loginErr').textContent = e.message;
    } finally {
      document.getElementById('loginBtn').textContent = 'Entrar';
    }
  }

  function doLogout() {
    token = ''; sessaoId = ''; sessaoData = null;
    localStorage.removeItem('cadastro_guiado_token');
    localStorage.removeItem('cadastro_guiado_sessao');
    location.reload();
  }

  /* ════════════════════════════════════════════════════
     SESSION
  ════════════════════════════════════════════════════ */
  async function validateStoredSession() {
    if (!token || !sessaoId) return;
    try {
      const d = await api('/cadastro-guiado/sessao/' + sessaoId);
      sessaoData = d;
      applySession(d);
    } catch(e) {
      if (e.status === 404 || e.status === 403) {
        sessaoId = ''; sessaoData = null;
        localStorage.removeItem('cadastro_guiado_sessao');
        document.getElementById('sessionBadge').textContent = 'Sessão encerrada';
      }
    }
  }

  function applySession(d) {
    if (!d) return;
    sessaoData = d;
    document.getElementById('sessionBadge').textContent = 'Sessão ' + d.sessao_id.slice(0,8);
    renderProgress(d.progresso || 0);
    renderSimilares(d.similares || []);

    const msgs = d.historico || [];
    const box = document.getElementById('chatMsgs');
    box.innerHTML = '';
    msgs.forEach(m => addMsg(m.origem === 'user' ? 'user' : 'bot', m.mensagem));

    if (d.pergunta) { const pb = document.getElementById('perguntaBox'); pb.textContent = d.pergunta; pb.style.display = 'block'; }
    if (d.cadastro_sugerido) renderizarProposta(d.cadastro_sugerido);
  }

  async function iniciarCadastro() {
    const txt = document.getElementById('textoInicial').value.trim();
    if (!txt) return;
    if (!token) { alert('Faça login primeiro.'); return; }
    try {
      const d = await api('/cadastro-guiado/iniciar', {
        method: 'POST',
        body: JSON.stringify({ texto_inicial: txt, empresa: 1 })
      });
      sessaoId = d.sessao_id;
      localStorage.setItem('cadastro_guiado_sessao', sessaoId);
      applySession(d);
      addMsg('bot', d.mensagem);
      if (d.pergunta) { const pb = document.getElementById('perguntaBox'); pb.textContent = d.pergunta; pb.style.display = 'block'; }
    } catch(e) { addMsg('err', 'Erro: ' + e.message); }
  }

  async function enviarResposta() {
    const inp = document.getElementById('chatInput');
    const txt = inp.value.trim();
    if (!txt || !sessaoId) return;
    inp.value = '';
    addMsg('user', txt);
    document.getElementById('perguntaBox').style.display = 'none';
    try {
      const d = await api('/cadastro-guiado/responder', {
        method: 'POST',
        body: JSON.stringify({ sessao_id: sessaoId, resposta: txt })
      });
      sessaoData = d;
      addMsg('bot', d.mensagem);
      if (d.pergunta) { const pb = document.getElementById('perguntaBox'); pb.textContent = d.pergunta; pb.style.display = 'block'; }
      renderProgress(d.progresso || 0);
      renderSimilares(d.similares || []);
      if (d.cadastro_sugerido) renderizarProposta(d.cadastro_sugerido);
    } catch(e) {
      if (e.status === 404) {
        addMsg('err', 'Sessão perdida. Clique em "Nova Sessão" e inicie novamente.');
        sessaoId = ''; localStorage.removeItem('cadastro_guiado_sessao');
      } else { addMsg('err', 'Erro: ' + e.message); }
    }
  }

  function limparSessao() {
    sessaoId = ''; sessaoData = null;
    localStorage.removeItem('cadastro_guiado_sessao');
    document.getElementById('chatMsgs').innerHTML = '';
    document.getElementById('perguntaBox').style.display = 'none';
    document.getElementById('sessionBadge').textContent = 'Sem sessão';
    document.getElementById('progressFill').style.width = '0';
    document.getElementById('stepDots').innerHTML = '';
    document.getElementById('simList').innerHTML = '<span style="color:var(--muted);font-size:13px">Inicie uma sessão para ver similares.</span>';
    document.getElementById('propostaArea').innerHTML = '<span style="color:var(--muted);font-size:13px">Proposta ainda não gerada.</span>';
    document.getElementById('textoInicial').value = '';
  }

  /* ════════════════════════════════════════════════════
     UI HELPERS
  ════════════════════════════════════════════════════ */
  function addMsg(tipo, txt) {
    const box = document.getElementById('chatMsgs');
    const d = document.createElement('div');
    d.className = 'msg ' + tipo;
    d.textContent = txt;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
  }

  function renderProgress(pct) {
    document.getElementById('progressFill').style.width = pct + '%';
    const steps = 5;
    const filled = Math.round(pct / (100 / steps));
    const cont = document.getElementById('stepDots');
    cont.innerHTML = '';
    for (let i = 0; i < steps; i++) {
      const d = document.createElement('div'); d.className = 'step-dot';
      if (i < filled) d.classList.add('done');
      else if (i === filled) d.classList.add('active');
      cont.appendChild(d);
    }
  }

  function renderSimilares(sims) {
    const box = document.getElementById('simList');
    if (!sims || !sims.length) {
      box.innerHTML = '<span style="color:var(--muted);font-size:13px">Nenhum similar encontrado para este item.</span>';
      return;
    }
    box.innerHTML = sims.map(s => `
      <div class="similar-item">
        <div class="similar-cod">${s.codigo || '—'}</div>
        <div class="similar-des">${s.descricao || '—'}</div>
        <div class="similar-tags">
          ${s.familia  ? `<span class="tag">FAM: ${s.familia}</span>` : ''}
          ${s.origem   ? `<span class="tag">ORI: ${s.origem}</span>` : ''}
          ${s.unidade  ? `<span class="tag">UN: ${s.unidade}</span>` : ''}
          ${s.tipo_cadastro ? `<span class="tag">${s.tipo_cadastro}</span>` : ''}
        </div>
      </div>`).join('');
  }

  function renderizarProposta(cad) {
    cad = cad || (sessaoData && sessaoData.cadastro_sugerido);
    const box = document.getElementById('propostaArea');
    if (!cad) { box.innerHTML = '<span style="color:var(--muted);font-size:13px">Proposta ainda não gerada.</span>'; return; }
    const f = (lbl, val) => val ? `<div class="prop-field"><span class="prop-label">${lbl}</span><span class="prop-val">${val}</span></div>` : '';
    box.innerHTML = `
      ${f('Tipo',          cad.tipo_cadastro)}
      ${f('Código sugerido', cad.codigo_sugerido)}
      ${f('Descrição',     cad.descricao)}
      ${f('Família',       cad.familia)}
      ${f('Origem',        cad.origem)}
      ${f('Unidade',       cad.unidade)}
      ${f('NCM',           cad.ncm)}
      ${f('Medida',        cad.medida)}
      ${(cad.pendencias||[]).map(p => `<div class="pendencia">⚠ ${p}</div>`).join('')}
      ${(cad.alertas||[]).map(a => `<div class="alerta">🔴 ${a}</div>`).join('')}
    `;
  }

  /* ════════════════════════════════════════════════════
     VALIDAR / RELATÓRIO
  ════════════════════════════════════════════════════ */
  async function validarCadastro() {
    if (!sessaoId) { alert('Inicie uma sessão primeiro.'); return; }
    try {
      const d = await api('/cadastro-guiado/validar/' + sessaoId, { method: 'POST' });
      renderizarProposta(d.cadastro_sugerido);
      document.getElementById('validacaoResult').textContent = 'Status: ' + d.status
        + (d.pendencias.length ? ' — Pendências: ' + d.pendencias.join('; ') : ' — OK');
    } catch(e) { document.getElementById('validacaoResult').textContent = 'Erro: ' + e.message; }
  }

  async function gerarRelatorio() {
    if (!sessaoId) { document.getElementById('reportOutput').textContent = 'Sem sessão ativa.'; return; }
    try {
      const d = await api('/cadastro-guiado/relatorio/' + sessaoId);
      document.getElementById('reportOutput').textContent = d.relatorio_texto || 'Sem conteúdo.';
    } catch(e) { document.getElementById('reportOutput').textContent = 'Erro: ' + e.message; }
  }

  /* ════════════════════════════════════════════════════
     DIAGNÓSTICO
  ════════════════════════════════════════════════════ */
  async function loadHealth() {
    try {
      const d = await fetch('/health').then(r => r.json());
      const b = document.getElementById('usu_ia_badge');
      b.textContent = 'usu_ia: ' + (d.usu_ia_disponivel ? 'OK' : 'ausente');
      b.className = 'badge ' + (d.usu_ia_disponivel ? 'ok' : 'warn');
      const eq = document.getElementById('erpcacheBadge');
      eq.textContent = 'ERP: ' + (d.erp_cache_carregado_em ? d.erp_cache_carregado_em.slice(11,19) : '—');
      document.getElementById('jsonViewer').textContent = JSON.stringify(d, null, 2);
    } catch(e) { console.warn('health error', e); }
  }

  async function loadContexto() {
    try {
      const d = await api('/erp/contexto');
      document.getElementById('jsonViewer').textContent = JSON.stringify(d, null, 2);
      showTab('diag');
    } catch(e) { alert('Erro: ' + e.message); }
  }

  async function recarregarContexto() {
    try {
      const d = await api('/erp/recarregar-contexto', { method: 'POST' });
      addMsg('system', d.mensagem || 'Recarga iniciada.');
      setTimeout(loadHealth, 3000);
    } catch(e) { alert('Erro: ' + e.message); }
  }

  async function loadDiag() {
    const box = document.getElementById('diagItems');
    const sum = document.getElementById('diagSummary');
    box.innerHTML = '<span style="color:var(--muted)">Carregando...</span>';
    try {
      const d = await api('/diagnostico/api-contexto');
      document.getElementById('jsonViewer').textContent = JSON.stringify(d, null, 2);
      const r = d.resumo || {};
      sum.innerHTML = `
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:4px">
          <span class="diag-status PASSOU">${r.PASSOU||0} PASSOU</span>
          <span class="diag-status FALHOU">${r.FALHOU||0} FALHOU</span>
          <span class="diag-status AVISO">${r.AVISO||0} AVISO</span>
          <span class="diag-status INFO">${r.INFO||0} INFO</span>
          <span style="font-size:12px;color:${d.pronto_para_uso?'var(--success)':'var(--danger)'};
                margin-left:8px;font-weight:600">
            ${d.pronto_para_uso ? '✓ PRONTO PARA USO' : '✗ ATENÇÃO: há falhas'}</span>
        </div>`;
      box.innerHTML = (d.itens || []).map(i => `
        <div class="diag-item">
          <span class="diag-status ${i.status}">${i.status}</span>
          <span class="diag-item-name">${i.item}</span>
          <span class="diag-item-detail">${i.detalhe}</span>
        </div>`).join('');
    } catch(e) { box.innerHTML = '<span style="color:var(--danger)">' + e.message + '</span>'; }
  }

  async function loadFamilias() {
    try {
      const d = await api('/erp/familias');
      const rows = (d.familias || []).slice(0, 60);
      document.getElementById('familiasArea').innerHTML =
        `<b style="font-size:11px;color:var(--muted)">${d.total} famílias · top 60 mostradas</b><br><br>` +
        rows.map(f => `<span style="margin-right:16px">${f.codfam||f.CODFAM} <span style="color:var(--muted)">(${f.qtd_produtos||f.QTD_PRODUTOS||0})</span></span>`).join('');
      document.getElementById('jsonViewer').textContent = JSON.stringify(d, null, 2);
    } catch(e) { document.getElementById('familiasArea').textContent = 'Erro: ' + e.message; }
  }

  async function loadOrigens() {
    try {
      const d = await api('/erp/origens');
      const rows = (d.origens || []).slice(0, 60);
      document.getElementById('origensArea').innerHTML =
        `<b style="font-size:11px;color:var(--muted)">${d.total} origens · top 60 mostradas</b><br><br>` +
        rows.map(o => `<span style="margin-right:16px">${o.codori||o.CODORI} <span style="color:var(--muted)">(${o.qtd_produtos||o.QTD_PRODUTOS||0})</span></span>`).join('');
      document.getElementById('jsonViewer').textContent = JSON.stringify(d, null, 2);
    } catch(e) { document.getElementById('origensArea').textContent = 'Erro: ' + e.message; }
  }

  /* ════════════════════════════════════════════════════
     SQL ERP
  ════════════════════════════════════════════════════ */
  async function loadSQLs() {
    try {
      const d = await api('/erp/sql-diagnostico');
      const area = document.getElementById('sqlArea');
      area.innerHTML = (d.sqls || []).map(s => `
        <div class="sql-block">
          <div class="sql-title">${s.titulo}</div>
          <div style="font-size:12px;color:var(--muted);margin-bottom:6px">${s.descricao||''}</div>
          <pre>${s.sql.replace(/</g,'&lt;')}</pre>
          <button class="sec sm" onclick="navigator.clipboard.writeText(${JSON.stringify(s.sql)})">Copiar</button>
        </div>`).join('');
    } catch(e) { document.getElementById('sqlArea').innerHTML = '<span style="color:var(--danger)">' + e.message + '</span>'; }
  }

  /* ════════════════════════════════════════════════════
     INIT
  ════════════════════════════════════════════════════ */
  window.addEventListener('DOMContentLoaded', async () => {
    if (token) {
      document.getElementById('loginOverlay').style.display = 'none';
      document.getElementById('userBadge').textContent = '—';
      await validateStoredSession();
      loadHealth();
    }
  });
</script>
</body>
</html>
"""



# =========================================================
# STARTUP
# =========================================================
@app.on_event("startup")
async def startup_event() -> None:
    """Carrega contexto do ERP na inicialização — não falha se DB inacessível."""
    import threading
    threading.Thread(target=carregar_contexto_erp, args=(EMPRESA_PADRAO,), daemon=True).start()


# =========================================================
# ROTAS
# =========================================================
@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return HTML_PAGE


@app.post("/erp/recarregar-contexto")
def recarregar_contexto(username: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Força recarga do cache de contexto do ERP. Requer autenticação."""
    import threading
    threading.Thread(target=carregar_contexto_erp, args=(EMPRESA_PADRAO,), daemon=True).start()
    return {"status": "recarga_iniciada", "mensagem": "Cache do ERP será atualizado em segundo plano."}


@app.get("/erp/contexto")
def erp_contexto(username: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Retorna o contexto completo carregado do ERP para diagnóstico."""
    return {
        "usu_ia_disponivel": ERP_CACHE.get("usu_ia_disponivel", False),
        "carregado_em": ERP_CACHE.get("carregado_em"),
        "erro_carga": ERP_CACHE.get("erro_carga"),
        "tipos_produto": ERP_CACHE.get("tipos_produto", {}),
        "qtd_servicos": ERP_CACHE.get("qtd_servicos", 0),
        "qtd_familias": len(ERP_CACHE.get("familias", [])),
        "qtd_origens": len(ERP_CACHE.get("origens", [])),
        "qtd_exemplos_ativos": len(ERP_CACHE.get("exemplos_ativos", [])),
        "qtd_exemplos_uso_consumo": len(ERP_CACHE.get("exemplos_uso_consumo", [])),
        "qtd_contexto_cadastro": len(ERP_CACHE.get("contexto_cadastro", [])),
        "qtd_politica_reaproveitamento": len(ERP_CACHE.get("politica_reaproveitamento", [])),
        "contexto_setor": ERP_CACHE.get("contexto_setor", []),
        "exemplos_uso_consumo_amostra": ERP_CACHE.get("exemplos_uso_consumo", [])[:20],
    }


@app.get("/erp/diagnostico")
def erp_diagnostico(empresa: int = EMPRESA_PADRAO, username: str = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Diagnóstico automático de toda a infraestrutura usu_ia.* diretamente no banco.
    Espelha a lógica do script usu_ia_conferencia.sql com PASSOU / FALHOU / AVISO / INFO.
    """
    itens: List[Dict[str, str]] = []

    def _check(item: str, passou: bool, detalhe: str, nivel: str = "FALHOU") -> None:
        itens.append({
            "item": item,
            "status": "PASSOU" if passou else nivel,
            "detalhe": detalhe,
        })

    # 1. Schema usu_ia
    r = execute_query_safe("SELECT COUNT(*) AS N FROM sys.schemas WHERE name='usu_ia'")
    schema_ok = bool(r and int(r[0].get("N") or r[0].get("n") or 0) > 0)
    _check("Schema usu_ia", schema_ok,
           "Schema encontrado" if schema_ok else "Execute usu_ia_base_auxiliar.sql")

    # 2. Tabelas
    tabelas = ["CONTEXTO_CADASTRO", "EXEMPLOS_CADASTRO", "SESSAO_CHATBOT",
               "SESSAO_CHATBOT_MSG", "POLITICA_REAPROVEITAMENTO"]
    for tab in tabelas:
        r = execute_query_safe(
            "SELECT COUNT(*) AS N FROM sys.tables t "
            "JOIN sys.schemas s ON s.schema_id=t.schema_id "
            "WHERE s.name='usu_ia' AND t.name=?", (tab,)
        )
        ok = bool(r and int(r[0].get("N") or r[0].get("n") or 0) > 0)
        _check(f"Tabela usu_ia.{tab}", ok,
               "Tabela encontrada" if ok else "Tabela não encontrada")

    # 3. Views
    views = ["VW_PRODUTOS_BASE", "VW_SERVICOS_BASE", "VW_FAMILIAS_USADAS",
             "VW_ORIGENS_USADAS", "VW_TIPOS_CADASTRO_USADOS",
             "VW_ITENS_EXIGEM_REVISAO_FISCAL", "VW_DASH_TIPOS_CADASTRO",
             "VW_DASH_FAMILIAS", "VW_DASH_ORIGENS",
             "VW_SIMILARES_PRODUTO", "VW_SIMILARES_SERVICO"]
    for v in views:
        r = execute_query_safe(
            "SELECT COUNT(*) AS N FROM sys.views v "
            "JOIN sys.schemas s ON s.schema_id=v.schema_id "
            "WHERE s.name='usu_ia' AND v.name=?", (v,)
        )
        ok = bool(r and int(r[0].get("N") or r[0].get("n") or 0) > 0)
        _check(f"View usu_ia.{v}", ok,
               "View encontrada" if ok else "View não encontrada")

    # 4. Contagem de tabelas (carga inicial)
    for tab, col, label, minimo in [
        ("CONTEXTO_CADASTRO",         f"WHERE EMPRESA={empresa} AND ATIVO='S'", "regras ativas", 5),
        ("EXEMPLOS_CADASTRO",         f"WHERE EMPRESA={empresa}",               "exemplos",      1),
        ("POLITICA_REAPROVEITAMENTO", f"WHERE EMPRESA={empresa} AND ATIVO='S'", "políticas",     4),
    ]:
        r = execute_query_safe(f"SELECT COUNT(*) AS N FROM usu_ia.{tab} {col}")
        n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
        _check(f"Carga {tab}", n >= minimo,
               f"{label}: {n} (esperado ≥ {minimo})")

    # 5. Contagem das views (dados do ERP)
    for view, filtro, label in [
        ("VW_PRODUTOS_BASE",               f"WHERE CODEMP={empresa}", "linhas"),
        ("VW_SERVICOS_BASE",               f"WHERE CODEMP={empresa}", "linhas"),
        ("VW_DASH_TIPOS_CADASTRO",         f"WHERE CODEMP={empresa}", "tipos"),
        ("VW_DASH_FAMILIAS",               f"WHERE CODEMP={empresa}", "famílias"),
        ("VW_DASH_ORIGENS",                f"WHERE CODEMP={empresa}", "origens"),
    ]:
        r = execute_query_safe(f"SELECT COUNT(*) AS N FROM usu_ia.{view} {filtro}")
        n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
        _check(f"Dados {view}", n > 0, f"{label}: {n}")

    # Revisão fiscal — informativo
    r = execute_query_safe(
        f"SELECT COUNT(*) AS N FROM usu_ia.VW_ITENS_EXIGEM_REVISAO_FISCAL WHERE CODEMP={empresa}"
    )
    n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
    itens.append({"item": "Dados VW_ITENS_EXIGEM_REVISAO_FISCAL",
                  "status": "INFO",
                  "detalhe": f"Itens com pendência fiscal: {n} (0 = base bem parametrizada)"})

    # 6. Família CONSUM
    r = execute_query_safe(
        f"SELECT COUNT(*) AS N FROM usu_ia.VW_DASH_FAMILIAS WHERE CODEMP={empresa} AND CODFAM='CONSUM'"
    )
    n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
    _check("Família CONSUM presente", n > 0,
           "CONSUM encontrada — uso_consumo inferido corretamente" if n > 0
           else "CONSUM ausente — inferência de uso_consumo pode ser afetada",
           nivel="AVISO")

    # 7. Origem 100
    r = execute_query_safe(
        f"SELECT COUNT(*) AS N FROM usu_ia.VW_DASH_ORIGENS WHERE CODEMP={empresa} AND CODORI='100'"
    )
    n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
    _check("Origem 100 presente (GER-075DERAO01)", n > 0,
           "Origem 100 ativa — regra fiscal GER-075DERAO01 funcionará" if n > 0
           else "Origem 100 ausente — revisão fiscal sempre ativada",
           nivel="AVISO")

    # 8. Similares produto (termos frequentes — busca genérica)
    r = execute_query_safe(
        f"SELECT COUNT(*) AS N FROM usu_ia.VW_SIMILARES_PRODUTO "
        f"WHERE CODEMP={empresa} AND SITPRO='A'"
    )
    n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
    _check("VW_SIMILARES_PRODUTO tem registros ativos", n > 0,
           f"Produtos ativos disponíveis para busca: {n}")

    # 9. Similares serviço
    r = execute_query_safe(
        f"SELECT COUNT(*) AS N FROM usu_ia.VW_SIMILARES_SERVICO WHERE CODEMP={empresa}"
    )
    n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
    _check("VW_SIMILARES_SERVICO tem registros", n > 0,
           f"Serviços disponíveis para busca: {n}")

    # 10. Exemplos por tipo
    for tipo in ["uso_consumo", "materia_prima", "produto_produzido", "servico"]:
        r = execute_query_safe(
            "SELECT COUNT(*) AS N FROM usu_ia.EXEMPLOS_CADASTRO "
            "WHERE EMPRESA=? AND TIPO_CADASTRO=?", (empresa, tipo)
        )
        n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
        _check(f"Exemplos {tipo}", n > 0, f"Registros: {n}", nivel="AVISO")

    # 11. Contexto por setor
    for setor in ["manutencao", "almoxarifado", "producao", "compras", "fiscal"]:
        r = execute_query_safe(
            "SELECT COUNT(*) AS N FROM usu_ia.CONTEXTO_CADASTRO "
            "WHERE EMPRESA=? AND ATIVO='S' AND SETOR=?", (empresa, setor)
        )
        n = int(r[0].get("N") or r[0].get("n") or 0) if r else 0
        _check(f"Contexto setor: {setor}", n > 0, f"Regras ativas: {n}")

    # 12. Sessão acessível
    r = execute_query_safe("SELECT COUNT(*) AS N FROM usu_ia.SESSAO_CHATBOT")
    n = int(r[0].get("N") or r[0].get("n") or 0) if r else -1
    itens.append({"item": "Tabela SESSAO_CHATBOT acessível",
                  "status": "PASSOU" if n >= 0 else "FALHOU",
                  "detalhe": f"Sessões registradas: {n} (0 = normal antes da 1ª conversa)"})

    # Resumo
    passou  = sum(1 for i in itens if i["status"] == "PASSOU")
    falhou  = sum(1 for i in itens if i["status"] == "FALHOU")
    aviso   = sum(1 for i in itens if i["status"] == "AVISO")
    info    = sum(1 for i in itens if i["status"] == "INFO")
    total   = len(itens)

    return {
        "empresa": empresa,
        "total_verificacoes": total,
        "resumo": {"PASSOU": passou, "FALHOU": falhou, "AVISO": aviso, "INFO": info},
        "pronto_para_uso": falhou == 0,
        "itens": itens,
        "falhas": [i for i in itens if i["status"] == "FALHOU"],
    }




@app.get("/erp/familias")
def erp_familias(username: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Retorna all famílias carregadas do ERP (E012FAM) com quantidade de produtos."""
    return {
        "total": len(ERP_CACHE.get("familias", [])),
        "carregado_em": ERP_CACHE.get("carregado_em"),
        "familias": ERP_CACHE.get("familias", []),
    }


@app.get("/erp/origens")
def erp_origens(username: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Retorna todas as origens carregadas do ERP (E083ORI) com quantidade de produtos."""
    return {
        "total": len(ERP_CACHE.get("origens", [])),
        "carregado_em": ERP_CACHE.get("carregado_em"),
        "origens": ERP_CACHE.get("origens", []),
    }


@app.get("/erp/sql-diagnostico")
def erp_sql_diagnostico(username: str = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Retorna as SQLs de diagnóstico que podem ser executadas diretamente no ERP
    para validar o contexto da IA.
    """
    empresa = EMPRESA_PADRAO
    return {
        "descricao": "SQLs para diagnóstico do contexto da IA no ERP Senior",
        "sqls": [
            {
                "titulo": "Tipos de produto usados (TIPPRO)",
                "sql": f"SELECT TIPPRO, COUNT(*) AS QTD FROM E075PRO WHERE CODEMP = {empresa} GROUP BY TIPPRO ORDER BY QTD DESC",
            },
            {
                "titulo": "Quantidade de serviços cadastrados",
                "sql": f"SELECT COUNT(*) AS QTD_SERVICOS FROM E080SER WHERE CODEMP = {empresa}",
            },
            {
                "titulo": "Famílias mais usadas",
                "sql": f"""
SELECT F.CODFAM, F.DESFAM, F.DEPPAD, COUNT(P.CODPRO) AS QTD
FROM E012FAM F
LEFT JOIN E075PRO P ON P.CODEMP = F.CODEMP AND P.CODFAM = F.CODFAM
WHERE F.CODEMP = {empresa}
GROUP BY F.CODFAM, F.DESFAM, F.DEPPAD
ORDER BY QTD DESC, F.CODFAM""",
            },
            {
                "titulo": "Origens usadas (com CTRSEP)",
                "sql": f"""
SELECT O.CODORI, O.DESORI, O.DEPPAD, O.CTRSEP, COUNT(P.CODPRO) AS QTD
FROM E083ORI O
LEFT JOIN E075PRO P ON P.CODEMP = O.CODEMP AND P.CODORI = O.CODORI
WHERE O.CODEMP = {empresa}
GROUP BY O.CODORI, O.DESORI, O.DEPPAD, O.CTRSEP
ORDER BY QTD DESC, O.CODORI""",
            },
            {
                "titulo": "Produtos ativos com perfil de uso e consumo (CODFAM=CONSUM)",
                "sql": f"""
SELECT TOP 200 P.CODPRO, P.DESPRO, P.CODFAM, P.CODORI, P.TIPPRO, P.UNIMED
FROM E075PRO P
WHERE P.CODEMP = {empresa}
  AND P.SITPRO = 'A'
  AND (P.CODFAM = 'CONSUM' OR UPPER(P.DESPRO) LIKE '%CONSUM%')
ORDER BY P.DESPRO""",
            },
            {
                "titulo": "Herança de depósito por produto",
                "sql": f"""
SELECT TOP 200 P.CODPRO, P.CODFAM, P.CODORI, D.CODDER,
    D.DEPPAD AS DEP_DERIVACAO, P.DEPPAD AS DEP_PRODUTO,
    F.DEPPAD AS DEP_FAMILIA, O.DEPPAD AS DEP_ORIGEM
FROM E075PRO P
LEFT JOIN E075DER D ON D.CODEMP = P.CODEMP AND D.CODPRO = P.CODPRO
LEFT JOIN E012FAM F ON F.CODEMP = P.CODEMP AND F.CODFAM = P.CODFAM
LEFT JOIN E083ORI O ON O.CODEMP = P.CODEMP AND O.CODORI = P.CODORI
WHERE P.CODEMP = {empresa}
ORDER BY P.CODPRO, D.CODDER""",
            },
            {
                "titulo": "Script para criar tabela de contexto por setor (opcional)",
                "sql": """
CREATE TABLE USU_IA_CONTEXTO_SETOR (
    SETOR         VARCHAR(50)  NOT NULL,
    TIPO_CADASTRO VARCHAR(30)  NOT NULL,
    CODFAM        VARCHAR(30)  NOT NULL,
    CODORI        VARCHAR(30)  NOT NULL,
    PRIORIDADE    INT          NOT NULL DEFAULT 99
);
-- Exemplo de dados:
-- INSERT INTO USU_IA_CONTEXTO_SETOR VALUES ('manutencao','uso_consumo','CONSUM','100',1);
-- INSERT INTO USU_IA_CONTEXTO_SETOR VALUES ('producao','materia_prima','MATPRI','200',1);""",
            },
        ],
    }


@app.post("/auth/login", response_model=TokenResponse)
def login(data: LoginRequest) -> TokenResponse:
    username = clean_str(data.username).upper()
    password = clean_str(data.password)
    if username not in USERS or USERS[username] != password:
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")
    return create_token(username)


@app.post("/cadastro-guiado/iniciar", response_model=SessaoResponse)
def iniciar_cadastro(data: IniciarCadastroRequest, username: str = Depends(get_current_user)) -> SessaoResponse:
    sessao = create_session(username, data.empresa, data.texto_inicial)
    roteiro = sessao["roteiro_atual"]
    slots = sessao["slots"]

    if sessao.get("similares"):
        top = sessao["similares"][0]
        sessao["campo_em_coleta"] = None  # aguardando sim/não antes de continuar
        return session_to_response(
            sessao,
            f"Encontrei {len(sessao['similares'])} item(ns) parecido(s) no ERP. O mais aderente é {top.get('codigo')} — {top.get('descricao')}.",
            "Algum desses cadastros já atende sua necessidade? Responda sim ou não.",
        )

    # Sem similares: inicia coleta pelo roteiro contextual
    campo = sessao.get("campo_em_coleta") or next_campo_roteiro(roteiro, slots)
    sessao["campo_em_coleta"] = campo

    # Monta mensagem de abertura contextual com base no tipo detectado
    tipo = clean_str(slots.get("tipo_cadastro") or "uso_consumo")
    if tipo == "servico":
        abertura = "Entendi que é um serviço. Vou seguir a trilha de serviço (E080SER), que tem campos próprios."
    elif tipo == "materia_prima":
        abertura = "Certo, matéria-prima. Vou te guiar para coletar especificação, medida e origem corretamente."
    elif tipo == "produto_produzido":
        abertura = "Produto fabricado internamente. Vou te guiar pela trilha de produto produzido."
    else:
        abertura = "Não encontrei similar claro. Vou te guiar para montar o cadastro correto."

    return session_to_response(
        sessao,
        abertura,
        pergunta_para_campo(campo, roteiro) if campo else None,
    )


@app.get("/cadastro-guiado/sessao/{sessao_id}", response_model=SessaoResponse)
def obter_sessao(sessao_id: str, username: str = Depends(get_current_user)) -> SessaoResponse:
    sessao = get_or_load_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if sessao["usuario"] != username:
        raise HTTPException(status_code=403, detail="Sessão pertence a outro usuário")
    return session_to_response(sessao, "Sessão carregada")


@app.post("/cadastro-guiado/responder", response_model=SessaoResponse)
def responder_cadastro(data: ResponderCadastroRequest, username: str = Depends(get_current_user)) -> SessaoResponse:
    sessao = get_or_load_sessao(data.sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if sessao["usuario"] != username:
        raise HTTPException(status_code=403, detail="Sessão pertence a outro usuário")

    resposta = clean_str(data.resposta)
    sessao["historico"].append({
        "origem": "user",
        "mensagem": resposta,
        "data_hora": datetime.now().isoformat(),
    })
    # Loga mensagem no banco
    db_log_msg(data.sessao_id, "user", resposta, etapa=sessao.get("etapa_atual"))

    def _salva_e_responde(msg: str, pergunta: Optional[str] = None) -> SessaoResponse:
        sessao["historico"].append({"origem": "assistant", "mensagem": msg, "data_hora": datetime.now().isoformat()})
        db_salvar_sessao(sessao)
        db_log_msg(data.sessao_id, "assistant", msg, etapa=sessao.get("etapa_atual"))
        return session_to_response(sessao, msg, pergunta)

    campo_em_coleta: Optional[str] = sessao.get("campo_em_coleta")
    slots = sessao["slots"]

    # -------------------------------------------------------
    # 1. AGUARDANDO CONFIRMAÇÃO DE SIMILAR (sim/não)
    # -------------------------------------------------------
    if sessao.get("aguardando_confirmacao_similar"):
        if yes_answer(resposta):
            top = sessao["similares"][0]
            sessao["etapa_atual"] = "encerrado_por_reaproveitamento"
            sessao["campo_em_coleta"] = None
            sessao["cadastro_sugerido"] = CadastroSugerido(
                tipo_cadastro=slots.get("tipo_cadastro", "uso_consumo"),
                codigo_sugerido=clean_str(top.get("codigo")) or None,
                descricao=clean_str(top.get("descricao")) or None,
                familia=clean_str(top.get("familia")) or None,
                origem=clean_str(top.get("origem")) or None,
                unidade=clean_str(slots.get("unidade")) or None,
                parametros_fiscais_sugeridos=["Reaproveitar cadastro existente e apenas validar enquadramento final."],
                pendencias=[],
                alertas=["Usuário informou que o similar existente pode atender."],
            ).model_dump()
            return _salva_e_responde("Perfeito. Vou sugerir o reaproveitamento do cadastro existente, evitando duplicidade.")

        if no_answer(resposta):
            sessao["aguardando_confirmacao_similar"] = False
            sessao["etapa_atual"] = "coletar_dados"

            novos = inferir_slots_do_texto(resposta, slots, None)
            merge_slots(slots, novos)
            slots["motivo_rejeicao_similar"] = resposta

            roteiro = definir_roteiro(slots)
            sessao["roteiro_atual"] = roteiro
            slots["roteiro"] = roteiro

            campo = next_campo_roteiro(roteiro, slots)
            sessao["campo_em_coleta"] = campo

            return _salva_e_responde(
                "Certo. Então vou te ajudar a montar o cadastro correto. Os similares encontrados servirão de referência para família e origem.",
                pergunta_para_campo(campo, roteiro) if campo else None,
            )

        # Resposta ambígua
        return _salva_e_responde(
            "Preciso que você confirme se algum dos similares atende ou não.",
            "Os itens existentes podem atender? Responda sim ou não.",
        )

    # -------------------------------------------------------
    # 2. COLETA DE DADOS — resposta preenche campo_em_coleta
    # -------------------------------------------------------
    novos = inferir_slots_do_texto(resposta, slots, campo_em_coleta)
    merge_slots(slots, novos)

    if campo_em_coleta and clean_str(slots.get(campo_em_coleta)):
        sessao["campo_em_coleta"] = None

    roteiro = definir_roteiro(slots)
    sessao["roteiro_atual"] = roteiro
    slots["roteiro"] = roteiro

    descricao_busca = (
        clean_str(slots.get("descricao_base"))
        or clean_str(slots.get("especificacao_principal"))
        or resposta
    )
    tipo = clean_str(slots.get("tipo_cadastro") or "uso_consumo")
    sessao["similares"] = (
        find_similar_services(sessao["empresa"], descricao_busca)
        if tipo == "servico"
        else find_similar_products(sessao["empresa"], descricao_busca)
    )

    proximo_campo = next_campo_roteiro(roteiro, slots)
    sessao["campo_em_coleta"] = proximo_campo

    if proximo_campo:
        sessao["etapa_atual"] = f"aguardando_{proximo_campo}"

        msgs_confirmacao = {
            "equipamento": "Entendido.",
            "finalidade": f"Certo, já registrei o equipamento como '{clean_str(slots.get('equipamento') or '')}'. Agora:",
            "especificacao_principal": f"Perfeito, já entendi que a finalidade é '{clean_str(slots.get('finalidade') or '')}'. O que falta agora é:",
            "medida": "Ótimo, entendido.",
            "unidade": "Certo.",
        }
        msg_confirmacao = msgs_confirmacao.get(proximo_campo, "Entendido.")

        return _salva_e_responde(
            msg_confirmacao,
            pergunta_para_campo(proximo_campo, roteiro),
        )

    # Todos os campos coletados — monta proposta
    sessao["etapa_atual"] = "proposta_pronta"
    sessao["campo_em_coleta"] = None
    sessao["cadastro_sugerido"] = build_suggestion(sessao).model_dump()

    return _salva_e_responde(
        "Pronto. Montei a proposta de cadastro completa com base em tudo que você me informou. "
        "Você pode validar e emitir o relatório final.",
    )


@app.post("/cadastro-guiado/validar/{sessao_id}", response_model=ValidacaoResponse)
def validar_cadastro(sessao_id: str, username: str = Depends(get_current_user)) -> ValidacaoResponse:
    sessao = get_or_load_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if sessao["usuario"] != username:
        raise HTTPException(status_code=403, detail="Sessão pertence a outro usuário")

    cadastro = build_suggestion(sessao)
    sessao["cadastro_sugerido"] = cadastro.model_dump()
    sessao["etapa_atual"] = "validado"
    db_salvar_sessao(sessao)

    return ValidacaoResponse(
        sessao_id=sessao_id,
        status="ok" if not cadastro.pendencias else "pendencias",
        pendencias=cadastro.pendencias,
        alertas=cadastro.alertas,
        cadastro_sugerido=cadastro,
    )


@app.get("/cadastro-guiado/relatorio/{sessao_id}", response_model=RelatorioResponse)
def gerar_relatorio(sessao_id: str, username: str = Depends(get_current_user)) -> RelatorioResponse:
    sessao = get_or_load_sessao(sessao_id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if sessao["usuario"] != username:
        raise HTTPException(status_code=403, detail="Sessão pertence a outro usuário")

    texto, rel_json = relatorio_final(sessao)
    sessao["etapa_atual"] = "relatorio_gerado"
    db_salvar_sessao(sessao)  # persiste estado final
    return RelatorioResponse(sessao_id=sessao_id, relatorio_texto=texto, relatorio_json=rel_json)


# =========================================================
# MAIN
# =========================================================
@app.get("/health")
def health() -> Dict[str, Any]:
    """Health check rápido — sem autenticação — retorna status e resumo do diagnóstico."""
    try:
        diag = diagnostico_api_erp()
    except Exception as exc:
        return {
            "status": "erro",
            "api": app.title,
            "porta": API_PORT,
            "erro": str(exc),
        }
    return {
        "status": "ok" if diag["status_geral"] == "ok" else "atencao",
        "api": app.title,
        "porta": API_PORT,
        "usu_ia_disponivel": ERP_CACHE.get("usu_ia_disponivel", False),
        "erp_cache_carregado_em": ERP_CACHE.get("carregado_em"),
        "diagnostico_resumido": {
            "conexao_sql": diag["conexao_sql"]["ok"],
            "schema_usu_ia": diag["schema_usu_ia"],
            "erp_base_ok": all(o["ok"] for o in diag["objetos_erp_base"]),
            "ia_objetos_ok": all(o["ok"] for o in diag["objetos_ia"]),
            "qtd_falhas": len(diag["falhas"]),
            "falhas": diag["falhas"],
        },
    }


@app.get("/diagnostico/api-contexto")
def diagnostico_api_contexto(username: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Diagnóstico completo da API, ERP e infraestrutura usu_ia.*. Requer autenticação."""
    return diagnostico_api_erp()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT, reload=False)
