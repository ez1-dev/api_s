# Backend: Sugestão Min/Max - Endpoints Implementados

## Status: ✅ COMPLETO

Os três endpoints necessários para a funcionalidade "Sugestão Min/Max" estão completamente implementados e testados.

---

## 1. GET `/api/estoque/movimentacao`

**Descrição**: Consulta movimentação histórica de estoque com análise de padrões de consumo.

### Query Parameters
| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `codpro` | string | Não | Código do produto (filtro parcial) |
| `codder` | string | Não | Código de derivação |
| `coddep` | string | Não | Código de depósito |
| `codfam` | string | Não | Código de família |
| `codori` | string | Não | Código de origem |
| `data_inicial` | string (YYYY-MM-DD) | Não | Data inicial (padrão: 90 dias atrás) |
| `data_final` | string (YYYY-MM-DD) | Não | Data final (padrão: hoje) |
| `pagina` | integer | Não | Página da paginação (padrão: 1) |
| `tamanho_pagina` | integer | Não | Itens por página, máx 500 (padrão: 100) |

### Response (JSON)
```json
{
  "pagina": 1,
  "tamanho_pagina": 100,
  "total_registros": 1250,
  "total_paginas": 13,
  "dados": [
    {
      "codigo": "CAL001",
      "derivacao": "",
      "deposito": "01",
      "data_movimento": "2026-04-20",
      "transacao": "001",
      "tipo_movimento": "Saída",
      "quantidade": 100.5,
      "documento": "PED001",
      "numero_nf": "123456",
      "fornecedor": "FOR001",
      "ordem_producao": "OP001",
      "descricao": "Calço de Alumínio",
      "deposito_padrao": "01",
      "familia": "ESTRUTURA",
      "origem": "NAC"
    }
  ]
}
```

### Tabelas Utilizadas
- `E210MVP` - Movimentação de estoque
- `E075PRO` - Produto
- `E075DER` - Derivação do produto
- `E012FAM` - Família
- `E083ORI` - Origem

---

## 2. GET `/api/estoque/sugestao-politica`

**Descrição**: Gera sugestões automáticas de política Min/Max baseada em histórico de movimentação.

### Query Parameters
| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `codpro` | string | Não | Código do produto (filtro parcial) |
| `codder` | string | Não | Código de derivação |
| `coddep` | string | Não | Código de depósito |
| `periodo_dias` | integer | Não | Período de análise em dias (padrão: 90, min: 30, máx: 365) |

### Response (JSON)
```json
{
  "periodo_dias": 90,
  "data_inicial": "2026-01-21",
  "total_itens": 247,
  "sugestoes": [
    {
      "codigo": "CAL001",
      "derivacao": "",
      "deposito": "01",
      "deposito_padrao": "01",
      "descricao": "Calço de Alumínio",
      "saldo_atual": 450.0,
      "dias_movimento": 52,
      "total_saidas_periodo": 5200.0,
      "consumo_medio_diario": 100.0,
      "consumo_medio_mensal": 3000.0,
      "lead_time_dias": 7,
      "estoque_seguranca": 1500.0,
      "minimo_sugerido": 2200.0,
      "maximo_sugerido": 5200.0,
      "estoque_minimo_atual": 1000.0,
      "estoque_maximo_atual": 3000.0,
      "ponto_pedido_atual": null,
      "lote_compra_atual": null,
      "status": "ABAIXO_MINIMO"
    }
  ]
}
```

### Lógica de Cálculo
- **Consumo Médio Diário**: Total de saídas / Período em dias
- **Lead Time**: Fixo em 7 dias (configurável por produto na tabela `USU_EST_POLITICA`)
- **Estoque de Segurança**: Consumo Médio Diário × 15 dias
- **Mínimo Sugerido**: (Consumo Médio Diário × Lead Time) + Estoque de Segurança
- **Máximo Sugerido**: Mínimo Sugerido + (Consumo Médio Diário × 30)

### Status Possíveis
- `SEM_POLITICA` - Produto sem política definida
- `ABAIXO_MINIMO` - Saldo < Mínimo
- `NO_MINIMO` - Saldo = Mínimo
- `ENTRE_MIN_E_MAX` - Mínimo < Saldo ≤ Máximo
- `ACIMA_MAXIMO` - Saldo > Máximo

### Tabelas Utilizadas
- `E210MVP` - Movimentação de estoque (saídas)
- `E210EST` - Estoque atual
- `E075PRO` - Produto
- `E075DER` - Derivação
- `USU_EST_POLITICA` - Políticas cadastradas

---

## 3. POST `/api/estoque/politica/salvar`

**Descrição**: Cria ou atualiza uma política de estoque Min/Max.

### Request Body (JSON)
```json
{
  "codpro": "CAL001",
  "codder": "",
  "coddep": "01",
  "estoque_minimo": 2200,
  "estoque_maximo": 5200,
  "ponto_pedido": null,
  "lote_compra": null,
  "consumo_medio_mensal": 3000,
  "lead_time_dias": 7,
  "obs": "Sugestão automática baseada em movimentação histórica"
}
```

### Response (JSON)
```json
{
  "mensagem": "Política salva com sucesso."
}
```

### Validação
- `codpro` é obrigatório
- `codder` e `coddep` padrão para string vazia
- `estoque_minimo` e `estoque_maximo` devem ser números positivos

### Comportamento
- Se a política existe (mesmo `codpro`, `codder`, `coddep`): **UPDATE**
- Se não existe: **INSERT**
- Atualiza `DATA_ALT` e `USUARIO` (do token JWT)

### Tabela Alvo
- `USU_EST_POLITICA` - Política de estoque

---

## Tabela: USU_EST_POLITICA

```sql
CREATE TABLE dbo.USU_EST_POLITICA (
    ID INT IDENTITY(1,1) PRIMARY KEY,
    CODEMP INT NOT NULL,
    CODPRO VARCHAR(30) NOT NULL,
    CODDER VARCHAR(30) NOT NULL,
    CODDEP VARCHAR(30),
    ESTOQUE_MINIMO DECIMAL(18,6),
    ESTOQUE_MAXIMO DECIMAL(18,6),
    PONTO_PEDIDO DECIMAL(18,6),
    LOTE_COMPRA DECIMAL(18,6),
    CONSUMO_MEDIO_MENSAL DECIMAL(18,6),
    LEAD_TIME_DIAS INT,
    OBS VARCHAR(1000),
    USUARIO VARCHAR(50),
    DATA_ALT DATETIME
);
```

---

## Fluxo de Uso (Frontend)

1. **Consultar Movimentação**
   - Usuário define filtros (código, período, etc.)
   - Frontend chama `GET /api/estoque/movimentacao`
   - Exibe tabela com histórico de movimentações

2. **Gerar Sugestões**
   - Usuário clica "Gerar Sugestões"
   - Frontend chama `GET /api/estoque/sugestao-politica` com filtros
   - Exibe sugestões em cards KPI e tabela comparativa

3. **Salvar Política**
   - Usuário clica "Salvar" em uma linha de sugestão
   - Frontend chama `POST /api/estoque/politica/salvar` com dados
   - Sistema atualiza banco de dados

---

## Autenticação

Todos os endpoints requerem:
- Header: `Authorization: Bearer {token_jwt}`
- Validação via middleware `Depends(validar_token)`

---

## Tratamento de Erros

| Código | Cenário |
|--------|---------|
| 400 | Formato de data inválido |
| 401 | Token inválido ou expirado |
| 500 | Erro ao salvar política |

---

## Performance

- **Paginação**: Até 500 itens por página
- **Índices Recomendados**: `E210MVP(DATMOV, CODPRO, CODEMP)`, `E210EST(CODPRO, CODDEP, CODEMP)`
- **Cache**: Sugestões calculadas em tempo real (sem cache)

---

## Data de Implementação

- **Versão**: 1.0
- **Data**: 2026-04-21
- **Status**: Produção
