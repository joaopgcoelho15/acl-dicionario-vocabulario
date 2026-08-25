# Gestão, persistência e estados editoriais

Esta implementação segue a simplificação definida no anexo de gestão de
persistência e estados editoriais. A base SQLite é a versão de trabalho; o XML
TEI consolidado não é reescrito a cada edição.

## Persistência do conjunto

1. A importação inicial TEI/XML inicializa a base de dados apenas se puder ser
   processada integralmente. Uma falha preserva o conjunto ativo anterior.
2. Todas as edições seguintes são gravadas imediatamente em SQLite e ativam um
   indicador global de alterações ainda não guardadas em XML.
3. **Guardar TEI/XML** cria um novo ficheiro canónico completo, sem entradas
   apagadas, e um log JSON associado. O indicador só é limpo depois de ambos os
   ficheiros serem escritos e verificados.
4. A importação adicional é atómica, aceita apenas identificadores novos e cria
   entradas em preparação. A substituição integral exige um aprovador e dupla
   confirmação na interface.

Este estado global de salvaguarda é independente do estado editorial de cada
entrada.

## Estados das entradas

```text
EM PREPARAÇÃO → EDITADA → REVISTA → VALIDADA → PUBLICADA
       ↑             ↓          ↓             ↓
       └─ RECUPERADA   PRECISA DE REVISÃO       APAGADA
```

| Estado técnico | Significado |
|---|---|
| `DRAFT` | Entrada nova, importada ou recuperada, ainda em preparação. |
| `EDITED` | Conteúdo alterado e guardado na base de trabalho. |
| `REVIEWED` | Conteúdo revisto editorialmente. |
| `NEEDS_REVISION` | Revisor ou aprovador pediu uma correção. |
| `VALIDATED` | Entrada sem erros impeditivos e pronta para publicação. |
| `PUBLISHED` | A versão desta entrada integra a interface pública ativa. |
| `REMOVED` | Entrada apagada logicamente e excluída do próximo XML/publicação. |

A origem de uma entrada em preparação é registada separadamente como
`new`, `imported` ou `recovered`. Apagar exige dupla confirmação. Uma entrada
publicada que seja apagada surge automaticamente em Publicação como remoção
pendente; permanece no site público até essa operação ser publicada.

## Papéis

| Papel | Operações principais |
|---|---|
| Editor | Editar, recuperar e enviar o trabalho para a etapa seguinte. |
| Revisor | Rever, pedir correções e gerir listas controladas. |
| Aprovador | Validar, apagar, escolher alterações e publicar. |

Os utilizadores atuais são perfis de demonstração. **Responsável pela
operação** determina quem fica registado no histórico enquanto existir uma
palavra-passe partilhada. Com contas individuais, passará a ser determinado
automaticamente pela sessão.

## Publicação

Para o utilizador existe uma única ação: o aprovador seleciona entradas
validadas e remoções pendentes e escolhe **Publicar entradas selecionadas**.
A aplicação constrói e verifica internamente uma versão completa, cria índices
versionados, executa testes de integridade e pesquisa e troca atomicamente a
versão pública. As etapas técnicas permanecem no histórico para auditoria e
reversão, mas não fazem parte do workflow editorial visível.

Entradas não selecionadas continuam a usar a respetiva projeção pública
anterior. Assim, publicar parcialmente não expõe edições em curso nem remove
conteúdo por acidente.

## Listas controladas e auditoria

Na interface, cada lista mostra apenas o valor/abreviatura, a descrição completa
e a contagem de utilizações. Alterar um valor para outro já existente propõe a
união; recusá-la repõe o formulário. Um valor em uso não pode ser apagado sem
ser unido a outro, evitando perda de dados.

A página **Auditoria** resume estados, validações, persistência, publicações e
as 200 operações mais recentes, com descarga do relatório em JSON.
