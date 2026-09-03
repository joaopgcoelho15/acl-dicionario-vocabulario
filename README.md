# ACL — Dicionário e Vocabulário

Plataforma de gestão, validação, publicação e pesquisa do Dicionário da
Língua Portuguesa e do Vocabulário Ortográfico da Academia das Ciências de
Lisboa.

O sistema tem duas aplicações:

- **aplicação editorial**, onde se editam, validam e publicam entradas;
- **aplicação pública**, que consulta apenas a última versão publicada.

Em produção estão disponíveis em:

- [Interface pública](https://iris.sysresearch.org/dicionario-vocabulario/)
- [Interface editorial](https://iris.sysresearch.org/dicionario-vocabulario/editor/)

## Arquitetura

```text
XML/TEI → SQLite editorial → release versionada → Meilisearch → aplicação pública
                  └─→ XML canónico
```

O SQLite é a base de trabalho da aplicação editorial. Guarda entradas,
revisões, workflow, utilizadores, listas controladas, validações e auditoria.
Ao publicar, a plataforma cria e verifica uma release imutável, constrói os
índices no Meilisearch e troca atomicamente a versão ativa. A aplicação
pública nunca consulta diretamente a base editorial.

O XML/TEI é a fonte canónica dos dados lexicais. Na interface editorial, um
novo XML é primeiro comparado com a base atual, sem a alterar; a comparação
pode ser descarregada em CSV. Depois de confirmada por um aprovador, a
importação substitui integralmente o corpus ativo, preservando contas e
configuração. Se existirem alterações editoriais por guardar, a substituição
é bloqueada. O atributo de estado recebido no XML inicializa diretamente o
workflow da respetiva entrada.

Os estados editoriais principais são:

```text
Em preparação → Editada → Revista editorialmente → Validada → Publicada
```

Existem ainda os estados alternativos **Precisa de revisão** e **Apagada**.
Na interface **Edição**, cada entrada pode mudar diretamente
para qualquer estado permitido pelo perfil ativo; não é imposto um percurso
rígido entre estados. **Publicada** não é apenas uma etiqueta manual: resulta
da publicação efetiva dos dados na aplicação pública.

A área **Publicação** reúne a gestão do TEI/XML e as operações em lote. O
utilizador pode guardar ou comparar o XML de trabalho em qualquer momento. O
aprovador escolhe um subconjunto das entradas validadas e das remoções
pendentes e publica-o numa única operação funcional, que:

1. cria, verifica e ativa a nova versão na aplicação pública;
2. muda as entradas selecionadas de **Validada** para **Publicada**;
3. gera e guarda o TEI/XML canónico completo com os estados atualizados.

O estado **Publicada** nunca pode ser aplicado manualmente em **Edição e
Validação**. A auditoria guarda um só evento de publicação em lote, com a lista
completa das entradas afetadas e a identificação do XML gerado.

Em **Auditoria**, a caixa de validação abre um resumo por regra. O total de
ocorrências pode ser superior ao total de entradas afetadas porque uma mesma
entrada pode violar várias regras. A pesquisa editorial permite filtrar
diretamente por cada tipo de problema, como falta de lema ou Relax NG inválido.

## Acesso de avaliação só de leitura

Um utilizador autenticado pela credencial principal pode abrir **Admin** e
configurar uma chave URL-safe com 32 a 128 caracteres. A aplicação gera um URL
do tipo:

```text
https://iris.sysresearch.org/dicionario-vocabulario/editor/?x=CHAVE
```

O primeiro acesso troca a chave por um cookie de sessão `HttpOnly` e redireciona
para o URL sem a chave. Esta sessão permite consultar a aplicação editorial,
mas o servidor recusa todas as operações `POST`, `PATCH` e `DELETE`. O painel
Admin também não fica acessível. A base guarda apenas hashes da chave e da
sessão; substituir ou limpar a chave invalida imediatamente os acessos antigos.

Na aplicação pública, o antigo endpoint `/api/debug/entries/{id}` foi removido.
A comparação XML/JSON usa `/api/entries/{id}/source`, que devolve apenas o
identificador público e o XML bruto necessário para essa funcionalidade.

## Repositórios

O projeto está separado em dois repositórios com responsabilidades e
níveis de acesso diferentes:

- `acl-dicionario-vocabulario`: software, configuração sem segredos e
  documentação; pode ser público;
- `acl-dicionario-vocabulario-dados`: snapshot restaurável dos dados e da
  configuração operacional; tem de permanecer privado e com acesso
  controlado.

O repositório de dados contém a base editorial comprimida, o log de utilização,
a release pública ativa, as imagens, a configuração de runtime e um manifesto
com checksums. Os ficheiros grandes são geridos por Git LFS.

As credenciais de runtime nunca são guardadas no repositório do software. O
ficheiro `.env` real existe no servidor e a sua cópia restaurável fica apenas em
`current/runtime.env` no repositório privado dos dados. O software contém
somente `.env.example`, com nomes de variáveis e valores fictícios.

## Requisitos

- Python 3.11 ou superior
- Git e Git LFS
- Docker com Docker Compose

## Desenvolvimento local

Criar o ambiente Python e instalar a aplicação:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
```

Com uma base e uma release já existentes nas pastas `var/` e `releases/`, iniciar
todos os serviços:

```bash
docker compose up -d --build
docker compose ps
```

Por omissão, as interfaces locais ficam em:

- pública: `http://127.0.0.1:5059/dicionario-vocabulario/`;
- editorial: `http://127.0.0.1:5060/dicionario-vocabulario/editor/`;
- Meilisearch: `http://127.0.0.1:7700/`.

Os caminhos, portas e credenciais podem ser alterados no `.env`.

### Criar os dados a partir do XML

Este passo só é necessário para iniciar um conjunto de dados novo. Não deve ser
usado para restaurar a produção; nesse caso, seguir a secção seguinte.

```bash
export PYTHONPATH="$PWD/src"
python3 -m acl_reference init-db
python3 -m acl_reference import-xml "/caminho/para/dic.xml.xz"
python3 -m acl_reference validate --rng-path contracts/schemas/academia.rng
python3 -m acl_reference build-release \
  --release-id local-001 \
  --images-root "/caminho/para/as/imagens"
python3 -m acl_reference verify-release releases/local-001
```

A aprovação e publicação podem depois ser feitas na interface editorial.

## Instalar ou restaurar um servidor

Um servidor novo precisa de duas deploy keys porque as respetivas chaves
privadas existem apenas no servidor e não podem ser recuperadas a partir do
GitHub:

- uma chave de **leitura** para instalar e atualizar o software;
- uma chave de **leitura e escrita** para restaurar e sincronizar os dados.

### 1. Instalar dependências

Instalar Git, Git LFS, Docker e Docker Compose no servidor.

### 2. Criar as deploy keys

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

No GitHub, adicionar:

1. `software_ed25519.pub` às **Deploy keys** do repositório do software, sem
   permissão de escrita;
2. `data_ed25519.pub` às **Deploy keys** do repositório dos dados, ativando
   **Allow write access**.

### 3. Clonar os repositórios

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

### 4. Restaurar e iniciar a plataforma

Disponibilizar ao contentor editorial apenas a chave do repositório de dados:

```bash
install -d -m 700 /opt/acl-reference/deploy/github-ssh
install -m 600 /root/.ssh/acl-github/data_ed25519 \
  /opt/acl-reference/deploy/github-ssh/id_ed25519
install -m 600 /root/.ssh/acl-github/known_hosts \
  /opt/acl-reference/deploy/github-ssh/known_hosts

/opt/acl-reference/deploy/restore-from-github.sh
```

O script descarrega os objetos Git LFS, verifica os checksums, restaura as bases
e a release ativa, repõe o `.env` a partir do `runtime.env` do repositório
privado, reconstrói o índice de pesquisa e inicia as duas aplicações.

Verificar o resultado:

```bash
cd /opt/acl-reference
docker compose ps
curl -fsS http://127.0.0.1:5059/health
curl -fsS http://127.0.0.1:5060/health
```

A configuração do proxy e dos endereços públicos está descrita em
[`docs/DEPLOYMENT_IRIS.md`](docs/DEPLOYMENT_IRIS.md).

## Backup automático

A aplicação editorial sincroniza um snapshot com o repositório privado de
dados:

- imediatamente depois de uma publicação ou reversão bem-sucedida;
- manualmente através de **Sincronizar agora**;
- a cada 30 minutos, apenas quando deteta alterações editoriais desde o último
  snapshot;
- uma vez por dia quando existirem apenas novos logs de utilização.

Uma verificação sem alterações não cria ficheiros, commits ou uploads. O
intervalo é configurado por `GITHUB_BACKUP_INTERVAL_SECONDS`; o valor `0`
desativa a verificação periódica.

Para detalhes sobre o conteúdo do snapshot e operações de restauro, consultar
[`docs/GITHUB_REPOSITORIES.md`](docs/GITHUB_REPOSITORIES.md).

## Testes

Executar os testes automatizados:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Com a aplicação pública iniciada, executar os testes de aceitação:

```bash
PYTHONPATH=src python3 -m acl_reference acceptance-test \
  --base-url http://127.0.0.1:5059/dicionario-vocabulario
```

## Documentação

- [Arquitetura](docs/ARCHITECTURE.md)
- [Workflow editorial](docs/EDITORIAL_WORKFLOW.md)
- [Requisitos da fase 1](docs/PHASE1_REQUIREMENTS.md)
- [Verificação da fase 1](docs/PHASE1_VERIFICATION.md)
- [Publicação no servidor IRIS](docs/DEPLOYMENT_IRIS.md)
- [Repositórios, backup e restauro](docs/GITHUB_REPOSITORIES.md)
