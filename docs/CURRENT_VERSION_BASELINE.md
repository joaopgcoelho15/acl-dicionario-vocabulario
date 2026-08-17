# Proteção da versão atual

A nova arquitetura é desenvolvida exclusivamente em
`ACL_Plataforma_Referencia/`.

A aplicação anterior permanece em `ACL_Plataforma_PoC/` e não é importada,
movida, renomeada nem usada como diretório de execução da nova versão.

Baseline registada em 28 de julho de 2026:

- diretório anterior: `ACL_Plataforma_PoC/`;
- tamanho observado: aproximadamente 539 MB;
- resumo SHA-256 dos módulos Python e recursos web, depois de os ficheiros
  OneDrive estarem materializados localmente:
  `8f492f7d3c910897c66473b52e22e9bcbe7510c9c608edc20ec5010a239eba53`;
- nenhum desses ficheiros apresenta data de modificação posterior ao início
  desta implementação paralela;
- implantação pública: não é alterada por este projeto;
- bases, releases e volumes Docker da nova versão usam nomes próprios.

Antes de qualquer implantação futura será feita uma nova comparação desta
baseline e um backup recuperável do serviço publicado.
