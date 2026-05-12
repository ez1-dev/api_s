# Contrato da API - Auditoria Apontamento Genius

## Endpoint

GET /api/auditoria-apontamento-genius

## Descrição

Retorna auditoria de apontamentos da plataforma Genius com validação de status (aprovado, reprovado, pendente) e detalhes de operações, produtos e eficiência.

## Parâmetros de Query

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| pagina | int | Não | Número da página (padrão: 1) |
| 
umero_projeto | string | Não | Filtro por número do projeto |
| 
umero_op | string | Não | Filtro por número da Operação (OP) |
| codigo_produto | string | Não | Filtro por código do produto |
| descricao_produto | string | Não | Filtro por descrição do produto |
| codigo_operador | string | Não | Filtro por código do operador |
| 
ome_operador | string | Não | Filtro por nome do operador |
| data_apontamento_ini | string (YYYY-MM-DD) | Não | Data inicial de apontamento |
| data_apontamento_fim | string (YYYY-MM-DD) | Não | Data final de apontamento |
| origem | string | Não | Filtro por origem (ex: GENIUS, ERP) |
| amilia | string | Não | Filtro por família de produto |
| status | string | Não | Filtro por status (APROVADO, REPROVADO, PENDENTE) |

## Autenticação

Requerida via JWT token no header Authorization: Bearer {token}

## Respostas

### Sucesso (200 OK)

JSON com dados, resumo e paginação

### Erro (400 Bad Request)

Parâmetro de filtro inválido

### Erro (401 Unauthorized)

Token expirado ou inválido

### Erro (500 Internal Server Error)

Erro ao consultar auditoria apontamento Genius

## Lógica de Status

- **APROVADO**: Apontamento válido com eficiência >= 80% e sem inconsistências
- **REPROVADO**: Apontamento inválido (eficiência < 50%, divergências críticas entre Genius e ERP)
- **PENDENTE**: Apontamento aguardando validação ou com eficiência entre 50% e 80%

## Tabelas Nativas do ERP (SQL Server)

- E900HOO: Apontamentos de produção
- E900COP: Operações de produção
- E075PRO: Cadastro de produtos
- E099USU: Usuários/operadores
- E906OPE: Operadores (detalhes)
- E900PRJ: Projetos

## Campos Esperados no Retorno

- data_apontamento (string YYYY-MM-DD)
- numero_projeto (string)
- numero_op (string)
- codigo_produto (string)
- descricao_produto (string)
- origem (string)
- familia (string)
- codigo_operador (string)
- nome_operador (string)
- horas_apontadas (number)
- quantidade_produzida (number)
- tempo_padrao (number)
- tempo_real (number)
- eficiencia (number)
- status (string: APROVADO, REPROVADO, PENDENTE)

## Resumo Esperado

- total_registros (number)
- total_paginas (number)
- total_apontamentos (number)
- total_horas (number)
- total_operadores (number)
- total_projetos (number)
- total_ops (number)
- total_produtos (number)

