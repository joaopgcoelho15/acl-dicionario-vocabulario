# Fase 1 — matriz de implementação

Referência: *Sistema de Gestão e Publicação do DLP e do VOLP — requisitos de
alto nível*, 29 de julho de 2026.

| Requisito | Implementação | Evidência |
|---|---|---|
| Importação integral DLP/VOLP | Cumprido | Importação incremental, preservação do fragmento XML, identificador, ordem e SHA-256. Corpus local: 244 124 entradas. |
| Modelo interno e edição principal | Cumprido | Lema, formas, classe, estado, aceções/definições, marcas/domínios e remissões; XML e projeção lado a lado. |
| Pesquisa interna e listas | Cumprido | Prefixo, recurso, workflow, estado de origem, classe, domínio e severidade; listas por estado e problemas. |
| Estados e transições | Cumprido | Estado original preservado; workflow `IMPORTED → EDITING → REVIEW → VALIDATED → PUBLISHED`, com devolução controlada. |
| Listas controladas prioritárias | Cumprido | Catálogo persistente de classes, domínios e estados com autorizado, obsoleto, por mapear, descrição, substituição e contagem de uso. |
| Validações essenciais | Cumprido | Estrutura, lema, identificadores, duplicados por recurso, workflow, relações, aceções vazias, listas controladas e Relax NG oficial. Erros impedem a candidata; avisos ficam no relatório. |
| Histórico e responsável | Cumprido | Utilizador e papel, snapshot anterior, data, comentário, auditoria das transições e reposição de revisão. |
| Exportação TEI/XML e derivados | Cumprido | XML canónico integral, instrução `xml-model`, NDJSON por recurso, manifesto, checksums, contagens e relatório de fidelidade. |
| Candidata, aprovação e publicação | Cumprido | Operações separadas, papéis autorizados, responsáveis, descrição, relatório e auditoria. |
| Consulta pública | Cumprido | DLP/VOLP separados ou conjuntos, filtros, A-Z, entrada estruturada, relações, imagens, XML/JSON, dados e estatísticas. |
| Versão e reversão | Cumprido | Releases preservadas; rollback reconstrói e troca os dois índices, executa smoke tests e só depois altera a release ativa. |
| Casos de teste | Cumprido tecnicamente | Oito casos em `acceptance/phase1-cases.json`, executáveis por CLI e aprovados pela bateria local. A homologação editorial continua a pertencer aos representantes da ACL. |

## Regras de bloqueio da publicação

- Qualquer erro impede a preparação de uma candidata.
- Entradas em edição ou revisão impedem a preparação.
- Avisos relativos ao legado importado não impedem uma baseline, mas ficam
  identificados no relatório e nas listas de trabalho.
- Uma candidata exige aprovação por `approver` ou `administrator`.
- A ativação exige checksums, indexação e smoke tests de contagem e pesquisa.
- Os smoke tests correm sobre os índices versionados; uma falha não troca os
  índices públicos nem o apontador da release.

## Resultado da validação integral de 4 de agosto de 2026

- 244 124 entradas processadas;
- zero erros impeditivos no estado importado;
- 200 070 avisos;
- 199 970 entradas não conformes com o `academia.rng` atualmente publicado;
- 93 aceções sem conteúdo detetável pelas regras iniciais;
- 5 utilizações de domínios por mapear;
- 2 entradas sem lema.

A elevada divergência Relax NG é uma conclusão editorial e não uma falha do
validador. O esquema atualmente publicado é mais restritivo do que grande parte
do corpus consolidado. No legado os resultados são avisos; depois de uma entrada
ser editada, a mesma violação passa a erro e impede a sua validação.

## Decisões que exigem validação dos donos do negócio

1. Aprovar ou corrigir os casos de aceitação propostos.
2. Confirmar os papéis e os nomes reais dos utilizadores.
3. Decidir se o Relax NG deve ser atualizado, complementado ou usado apenas em
   subconjuntos do corpus.
4. Aprovar as substituições dos valores obsoletos ou por mapear.
5. Confirmar a política de elegibilidade da baseline importada.

Estas decisões não correspondem a funcionalidades em falta: são atos de
homologação e governo dos dados que o sistema já suporta e regista.
