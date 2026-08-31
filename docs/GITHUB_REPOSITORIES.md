# Repositórios, backup e restauro

## Separação

O projeto usa dois repositórios com responsabilidades e níveis de acesso
diferentes:

- `acl-dicionario-vocabulario`: software, testes, schemas, migrações,
  configuração sem segredos e documentação; pode ser público;
- `acl-dicionario-vocabulario-dados`: XML canónico e representações derivadas,
  manifestos, relatórios de validação, histórico de versões e configuração
  operacional necessária ao restauro.

O repositório de dados tem acesso restrito e deve permanecer privado. O
repositório do software nunca deve conter `.env`, passwords, chaves do
Meilisearch, chaves SSH, deploy keys ou tokens. O único ficheiro de ambiente
versionado no software é `.env.example`, com valores fictícios.

## Snapshot de dados

Cada versão guardada no repositório de dados deve ser imutável e conter:

```text
versions/<identificador>/
├── canonical.xml.xz
├── dictionary.ndjson.xz
├── vocabulary.ndjson.xz
├── manifest.json
└── validation-report.json
```

O manifesto deve identificar a versão, data, contagens, checksums e responsável.
O XML canónico é a fonte de verdade; JSON/NDJSON e CSV são projeções que podem
ser reconstruídas. Ficheiros que excedam os limites normais do GitHub devem ser
comprimidos ou geridos com Git LFS.

## Sincronização editorial

A aplicação cria automaticamente um snapshot depois de cada publicação ou
reversão bem-sucedida e, por omissão, a cada 30 minutos para proteger trabalho
editorial ainda não publicado. O intervalo é configurado por
`GITHUB_BACKUP_INTERVAL_SECONDS`; o valor `0` desativa o ciclo periódico.
Também disponibiliza a operação manual
**Sincronizar agora** em **Dados TEI/XML**.

O ciclo periódico compara primeiro o estado da base editorial e só cria um
commit quando existem alterações desde o último snapshot. Alterações apenas no
log de acessos não criam versões Git de 30 em 30 minutos; o log é incluído na
salvaguarda seguinte provocada por trabalho editorial, publicação ou operação
manual.

O snapshot `current/` contém:

- `editorial.sqlite.xz`: entradas, contas, papéis, workflow, listas,
  revisões e auditoria;
- `usage.sqlite.xz`: log de utilização, quando existe;
- `active-release.tar.xz`: release pública ativa completa, incluindo imagens;
- `runtime.env`: configuração necessária para arrancar os serviços;
- `manifest.json`: checksums, tamanhos, data, responsável e release ativa.

Os `.xz` são guardados com Git LFS. Se o push falhar, a publicação continua
ativa e a interface mostra **Sincronização GitHub pendente**. O aprovador pode
repetir a operação sem voltar a publicar.

O acesso do servidor ao repositório de dados deve usar uma deploy key dedicada
com o menor privilégio possível. Uma chave de leitura pode restaurar; uma chave
de escrita só é necessária para a operação explícita de guardar.

## Credenciais e contas

### Modelo atual

O `.env` real existe em `/opt/acl-reference/.env` no servidor e deve ter
permissões `0600`. Durante uma sincronização, a aplicação guarda-o como
`current/runtime.env` apenas no repositório privado dos dados. O script de
restauro repõe essa cópia antes de iniciar os contentores. Isto preserva o
restauro simples pelos dois repositórios sem publicar as credenciais juntamente
com o código.

O utilizador HTTP Basic continua a ser `acl`. Não são guardadas no Git chaves
SSH pessoais, deploy keys ou tokens de acesso ao próprio GitHub. Quem tiver
acesso ao repositório privado dos dados consegue recuperar as credenciais de
runtime, pelo que esse acesso deve ser concedido apenas a administradores.

Se o repositório de software expuser acidentalmente uma credencial, deve-se
primeiro rodá-la no servidor e depois removê-la de todo o histórico Git. Tornar
o repositório privado não invalida cópias que já tenham sido clonadas.

### Evolução para contas individuais

Passwords nunca são recuperadas em texto original. Num sistema com contas
individuais, a base guarda apenas hashes lentos e com salt, preferencialmente
Argon2id. O login compara a password introduzida com o hash; não existe uma
operação para decifrar o hash.

Para restauro existem duas opções seguras:

- guardar uma cópia cifrada da base de autenticação num sistema de backups
  separado, com a chave de cifragem num gestor de segredos;
- restaurar apenas utilizadores, papéis e estados das contas, obrigando cada
  utilizador a definir uma nova password.

Quando forem introduzidas contas individuais, os hashes das passwords e as
futuras chaves de sessão devem sair deste modelo simplificado e passar para a
base de autenticação e para uma salvaguarda própria.

## Restauro completo

1. clonar o repositório do software em `/opt/acl-reference`;
2. clonar o repositório privado dos dados em
   `/opt/ACL_Dados_Editorais_GitHub`;
3. executar:

```bash
chmod +x /opt/acl-reference/deploy/restore-from-github.sh
/opt/acl-reference/deploy/restore-from-github.sh
```

O comando verifica os checksums, preserva a base anterior em
`var/restore-backups/`, restaura SQLite, release e configuração, reconstrói os
índices Meilisearch e inicia as duas aplicações. Também inicializa o Git LFS no
clone dos dados e descarrega os objetos do snapshot. O servidor precisa de ter
`git-lfs` instalado antes de executar o comando.

O acesso inicial aos repositórios privados continua a exigir autenticação na
conta GitHub ou uma deploy key. Essa credencial não pode ser recuperada de um
repositório que ainda não foi clonado. Num servidor novo devem ser geradas duas
deploy keys novas: leitura para o software e escrita para os dados. As instruções
completas estão na secção **Iniciar um servidor novo do zero** do `README.md`.
