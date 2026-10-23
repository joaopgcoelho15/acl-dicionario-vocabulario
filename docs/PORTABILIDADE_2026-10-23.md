# Verificação de portabilidade — 23 de outubro de 2026

## Versões e método

- Código clonado do GitHub: `408dbd8c1c2ce37f56c36bb87b413114b9080904`.
- Dados privados clonados do GitHub: `6f0f399fa0db7f75cdda972cb91732ad0dcc0dc5`.
- Ambiente novo: Python 3.11.17, instalação do pacote a partir de `pyproject.toml`.
- Não foi utilizado o estado local OneDrive para preencher o clone.

## Resultado

- Suite documentada: **37 testes passaram**, incluindo importação, edição,
  workflow, backup/restauro com dados de teste, integridade de release,
  publicação/reversão e apresentação pública.
- `git lfs pull` descarregou os arquivos reais do snapshot privado.
- `git lfs fsck`: **OK**.
- SHA-256 dos **12 ficheiros** listados no manifesto: **todos conformes**,
  incluindo release, base editorial, configuração e nove arquivos de logs.
- Manifesto: `2026-10-23T00:19:00+00:00`, release `prod-2026-001`.
- A configuração privada não foi publicada nem incluída neste relatório.

## Limites

Esta verificação confirma que o snapshot identificado pode ser descarregado
integralmente e corresponde ao respetivo manifesto. Os testes de restauro usam
dados de teste; não foi executado um restauro integral do corpus real com Docker,
Meilisearch e proxy, nem foi alterado o servidor de produção.

Também não foi comparado o estado vivo do servidor com o snapshot. Alterações
posteriores ao momento do backup não são abrangidas. Para uma migração efetiva,
seguir o README, testar no destino e confirmar o snapshot mais recente.
