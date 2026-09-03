# Implantação no IRIS

## Endereços

- Público: `https://iris.sysresearch.org/dicionario-vocabulario/`
- Editorial: `https://iris.sysresearch.org/dicionario-vocabulario/editor/`

O Nginx remove estes prefixos ao encaminhar para os contentores. As aplicações
recebem os prefixos por configuração e geram URLs corretos no HTML e JavaScript.

## Portas na VM 10.10.10.99

- aplicação pública nova: `5059`;
- aplicação editorial: `5060`;
- PoC anterior, mantida para rollback: `5058`;
- Meilisearch: `7700`, limitado ao host.

## Preparação

1. Confirmar que `git`, `git-lfs`, Docker e Docker Compose estão instalados.
2. Confirmar que o clone inclui o `.env` versionado no repositório privado.
3. Confirmar `APP_BIND_ADDRESS=0.0.0.0` se o Nginx estiver noutra máquina.
4. Restringir as portas 5059 e 5060, na firewall, ao endereço do proxy.
5. Confirmar que `var/editorial.sqlite`, `releases/` e as imagens estão
   materializados localmente e não são placeholders OneDrive.

Nesta fase provisória, `EDITORIAL_PASSWORD` e `MEILI_MASTER_KEY` estão no
`.env` versionado para permitir um restauro simples. O ficheiro só pode
permanecer no Git enquanto o repositório for privado.

## Arranque sem corte do serviço anterior

```bash
docker compose build
docker compose up -d meilisearch public editorial
docker compose ps
curl http://127.0.0.1:5059/health
curl http://127.0.0.1:5060/health
```

Executar os casos de aceitação diretamente na porta nova antes de alterar o
Nginx:

```bash
docker compose exec editorial acl-reference acceptance-test \
  --base-url http://public:8000
```

## Nginx

Instalar o conteúdo de `deploy/nginx-location.conf` no `server` HTTPS de
`iris.sysresearch.org`, executar `nginx -t` e só depois recarregar o Nginx.
A localização `/editor/` tem de aparecer antes da localização pública.

## Autenticação editorial

O editor responde com HTTP 401 e apresenta o diálogo nativo do browser.

- utilizador provisório: `acl`;
- palavra-passe: valor de `EDITORIAL_PASSWORD` no `.env` do servidor.
- `/health` permanece sem autenticação para o healthcheck.

Esta autenticação partilhada é deliberadamente provisória. A identificação selecionada
em “Responsável pela operação” não é autenticação: serve para atribuir uma
edição, revisão, aprovação ou rollback ao respetivo interveniente.

## Backup e rollback

- o contentor editorial cria uma cópia consistente do SQLite e sincroniza-a
  automaticamente com o repositório privado dos dados depois de publicar;
- o servidor deve ter o repositório dos dados clonado no caminho definido por
  `GITHUB_DATA_REPOSITORY_HOST`, Git LFS e uma deploy key com escrita;
- preservar todas as releases e o ficheiro `ACTIVE_RELEASE`;
- preservar `var/usage-logs/` segundo a política de retenção de IPs;
  o serviço cria um SQLite por semana ISO (`usage-AAAA-Wnn.sqlite`) e, na
  primeira execução, copia automaticamente o antigo `var/usage.sqlite` para
  `var/usage-logs/usage-legacy.sqlite`;
- manter a PoC na porta 5058 durante a validação inicial;
- para rollback imediato do serviço, repor no Nginx o upstream da porta 5058;
- para rollback de dados na nova arquitetura, usar a operação editorial de
  reversão de release.
