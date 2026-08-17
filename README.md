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
  --images-root releases/local-2026-007/images
```

Fica disponível apenas em `http://127.0.0.1:8089/`.

Com a configuração de produção, os endereços são:

- `https://iris.sysresearch.org/dicionario-vocabulario/`;
- `https://iris.sysresearch.org/dicionario-vocabulario/editor/`.

O editor usa autenticação HTTP Basic. A configuração provisória pedida é
utilizador `acl` e palavra-passe `ACL`; deve ser substituída antes de uma
utilização institucional prolongada.

O editor permite alterar lema, formas, classe, estado, definições, marcas e
remissões. Preserva revisões, identifica o responsável e implementa o workflow
`IMPORTED → EDITING → REVIEW → VALIDATED`. Inclui filtros editoriais,
listas controladas, validação Relax NG e reposição de revisões.

A publicação separa preparação, aprovação e ativação. A publicação e a
reversão constroem índices versionados, verificam contagens e pesquisa antes da
troca atómica e só depois mudam o apontador ativo. A CLI e a interface usam o
mesmo workflow e as mesmas regras de papéis.

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

## Segurança da versão anterior

- Não executar comandos desta nova versão dentro de `ACL_Plataforma_PoC`.
- Não reutilizar o volume, a base, a porta ou o contentor da PoC.
- Não existe qualquer passo de implantação no servidor nesta fase.
- Consultar `docs/CURRENT_VERSION_BASELINE.md`.
