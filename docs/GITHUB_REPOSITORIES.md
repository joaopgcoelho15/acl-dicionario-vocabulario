# Repositórios, backup e restauro

## Separação

O projeto usa dois repositórios privados com responsabilidades diferentes:

- `acl-dicionario-vocabulario`: software, testes, schemas, migrações,
  configuração sem segredos e documentação;
- `acl-dicionario-vocabulario-dados`: XML canónico e representações derivadas,
  manifestos, relatórios de validação e histórico de versões dos dados.

O repositório de dados tem acesso restrito. Ficheiros SQLite em utilização,
logs, endereços IP, chaves SSH e cópias da base de autenticação não devem ser
adicionados aos repositórios. Como simplificação provisória, o repositório
privado do software inclui o `.env` com a password editorial partilhada e a
chave do Meilisearch.

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

### Modo provisório atual

O `.env` versionado permite que o Docker Compose recupere diretamente do GitHub
a password editorial partilhada e a chave interna do Meilisearch. O utilizador
HTTP Basic continua a ser `acl`. Não são guardadas no Git chaves SSH pessoais,
deploy keys ou tokens de acesso ao próprio GitHub.

Este modo pressupõe que o repositório permanece privado. Antes de o tornar
público, é obrigatório remover o `.env` do histórico e trocar todas as
credenciais nele contidas.

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
repositório que ainda não foi clonado.
