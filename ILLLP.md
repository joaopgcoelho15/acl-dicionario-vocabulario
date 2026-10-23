# ILLLP — contexto duradouro do projeto

Documento de continuidade preparado em 23 de outubro de 2026. Destina-se a uma
pessoa ou IA sem acesso às conversas que originaram a aplicação. Confirmar sempre
o estado atual do código e do manifesto de dados antes de intervir.

## Objetivo e âmbito

Manter uma plataforma da Academia das Ciências de Lisboa para trabalhar sobre
o Dicionário da Língua Portuguesa (DLP) e o Vocabulário Ortográfico (VOLP):
importar XML/TEI, editar entradas, validar, rever, publicar versões verificáveis
e permitir pesquisa pública. A preservação dos dados lexicais e do histórico
editorial é tão importante como a interface de consulta.

O resultado pretendido é uma aplicação sustentável e transferível: outro
programador ou sistema de IA deve conseguir compreender, testar, alterar e
restaurar o sistema apenas com os repositórios e os acessos autorizados.
Esta plataforma não é a Biblioteca Correia da Serra; essa aplicação tem código
e dados próprios em
[acl-biblioteca-correia-da-serra](https://github.com/joaopgcoelho15/acl-biblioteca-correia-da-serra).

## Onde está cada parte

| Repositório ou caminho | Responsabilidade |
| --- | --- |
| `joaopgcoelho15/acl-dicionario-vocabulario` | Código, interfaces, testes, contratos, schemas e documentação |
| `joaopgcoelho15/acl-dicionario-vocabulario-dados` (privado) | Snapshot editorial, release pública, imagens, logs e configuração de restauro |
| `src/acl_reference/` | Backend Python e CLI `acl-reference` |
| `editorial_app/web/` | Interface editorial |
| `public_app/web/` | Interface pública; verificar no backend que recursos são efetivamente servidos |
| `contracts/` | Contratos públicos, definições dos índices e schemas Relax NG |
| `tests/` e `acceptance/` | Testes e casos de aceitação |
| `deploy/` e `docker-compose.yml` | Restauro, serviços e exemplo de proxy |
| `var/` e `releases/` | Estado local gerado ou restaurado, fora do Git do software |

## Fluxo e decisões a preservar

```text
XML/TEI → SQLite editorial → pacote de release → Meilisearch → consulta pública
                          └→ XML canónico exportável
```

- O SQLite conserva o trabalho editorial, revisões, estados, listas e auditoria.
  Não substituir uma base existente por uma importação de teste.
- Preservar XML, identificadores, relações e imagens. O contrato público JSON
  é separado da representação editorial e dos detalhes do motor de pesquisa.
- A aplicação pública consulta a versão publicada, sem acesso direto à base
  editorial. Uma edição não altera imediatamente o conteúdo público.
- Validar e verificar a release antes de ativar; os índices DLP/VOLP mudam
  atomicamente. Preservar a possibilidade de reversão.
- `Publicada` resulta de publicação efetiva, não de uma mudança manual de rótulo.
  A publicação em lote atualiza as entradas selecionadas e gera o XML canónico.
- A importação de um XML novo compara primeiro com a base e exige o fluxo de
  confirmação previsto; pode substituir integralmente o corpus ativo.
- Acesso de avaliação é só de leitura e deve continuar a recusar escritas no
  servidor. As sessões e chaves não devem aparecer em documentação ou logs de teste.
- Enriquecimentos SPE preservados não implicam publicação. Não os ativar como
  efeito secundário de uma alteração de interface.

## Dados e portabilidade

O snapshot `current/` contém `editorial.sqlite.xz`, `active-release.tar.xz`,
`runtime.env`, `manifest.json` e arquivos semanais em `usage-logs/`.
O pacote da release inclui as imagens; o manifesto identifica tamanhos e SHA-256.
Os arquivos grandes são objetos Git LFS: um clone que só contém ponteiros LFS
não é um backup completo. Executar `git lfs pull` e verificar os checksums.

Em 23 de outubro de 2026, o manifesto remoto consultado indicava criação em
`2026-10-23T00:19:00+00:00` e release `prod-2026-001`. Isto é uma observação
datada, não uma versão que futuros restauros devam impor. Consultar o manifesto
atual e o histórico para escolher o snapshot pretendido.

A configuração `runtime.env` é sensível e existe apenas no repositório privado.
Não a copiar para este repositório, para prompts ou para relatórios. GitHub tokens,
chaves SSH e deploy keys não são recuperáveis desse snapshot: obter acesso com a
conta autorizada ou criar novas chaves. Para desenvolvimento com dados reais,
desativar a sincronização automática no ambiente isolado
(`GITHUB_BACKUP_INTERVAL_SECONDS=0`) e não usar um remoto de escrita de produção.

O GitHub conserva os snapshots enviados com sucesso, não alterações posteriores
a esse envio. Verificar data, integridade e estado da sincronização antes de
afirmar que uma cópia inclui todo o trabalho mais recente.

## Executar e verificar

Requisitos: Python >= 3.11, Git, Git LFS e, para a plataforma completa, Docker
Compose. A instalação Python está definida em `pyproject.toml`.

Para desenvolvimento sem dados institucionais, instalar o pacote como descrito
no README e executar os testes com as fixtures incluídas:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Para uma instância com o corpus real, seguir a secção de restauro do README:
clonar software e dados, obter os objetos LFS, preparar acessos e configuração
e executar `deploy/restore-from-github.sh` no destino adequado. Esse script
repõe estado, substitui `.env` e reinicia serviços; não é um simples teste.
Os seus argumentos permitem escolher os diretórios de software e dados;
ajustar também os caminhos de volumes no ambiente. O Compose usa o volume
nomeado `acl_reference_meili_data`: uma segunda instância na mesma máquina
precisa de volumes e portas distintos.

Verificação operacional: serviços saudáveis, `/health` nas portas 5059 e 5060,
pesquisa pública, consulta de uma entrada, imagens, acesso editorial e release
ativa correta. Executar os casos de aceitação do README após levantar a app.
Usar uma cópia isolada para testar edições, importações, publicação e reversão.

## Estado e limites da documentação

O README descreve o funcionamento atual e liga os detalhes de implantação,
workflow e backup. Alguns documentos de fase e `CURRENT_VERSION_BASELINE.md`
descrevem etapas históricas e caminhos da máquina original. Não precisam de
existir pastas `ACL_Plataforma_Referencia/` ou `ACL_Plataforma_PoC/` para trabalhar
num clone independente; a raiz deste repositório é a raiz do software.

Não existe neste documento uma lista de funcionalidades futuras já autorizadas.
A próxima tarefa deve vir do utilizador. Registar aqui decisões duradouras e
nos testes os comportamentos alterados, evitando que o conhecimento volte a
ficar exclusivamente numa conversa.
