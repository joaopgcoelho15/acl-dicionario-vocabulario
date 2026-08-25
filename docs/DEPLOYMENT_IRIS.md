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

1. Copiar `.env.example` para `.env`.
2. Substituir `MEILI_MASTER_KEY` por uma chave aleatória longa.
3. Definir `EDITORIAL_PASSWORD` com uma palavra-passe longa e exclusiva.
4. Confirmar `APP_BIND_ADDRESS=0.0.0.0` se o Nginx estiver noutra máquina.
5. Restringir as portas 5059 e 5060, na firewall, ao endereço do proxy.
6. Confirmar que `var/editorial.sqlite`, `releases/` e as imagens estão
   materializados localmente e não são placeholders OneDrive.

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

- fazer backup SQLite com `.backup`, nunca copiando uma base aberta;
- preservar todas as releases e o ficheiro `ACTIVE_RELEASE`;
- preservar `var/usage.sqlite` segundo a política de retenção de IPs;
- manter a PoC na porta 5058 durante a validação inicial;
- para rollback imediato do serviço, repor no Nginx o upstream da porta 5058;
- para rollback de dados na nova arquitetura, usar a operação editorial de
  reversão de release.
