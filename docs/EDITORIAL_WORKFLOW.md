# Catálogo inicial do fluxo editorial

## Papéis

| Papel | Operações da Fase 1 |
|---|---|
| Editor | Pesquisar, editar, comentar e submeter para revisão. |
| Revisor | Devolver para edição, validar entradas e governar listas. |
| Aprovador | Aprovar candidatas, publicar e reverter releases. |
| Administrador | Todas as operações anteriores. |

Na interface, os perfis são apresentados por ordem de responsabilidade:
editor, revisor e aprovador. A cor do indicador junto ao responsável muda com
o papel ativo, para tornar imediatamente visível em que contexto se está a
operar.

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

Ao chegar a `VALIDATED`, a entrada aparece em **Publicação**. Um aprovador
escolhe explicitamente quais das entradas validadas entram na candidata. As
restantes continuam validadas e pendentes; uma candidata continua a conter o
corpus completo, reutilizando a última projeção pública das entradas que não
foram escolhidas. Assim, uma publicação parcial de alterações nunca remove
entradas do corpus público nem deixa passar edições ainda não selecionadas.

O estado existente no XML continua separado em `editorial_status`; não é
destruído nem reinterpretado sem decisão editorial.

## Release

```text
candidate → approved → indexed → tested → active → archived
                                                  └── rollback ──┘
```

O pacote de publicação não é alterado depois de criado. O estado operacional
fica na base editorial e no apontador atómico da release ativa.

## Listas controladas

Cada lista guarda o valor técnico/abreviatura, a descrição completa e a
quantidade de entradas que o usam. **Estado** é a decisão de governação:
`authorized` permite o valor, `unmapped` assinala que ainda precisa de decisão
e `obsolete` indica que não deve continuar a ser usado. **Substituição** aponta
para o valor canónico proposto para um valor obsoleto; por si só não modifica
o corpus.

Quando a alteração do valor o torna igual a outro, a interface propõe um
merge. Se o operador recusar, o formulário é reposto sem gravar. Se confirmar,
os usos no XML e nas tabelas normalizadas são atualizados, as entradas afetadas
passam a `EDITING` e a operação fica na auditoria. Um valor só pode ser apagado
diretamente quando tem zero utilizações; valores em uso têm de ser unidos a um
substituto para evitar perda de dados.

## Contas individuais (evolução prevista)

Os utilizadores de demonstração continuam a representar os três papéis. A
estrutura já prevê utilizadores ativos e papéis, mas a criação de contas, a
gestão de credenciais e uma área de administração ficam para a fase seguinte.
Quando existir autenticação individual, o responsável deixará de ser escolhido
manualmente e passará a ser determinado pela sessão autenticada.
