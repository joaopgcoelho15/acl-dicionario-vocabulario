(() => {
  "use strict";

  const basePath = (
    document.querySelector('meta[name="acl-base-path"]')?.content || ""
  ).replace(/\/+$/, "");
  const view = document.body.dataset.dashboardView || "usage";
  const period = document.querySelector("#usage-period");
  const refresh = document.querySelector("#refresh-stats");
  const loading = document.querySelector("#stats-loading");
  const error = document.querySelector("#stats-error");

  refresh?.addEventListener("click", load);
  period?.addEventListener("change", load);
  document.querySelectorAll("[data-table-filter]").forEach((input) => {
    input.addEventListener("input", () => filterTable(input));
  });
  load();

  async function load() {
    loading.hidden = false;
    error.hidden = true;
    refresh.disabled = true;
    try {
      const response = await fetch(
        appUrl(`/api/dashboard?days=${encodeURIComponent(period?.value || "30")}`),
        { headers: { Accept: "application/json" } }
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      render(data);
    } catch {
      error.textContent = "Não foi possível calcular as estatísticas.";
      error.hidden = false;
    } finally {
      loading.hidden = true;
      if (refresh) refresh.disabled = false;
    }
  }

  function render(data) {
    document.querySelector("#stats-generated").textContent =
      `Atualizado em ${dateTime(data.generated_at)}`;
    if (view === "data") {
      renderCorpus(data.corpus);
      [
        "#source-section",
        "#corpus-section",
        "#editorial-section",
        "#facets-section",
      ].forEach((selector) => {
        document.querySelector(selector).hidden = false;
      });
    } else {
      renderUsage(data.usage);
      document.querySelector("#usage-section").hidden = false;
    }
  }

  function renderCorpus(corpus) {
    const totals = corpus.totals || {};
    kpis("#corpus-kpis", [
      ["Entradas", totals.entries],
      ["Formas", totals.forms],
      ["Aceções", totals.senses],
      ["Definições", totals.definitions],
      ["Remissões", totals.references],
      ["Etiquetas", totals.labels],
    ]);
    renderSourceComparison(corpus);
    bars(
      "#collection-chart",
      corpus.collections,
      collectionLabel,
      (value) => searchUrl({ source: value })
    );
    bars("#publication-chart", corpus.publication, (value) =>
      value === "published" ? "Publicadas" : "Não publicadas"
    );
    table(
      "#external-source-table",
      ["Fonte", "Registos de origem", "Enriquecimentos associados", "Esperadas", "Diferença", "Licença", "Nota"],
      (corpus.external_sources || []).map((item) => [
        linkCell(
          collectionLabel(item.source_code),
          item.source_url || searchUrl({ source: item.source_code }),
          Boolean(item.source_url)
        ),
        number(item.source_records ?? item.imported_entries),
        number(item.imported_entries),
        number(item.expected_entries),
        number((item.source_records ?? item.imported_entries) - item.expected_entries),
        item.source_license || "—",
        item.note || "Contagem coincidente",
      ])
    );
    bars(
      "#status-chart",
      corpus.source_statuses,
      (value, item) => item.label,
      (value) => searchUrl({ status: value })
    );
    bars("#workflow-chart", corpus.workflow_statuses, workflowLabel);

    const quality = corpus.quality || {};
    kpis("#quality-kpis", [
      ["Sem xml:id", quality.missing_xml_id],
      ["Sem lema", quality.missing_lemma],
      ["Sem classe", quality.missing_grammar],
      ["Sem estado", quality.missing_source_status],
      ["IDs duplicados", quality.duplicate_xml_ids],
      ["Sem aceções", quality.entries_without_senses],
      ["Aceções sem definição", quality.senses_without_definition],
      ["Aceções definidas por <gloss>", quality.senses_using_gloss],
      ["Entradas com <gloss>", quality.entries_with_gloss],
      ["Com alertas", quality.entries_with_anomalies],
      ["Classes por mapear", quality.unmapped_grammar_values],
      ["Domínios por mapear", quality.unmapped_domain_values],
      ["Estados por mapear", quality.unmapped_status_values],
    ], true);

    table(
      "#collection-status-table",
      ["Coleção", "Estado original", "Entradas"],
      (corpus.collection_status || []).map((item) => [
        linkCell(
          collectionLabel(item.collection),
          searchUrl({ source: item.collection })
        ),
        linkCell(
          item.status_label,
          searchUrl({ source: item.collection, status: item.status })
        ),
        number(item.count),
      ])
    );
    table(
      "#anomaly-table",
      ["Alerta", "Entradas"],
      (corpus.anomalies || []).map((item) => [
        item.value || "Sem alerta",
        number(item.count),
      ])
    );
    facetTable("#grammar-table", corpus.grammar || [], "grammar");
    facetTable("#domain-table", corpus.domains || [], "domain");
  }

  function renderSourceComparison(corpus) {
    const collections = Object.fromEntries(
      (corpus.collections || []).map((item) => [item.value, item.count])
    );
    const statuses = corpus.collection_status || [];
    [
      ["DLP", "#dlp-source-data"],
      ["VOCABULARIO", "#vocabulary-source-data"],
    ].forEach(([source, selector]) => {
      const target = document.querySelector(selector);
      target.innerHTML = `
        <a class="source-total stats-link"
           href="${h(searchUrl({ source }))}">${h(number(collections[source] || 0))} entradas</a>`;
      bars(
        selector,
        statuses
          .filter((item) => item.collection === source)
          .map((item) => ({
            value: item.status,
            label: item.status_label,
            count: item.count,
          })),
        (value, item) => item.label,
        (value) => searchUrl({ source, status: value }),
        true
      );
    });
  }

  function renderUsage(usage) {
    document.querySelector("#usage-privacy").textContent =
      usage.privacy || "O registo de utilização ainda não está disponível.";
    const overview = usage.overview || {};
    kpis("#usage-kpis", [
      ["Pedidos", overview.requests],
      ["Visitantes", overview.visitors],
      ["Visualizações de página", overview.page_views],
      ["Pesquisas", overview.searches],
      ["Tempo médio da API", `${number(overview.average_duration_ms)} ms`],
    ]);
    dailyChart(usage.daily || []);
    bars("#search-chart", usage.searches, (value) => `“${value}”`);
    bars("#agent-chart", usage.agents);
    bars("#route-chart", usage.routes);
    bars("#http-chart", usage.status_codes, (value) => `HTTP ${value}`);
    bars("#referrer-chart", usage.referrers);
    bars("#ip-chart", usage.ips);
    bars("#hour-chart", usage.hours, (value) => `${value}:00`);
    table(
      "#recent-table",
      ["Data", "IP", "Tipo", "Rota/pesquisa", "HTTP", "Duração"],
      (usage.recent || []).map((item) => [
        dateTime(item.created_at),
        item.client_ip || "—",
        item.route_kind,
        item.search_query
          ? `${item.path} — “${item.search_query}”`
          : item.path,
        item.status_code,
        `${number(item.duration_ms)} ms`,
      ])
    );
  }

  function kpis(selector, items, warn = false) {
    document.querySelector(selector).innerHTML = items.map(([label, value]) => `
      <article class="stats-kpi${warn && Number(value) ? " stats-kpi--warn" : ""}">
        <strong>${h(number(value))}</strong>
        <span>${h(label)}</span>
      </article>
    `).join("");
  }

  function bars(
    selector,
    items = [],
    labeler = (value) => value,
    linker = null,
    preserveLeading = false
  ) {
    const target = document.querySelector(selector);
    if (!items.length) {
      target.innerHTML = '<p class="stats-empty">Ainda sem dados.</p>';
      return;
    }
    const max = Math.max(...items.map((item) => Number(item.count) || 0), 1);
    const leading = preserveLeading
      ? target.querySelector(".source-total")?.outerHTML || ""
      : "";
    target.innerHTML = `${leading}<ol class="bar-list">${items.map((item) => `
      <li>
        <div class="bar-list__label">
          ${linker
            ? `<a class="stats-link" href="${h(linker(item.value, item))}">${h(labeler(item.value, item))}</a>`
            : `<span>${h(labeler(item.value, item))}</span>`}
          <strong>${h(number(item.count))}</strong>
        </div>
        <span class="bar-list__track">
          <span style="width:${Math.max(1, (Number(item.count) / max) * 100)}%"></span>
        </span>
      </li>
    `).join("")}</ol>`;
  }

  function dailyChart(items) {
    const target = document.querySelector("#daily-chart");
    if (!items.length) {
      target.innerHTML = '<p class="stats-empty">O log começa a ser preenchido com a utilização da nova versão.</p>';
      return;
    }
    const max = Math.max(...items.map((item) => Number(item.requests) || 0), 1);
    target.innerHTML = items.map((item) => `
      <div class="timeline-chart__day" title="${h(item.date)}: ${h(number(item.requests))} pedidos">
        <span class="timeline-chart__value">${h(number(item.requests))}</span>
        <span class="timeline-chart__bar" style="height:${Math.max(4, (Number(item.requests) / max) * 150)}px"></span>
        <span class="timeline-chart__label">${h(item.date.slice(5))}</span>
      </div>
    `).join("");
  }

  function facetTable(selector, items, kind) {
    table(
      selector,
      ["Valor original e expansão", "Entradas", "Mapeamento"],
      items.map((item) => [
        linkCell(item.label, searchUrl({ [kind]: item.value })),
        number(item.count),
        item.unmapped ? "Por rever" : "Mapeado",
      ]),
      items.map((item) => item.unmapped)
    );
  }

  function table(selector, headings, rows, warnings = []) {
    const target = document.querySelector(selector);
    if (!rows.length) {
      target.innerHTML = '<p class="stats-empty">Sem dados.</p>';
      return;
    }
    target.innerHTML = `
      <table class="stats-table">
        <thead><tr>${headings.map((item) => `<th>${h(item)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((row, index) => `
          <tr${warnings[index] ? ' class="needs-review"' : ""} data-filter-text="${h(row.map(cellText).join(" ").toLocaleLowerCase("pt"))}">
            ${row.map((cell) => `<td>${cellHtml(cell)}</td>`).join("")}
          </tr>
        `).join("")}</tbody>
      </table>`;
  }

  function filterTable(input) {
    const query = input.value.trim().toLocaleLowerCase("pt");
    document.querySelectorAll(
      `#${input.dataset.tableFilter} tbody tr`
    ).forEach((row) => {
      row.hidden = query && !row.dataset.filterText.includes(query);
    });
  }

  function collectionLabel(value) {
    return value === "DLP" ? "Dicionário" :
      value === "VOCABULARIO" ? "Vocabulário Ortográfico" :
      value === "SPE" ? "Glossário SPE" : value;
  }

  function workflowLabel(value) {
    const labels = {
      IMPORTED: "Importada para a PoC",
      EDITING: "Em edição",
      REVIEW: "Em revisão",
      VALIDATED: "Validada",
      PUBLISHED: "Publicada",
    };
    return labels[value] || value;
  }

  function linkCell(label, href, external = false) {
    return { label, href, external };
  }

  function cellText(cell) {
    return typeof cell === "object" && cell !== null ? cell.label : cell;
  }

  function cellHtml(cell) {
    if (typeof cell !== "object" || cell === null) return h(cell);
    return `<a class="stats-link" href="${h(cell.href)}"${cell.external ? ' rel="noreferrer"' : ""}>${h(cell.label)}</a>`;
  }

  function searchUrl(parameters = {}) {
    const query = new URLSearchParams();
    Object.entries(parameters).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") {
        query.set(key, value);
      }
    });
    return appUrl(`/?${query}`);
  }

  function appUrl(path = "/") {
    const normalized = path.startsWith("/") ? path : `/${path}`;
    return `${basePath}${normalized}` || "/";
  }

  function number(value) {
    if (typeof value === "string" && /[^\d.,-]/.test(value)) return value;
    return new Intl.NumberFormat("pt-PT", {
      maximumFractionDigits: 1,
    }).format(Number(value) || 0);
  }

  function dateTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : new Intl.DateTimeFormat("pt-PT", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(date);
  }

  function h(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }
})();
