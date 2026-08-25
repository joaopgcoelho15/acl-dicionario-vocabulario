# Repositórios, backup e restauro

## Separação

O projeto usa dois repositórios privados com responsabilidades diferentes:

- `acl-dicionario-vocabulario`: software, testes, schemas, migrações,
  configuração sem segredos e documentação;
- `acl-dicionario-vocabulario-dados`: XML canónico e representações derivadas,
  manifestos, relatórios de validação e histórico de versões dos dados.

O repositório de dados tem acesso restrito. Ficheiros SQLite em utilização,
logs, endereços IP, `.env`, tokens, chaves, passwords e cópias da base de
autenticação não podem ser adicionados a nenhum dos repositórios.

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

## Sincronização editorial pretendida

A sincronização não deve ser silenciosa nem bidirecional. A aplicação deverá
oferecer operações explícitas e auditadas:

1. **Guardar no repositório de dados**: validar, produzir snapshot, mostrar o
   resumo das alterações e criar commit/push com a identidade do responsável;
2. **Consultar versões**: listar commits/snapshots e respetivos relatórios;
3. **Restaurar uma versão**: verificar checksums, criar primeiro um backup do
   estado corrente, importar para uma base temporária, validar e só depois
   trocar o conjunto editorial ativo;
4. manter download e upload manual como alternativa.

O acesso do servidor ao repositório de dados deve usar uma deploy key dedicada
com o menor privilégio possível. Uma chave de leitura pode restaurar; uma chave
de escrita só é necessária para a operação explícita de guardar.

## Segredos e contas

Passwords nunca são recuperadas em texto original. Num sistema com contas
individuais, a base guarda apenas hashes lentos e com salt, preferencialmente
Argon2id. O login compara a password introduzida com o hash; não existe uma
operação para decifrar o hash.

Para restauro existem duas opções seguras:

- guardar uma cópia cifrada da base de autenticação num sistema de backups
  separado, com a chave de cifragem num gestor de segredos;
- restaurar apenas utilizadores, papéis e estados das contas, obrigando cada
  utilizador a definir uma nova password.

Os secrets operacionais (`EDITORIAL_PASSWORD`, `MEILI_MASTER_KEY`, deploy keys
e futuras chaves de sessão) devem existir no `.env` protegido do servidor ou
num gestor de segredos. O restauro completo combina o GitHub com esse backup
cifrado/gestor de segredos; o GitHub, isoladamente, não deve conter credenciais.

## Restauro completo

1. clonar o repositório de software e escolher uma versão/tag conhecida;
2. recuperar os secrets através do gestor de segredos;
3. clonar o repositório privado de dados e verificar checksums;
4. descomprimir e importar o XML canónico para uma nova base editorial;
5. reconstruir e ativar os índices Meilisearch;
6. restaurar contas a partir do backup cifrado ou iniciar o fluxo de reposição
   de passwords;
7. executar testes de integridade antes de reabrir o serviço.
