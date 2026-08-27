# ACL — plataforma de arquitetura de referência


## Cadeia principal

```text
XML → SQLite editorial → pacote versionado → Meilisearch → API/aplicação pública
              └──────────────→ XML canónico
```

## Preparação

Na raiz desta pasta:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
export PYTHONPATH="$PWD/src"
python3 -m acl_reference init-db
python3 -m acl_reference import-xml "../ACL documentos/dic.xml.xz"
python3 -m acl_reference build-release --release-id 2026-001 \
  --images-root "../ACL_Plataforma_PoC/var/entry-images"
python3 -m acl_reference verify-release releases/2026-001
```

## Meilisearch e aplicação pública local

```bash
docker compose up -d meilisearch
export MEILI_MASTER_KEY=acl-local-development-key
python3 -m acl_reference approve-release 2026-001 --actor aprovador.demo
python3 -m acl_reference publish-release 2026-001 --actor aprovador.demo
python3 -m acl_reference serve
```

A interface local fica em `http://127.0.0.1:8090/`. O Meilisearch só é exposto
em `127.0.0.1:7700`.

O editor local pode ser iniciado separadamente:

```bash
python3 -m acl_reference serve-editorial \
  --releases-root releases \
  --images-root releases/local-2026-008/images
```

Fica disponível apenas em `http://127.0.0.1:8089/`.

Com a configuração de produção, os endereços são:

- `https://iris.sysresearch.org/dicionario-vocabulario/`;
- `https://iris.sysresearch.org/dicionario-vocabulario/editor/`.

O editor usa temporariamente autenticação HTTP Basic. Para simplificar o
restauro nesta fase, o repositório privado inclui o ficheiro `.env` usado pelo
Docker Compose. O `.env.example` continua a documentar a configuração sem
credenciais. Esta decisão tem de ser revista antes de o repositório poder ser
tornado público.

O editor permite alterar lema, formas, classe, estado, definições, marcas e
remissões. Preserva revisões, identifica o responsável e implementa o workflow
`DRAFT → EDITED → REVIEWED → VALIDATED → PUBLISHED`, incluindo pedido de
revisão, remoção e recuperação. Inclui filtros editoriais, listas
controladas, validação Relax NG, auditoria e reposição de revisões.

Na interface, o aprovador publica a seleção numa única ação. Internamente, a
publicação e a reversão constroem índices versionados, verificam integridade,
contagens e pesquisa antes da troca atómica e só depois mudam o apontador
ativo. A base SQLite é a versão de trabalho; **Guardar TEI/XML** cria uma
salvaguarda canónica completa e o respetivo log apenas quando solicitado.
Depois de cada publicação, a aplicação cria ainda um snapshot restaurável do
estado editorial e sincroniza-o com o repositório privado dos dados. A mesma
operação pode ser iniciada manualmente em **Dados TEI/XML**.

## Interfaces locais

- Pesquisa pública: `http://127.0.0.1:8090/`
- Dados e diagnóstico: `http://127.0.0.1:8090/dados`
- Estatísticas de utilização: `http://127.0.0.1:8090/estatisticas`
- Aplicação editorial: `http://127.0.0.1:8089/`

A aplicação pública usa apenas o pacote ativo e o Meilisearch. O ficheiro
`var/editorial.sqlite` não é consultado por pedidos públicos. O log técnico
é gravado de forma assíncrona em `var/usage.sqlite`.

## Testes

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Preparar os esquemas e executar validação integral:

```bash
python3 -m acl_reference fetch-schema
python3 -m acl_reference validate
```

Executar os casos de aceitação com a aplicação pública iniciada:

```bash
python3 -m acl_reference acceptance-test --base-url http://127.0.0.1:8090
```

Consultar `docs/PHASE1_REQUIREMENTS.md` e `docs/EDITORIAL_WORKFLOW.md`.
Para publicação no IRIS, consultar `docs/DEPLOYMENT_IRIS.md`.
Para a separação entre software, dados e segredos, bem como para o processo de
backup e restauro, consultar `docs/GITHUB_REPOSITORIES.md`.

## Iniciar um servidor novo do zero

Um servidor novo precisa de duas **deploy keys novas**. As chaves privadas só
existem no servidor e não são guardadas no GitHub nem nos snapshots; por isso,
se o servidor desaparecer, estas chaves têm obrigatoriamente de ser geradas e
registadas novamente.

1. Instalar Git, Git LFS, Docker e Docker Compose.
2. Gerar duas chaves sem passphrase:

```bash
install -d -m 700 /root/.ssh/acl-github
ssh-keygen -t ed25519 -N "" \
  -C "acl-software-deploy@acl-server" \
  -f /root/.ssh/acl-github/software_ed25519
ssh-keygen -t ed25519 -N "" \
  -C "acl-data-backup@acl-server" \
  -f /root/.ssh/acl-github/data_ed25519
ssh-keyscan -H github.com > /root/.ssh/acl-github/known_hosts
```

3. No GitHub, adicionar `software_ed25519.pub` às **Deploy keys** do
   repositório do software, apenas com leitura. Adicionar `data_ed25519.pub` às
   **Deploy keys** do repositório dos dados, ativando **Allow write access**.
4. Clonar os dois repositórios e fixar a chave de cada clone:

```bash
GIT_SSH_COMMAND="ssh -i /root/.ssh/acl-github/software_ed25519 -o IdentitiesOnly=yes" \
  git clone git@github.com:joaopgcoelho15/acl-dicionario-vocabulario.git \
  /opt/acl-reference
GIT_SSH_COMMAND="ssh -i /root/.ssh/acl-github/data_ed25519 -o IdentitiesOnly=yes" \
  git clone git@github.com:joaopgcoelho15/acl-dicionario-vocabulario-dados.git \
  /opt/ACL_Dados_Editorais_GitHub
git -C /opt/acl-reference config core.sshCommand \
  "ssh -i /root/.ssh/acl-github/software_ed25519 -o IdentitiesOnly=yes"
git -C /opt/ACL_Dados_Editorais_GitHub config core.sshCommand \
  "ssh -i /root/.ssh/acl-github/data_ed25519 -o IdentitiesOnly=yes"
```

5. Disponibilizar a chave dos dados apenas ao contentor editorial e executar o
   restauro integral:

```bash
install -d -m 700 /opt/acl-reference/deploy/github-ssh
install -m 600 /root/.ssh/acl-github/data_ed25519 \
  /opt/acl-reference/deploy/github-ssh/id_ed25519
install -m 600 /root/.ssh/acl-github/known_hosts \
  /opt/acl-reference/deploy/github-ssh/known_hosts
/opt/acl-reference/deploy/restore-from-github.sh
```

O último comando descarrega o snapshot Git LFS, verifica os checksums, restaura
as bases e a release ativa, reconstrói o Meilisearch e inicia as aplicações
pública e editorial. As passwords provisórias são recuperadas do `.env` do
repositório privado; as deploy keys não são recuperáveis e têm sempre de ser
recriadas no servidor novo.

## Segurança da versão anterior

- Não executar comandos desta nova versão dentro de `ACL_Plataforma_PoC`.
- Não reutilizar o volume, a base, a porta ou o contentor da PoC.
- Não existe qualquer passo de implantação no servidor nesta fase.
- Consultar `docs/CURRENT_VERSION_BASELINE.md`.
