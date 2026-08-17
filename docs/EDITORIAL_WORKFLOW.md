# Catálogo inicial do fluxo editorial

## Papéis

| Papel | Operações da Fase 1 |
|---|---|
| Editor | Pesquisar, editar, comentar e submeter para revisão. |
| Revisor | Devolver para edição, validar entradas e governar listas. |
| Aprovador | Aprovar candidatas, publicar e reverter releases. |
| Administrador | Todas as operações anteriores. |

Os utilizadores incluídos são perfis de demonstração. A identidade é
registada em revisões, transições, listas e releases.

Na interface, “Responsável pela operação” seleciona qual destes perfis fica
associado à próxima alteração. Esta opção materializa o requisito de histórico,
responsável e separação de papéis da Fase 1; não foi pedida com esse texto
exato. Enquanto existir uma palavra-passe partilhada, a seleção continua a ser
necessária. Com autenticação individual futura, o campo poderá ser preenchido
automaticamente e deixar de ser selecionável.

## Estados e transições

```text
IMPORTED → EDITING → REVIEW → VALIDATED → PUBLISHED
                ↑         │          │
                └─────────┘          └──→ EDITING
```

- `IMPORTED`: conteúdo preservado da fonte oficial, ainda não revisto no novo
  sistema.
- `EDITING`: entrada alterada ou devolvida para correção.
- `REVIEW`: edição terminada e submetida a revisor.
- `VALIDATED`: regras satisfeitas e decisão editorial registada.
- `PUBLISHED`: entrada validada incluída numa release ativa.

O estado existente no XML continua separado em `editorial_status`; não é
destruído nem reinterpretado sem decisão editorial.

## Release

```text
candidate → approved → indexed → tested → active → archived
                                                  └── rollback ──┘
```

O pacote de publicação não é alterado depois de criado. O estado operacional
fica na base editorial e no apontador atómico da release ativa.
