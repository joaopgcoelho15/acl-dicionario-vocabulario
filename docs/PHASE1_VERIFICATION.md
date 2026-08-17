# Verificação final da Fase 1

Data da verificação técnica: 4 de agosto de 2026.

## Resultado

A implementação técnica dos requisitos da Fase 1 está completa. A versão
anterior permanece separada em `ACL_Plataforma_PoC` e esta arquitetura continua
apenas local.

## Evidência executada

- 9/9 testes automatizados aprovados;
- 8/8 casos de pesquisa e aceitação aprovados no corpus integral;
- 244 124 entradas validadas;
- 0 erros impeditivos e 200 070 avisos de qualidade registados;
- validação contra o `academia.rng` oficial ativada;
- falha de smoke test confirmada como segura: não troca índices públicos nem
  ativa a candidata;
- publicação e rollback confirmados em teste com reconstrução dos dois índices;
- fecho automático das ligações SQLite de curta duração confirmado pela bateria.

## Estado operacional final

- apontador local: `local-2026-008`;
- estado editorial de `local-2026-008`: `active`;
- `local-2026-007` e anteriores: `archived`;
- campo `publication_version` no índice Dicionário: `local-2026-008`;
- campo `publication_version` no índice Vocabulário: `local-2026-008`;
- reposição do ensaio técnico registada em `audit_events`.

## Casos de aceitação

| Caso | Resultado |
|---|---|
| Pesquisa exata por `abacate` | Aprovado |
| Homógrafos de `cavalo` | Aprovado |
| Pesquisa por prefixo | Aprovado |
| Forma flexionada `cavalos` | Aprovado |
| Tolerância a gralha | Aprovado |
| Domínio nativo Zoologia em `cavalo` | Aprovado |
| Consulta apenas no Vocabulário | Aprovado |
| Pesquisa sem resultados | Aprovado |

## Homologação externa

Para declarar a Fase 1 institucionalmente aceite, e não apenas tecnicamente
concluída, falta aos responsáveis editoriais validar os casos propostos, os
papéis reais, as listas controladas e a política relativa ao corpus legado que
não cumpre o Relax NG atual. O sistema já disponibiliza as interfaces e a
auditoria necessárias para registar essas decisões.

## Fonte SPE adiada

Em 5 de agosto de 2026 a publicação do enriquecimento SPE foi adiada. Os 255
emparelhamentos e os 3 485 termos por reconciliar permanecem preservados na
base editorial, mas a fonte tem `publication_enabled=0`. Por isso não é
projetada como aceção, domínio ou proveniência nas releases posteriores a
esta decisão. Os usos de Estatística que já pertencem ao XML original da ACL
não são alterados.

## Desempenho editorial depois da otimização

Medições locais sobre o corpus integral, em 5 de agosto de 2026:

- abrir a entrada `cavalo`: aproximadamente 6 ms;
- pesquisar `abacate`: aproximadamente 8 ms;
- primeiras 100 entradas sem filtro: aproximadamente 134 ms a frio;
- filtro de domínio `Mat.`: aproximadamente 45 ms;
- filtro de avisos: aproximadamente 97 ms.

Antes dos novos índices, o resumo demorava cerca de 5 s, a listagem geral
cerca de 2,7 s e certos filtros ultrapassavam 30 s.
