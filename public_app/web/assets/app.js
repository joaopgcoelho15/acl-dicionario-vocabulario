(() => {
  "use strict";

  const basePath = (
    document.querySelector('meta[name="acl-base-path"]')?.content || ""
  ).replace(/\/+$/, "");

  const els = {
    form: document.querySelector("#search-form"),
    input: document.querySelector("#search-input"),
    collectionTabs: [...document.querySelectorAll(".collection-tab")],
    collectionLinks: [...document.querySelectorAll("[data-collection-link]")],
    grammar: document.querySelector("#grammar-filter"),
    domain: document.querySelector("#domain-filter"),
    status: document.querySelector("#status-filter"),
    clearFilters: document.querySelector("#clear-filters"),
    clearAll: document.querySelector("#clear-all"),
    filterToggle: document.querySelector("#filter-toggle"),
    filters: document.querySelector("#filters"),
    activeFilterCount: document.querySelector("#active-filter-count"),
    workspaceMeta: document.querySelector("#workspace-meta"),
    summary: document.querySelector("#result-summary"),
    results: document.querySelector("#results-list"),
    alphabet: document.querySelector("#alphabet-index"),
    alphabetGuide: document.querySelector("#alphabet-guide"),
    alphabetCurrent: document.querySelector("#alphabet-current"),
    alphabetAll: document.querySelector("#alphabet-all"),
    loadMore: document.querySelector("#load-more"),
    entry: document.querySelector("#entry-panel"),
    countAll: document.querySelector("#count-all"),
    countDlp: document.querySelector("#count-dlp"),
    countVocabulary: document.querySelector("#count-vocabulary"),
  };

  const state = {
    query: "",
    collection: "",
    grammar: "",
    domain: "",
    status: "",
    limit: 30,
    offset: 0,
    total: 0,
    selectedId: null,
    focusSenseId: null,
    searchController: null,
    browsing: false,
    browseCursor: null,
    browseLoading: false,
    browseLoaded: 0,
    browseTotal: null,
    browseHasMore: false,
    browseStart: "",
    entryCounts: null,
    globalFacets: null,
    currentFacets: null,
  };

  let inputTimer = null;

  renderAlphabet();
  bindEvents();
  initialise();

  async function initialise() {
    void loadStatistics();
    void loadGlobalFacets();
    const path = currentAppPath();
    const routeId = path.startsWith("/id/") ? path.slice(4).replace(/\/$/, "") : "";
    const params = new URLSearchParams(window.location.search);

    if (path === "/dicionario") state.collection = "DLP";
    if (path === "/vocabulario") state.collection = "VOCABULARIO";
    if (params.get("source")) state.collection = params.get("source");
    state.grammar = params.get("grammar") || "";
    state.domain = params.get("domain") || "";
    state.status = params.get("status") || "";
    state.browseStart = params.get("letter") || "";
    updateCollectionTabs();
    updateActiveFilterCount();

    if (routeId) {
      await loadPersistentId(routeId);
      return;
    }

    state.query = params.get("q") || "";
    els.input.value = state.query;
    if (!state.query) {
      renderInitialState();
      return;
    }
    await runSearch({ selectFirst: true });
  }

  function bindEvents() {
    els.form.addEventListener("submit", (event) => {
      event.preventDefault();
      state.query = els.input.value.trim();
      state.offset = 0;
      updateSearchUrl();
      runSearch({ selectFirst: true });
    });

    els.input.addEventListener("input", () => {
      window.clearTimeout(inputTimer);
      inputTimer = window.setTimeout(() => {
        const value = els.input.value.trim();
        if (value.length >= 2 || value.length === 0) {
          state.query = value;
          state.offset = 0;
          updateSearchUrl();
          runSearch({ selectFirst: true });
        }
      }, 350);
    });

    els.collectionTabs.forEach((button) => {
      button.addEventListener("click", () => {
        state.collection = button.dataset.collection || "";
        state.offset = 0;
        updateCollectionTabs();
        updateSearchUrl();
        if (state.browsing) browseCatalogue({ reset: true });
        else runSearch({ selectFirst: true });
      });
    });

    els.collectionLinks.forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        state.collection = link.dataset.collectionLink || "";
        state.offset = 0;
        updateCollectionTabs();
        updateSearchUrl();
        runSearch({ selectFirst: true });
      });
    });

    [els.grammar, els.domain, els.status].forEach((select) => {
      select.addEventListener("change", () => {
        state.grammar = els.grammar.value;
        state.domain = els.domain.value;
        state.status = els.status.value;
        state.offset = 0;
        updateActiveFilterCount();
        if (state.query) runSearch({ selectFirst: true });
        else browseCatalogue({ reset: true });
      });
    });

    els.clearFilters.addEventListener("click", () => {
      state.grammar = "";
      state.domain = "";
      state.status = "";
      els.grammar.value = "";
      els.domain.value = "";
      els.status.value = "";
      state.offset = 0;
      updateActiveFilterCount();
      if (state.query) runSearch({ selectFirst: true });
      else browseCatalogue({ reset: true });
    });

    els.clearAll.addEventListener("click", () => {
      window.clearTimeout(inputTimer);
      state.query = "";
      state.collection = "";
      state.grammar = "";
      state.domain = "";
      state.status = "";
      state.browseStart = "";
      state.offset = 0;
      els.input.value = "";
      els.grammar.value = "";
      els.domain.value = "";
      els.status.value = "";
      updateCollectionTabs();
      updateActiveFilterCount();
      updateSearchUrl();
      renderInitialState();
      els.input.focus();
    });

    els.filterToggle.addEventListener("click", () => {
      const open = els.filters.classList.toggle("is-open");
      els.filterToggle.setAttribute("aria-expanded", String(open));
    });

    els.loadMore.addEventListener("click", () => {
      if (state.browsing) {
        browseCatalogue();
      } else {
        state.offset += state.limit;
        runSearch({ append: true, selectFirst: false });
      }
    });

    els.results.addEventListener("scroll", () => {
      if (!state.browsing || state.browseLoading || !state.browseHasMore) return;
      const hasVerticalOverflow =
        els.results.scrollHeight > els.results.clientHeight + 1;
      const hasHorizontalOverflow =
        els.results.scrollWidth > els.results.clientWidth + 1;
      const nearVerticalEnd =
        hasVerticalOverflow &&
        els.results.scrollHeight - els.results.clientHeight - els.results.scrollTop < 220;
      const nearHorizontalEnd =
        hasHorizontalOverflow &&
        els.results.scrollWidth - els.results.clientWidth - els.results.scrollLeft < 220;
      if (nearVerticalEnd || nearHorizontalEnd) browseCatalogue();
    });

    els.alphabet.addEventListener("click", (event) => {
      const button = event.target.closest("[data-letter]");
      if (!button) return;
      state.query = "";
      els.input.value = "";
      state.browseStart = button.dataset.letter || "";
      state.offset = 0;
      updateSearchUrl();
      browseCatalogue({ reset: true });
    });

    els.alphabetAll.addEventListener("click", () => {
      state.query = "";
      els.input.value = "";
      state.browseStart = "";
      state.offset = 0;
      updateSearchUrl();
      browseCatalogue({ reset: true });
    });

    document.addEventListener("keydown", (event) => {
      if (
        event.key === "/" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
      ) {
        event.preventDefault();
        els.input.focus();
      }
    });

    window.addEventListener("popstate", () => {
      const path = currentAppPath();
      if (path.startsWith("/id/")) {
        loadPersistentId(path.slice(4).replace(/\/$/, ""));
      } else {
        state.query = new URLSearchParams(window.location.search).get("q") || "";
        els.input.value = state.query;
        state.offset = 0;
        runSearch({ selectFirst: true });
      }
    });
  }

  async function loadStatistics() {
    try {
      const stats = await getJson(appUrl("/api/entry-counts"));
      state.entryCounts = stats;
      updateCollectionCounts(state.currentFacets || state.globalFacets);
      if (state.browsing) renderBrowseSummary();
    } catch {
      els.countAll.textContent = "—";
    }
  }

  async function loadGlobalFacets() {
    try {
      state.globalFacets = await getJson(appUrl("/api/facets"));
      updateFacets(state.currentFacets || state.globalFacets);
    } catch {
      state.globalFacets = null;
    }
  }

  async function runSearch({ append = false, selectFirst = false } = {}) {
    if (!state.query) {
      renderInitialState();
      return;
    }
    state.browsing = false;
    state.browseCursor = null;
    state.browseStart = "";
    els.alphabet.hidden = true;
    els.alphabetGuide.hidden = true;
    updateAlphabet();
    els.loadMore.textContent = "Mostrar mais resultados";
    if (state.searchController) state.searchController.abort();
    state.searchController = new AbortController();
    if (!append) {
      els.results.innerHTML = loadingCards();
      els.summary.textContent = "A pesquisar…";
    }

    const params = new URLSearchParams({
      query: state.query,
      limit: String(state.limit),
      offset: String(state.offset),
    });
    if (state.collection) params.set("collection", state.collection);
    if (state.grammar) params.set("grammar", state.grammar);
    if (state.domain) params.set("domain", state.domain);
    if (state.status) params.set("status", state.status);

    try {
      const data = await getJson(appUrl(`/api/entries?${params}`), {
        signal: state.searchController.signal,
      });
      state.total = data.total;
      renderSearchSummary(data);
      updateFacets(data.facets);
      renderResults(data.items, { append });
      els.loadMore.hidden = state.offset + data.items.length >= data.total;

      if (selectFirst && data.items.length) {
        await loadEntry(data.items[0].xml_id, { navigate: false });
      } else if (!data.items.length && !append) {
        renderEmptyEntry("Sem resultados", "Experimente outra palavra ou remova alguns filtros.");
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      els.results.innerHTML = `<div class="error-state"><p>Não foi possível pesquisar.</p></div>`;
      renderEmptyEntry("Pesquisa indisponível", "Confirme que a base de dados está carregada e volte a tentar.");
    }
  }

  function renderSearchSummary(data) {
    els.workspaceMeta.hidden = false;
    const queryText = state.query ? ` para “${h(state.query)}”` : "";
    els.summary.innerHTML = `<strong>${number(data.total)}</strong> ${data.total === 1 ? "resultado" : "resultados"}${queryText}`;
  }

  function renderResults(items, { append }) {
    const html = items.map((item) => {
      const selected = item.xml_id === state.selectedId ? " is-selected" : "";
      const workflow = workflowClass(item.workflow_status || "PUBLISHED");
      const summary = item.summary
        ? `<p class="result-card__summary">${highlight(item.summary, state.query)}</p>`
        : "";
      return `
        <button class="result-card ${workflow}${selected}" type="button" data-entry-id="${h(item.xml_id || "")}">
          <span class="result-card__top">
            <h3>${highlight(item.lemma || "(sem lema)", state.query)}</h3>
            <span class="result-card__source">${h(sourceLabel(item))}</span>
          </span>
          <p class="result-card__grammar">${h(item.grammatical_label || item.grammatical_info || "sem classificação")}</p>
          ${summary}
        </button>`;
    }).join("");

    if (append) els.results.insertAdjacentHTML("beforeend", html);
    else els.results.innerHTML = html || `<div class="error-state"><p>Sem resultados.</p></div>`;

    els.results.querySelectorAll("[data-entry-id]").forEach((button) => {
      button.addEventListener("click", () => {
        loadEntry(button.dataset.entryId, { navigate: true });
      });
    });
  }

  async function loadEntry(xmlId, { navigate = true, focusSenseId = null } = {}) {
    if (!xmlId) return;
    state.selectedId = xmlId;
    state.focusSenseId = focusSenseId;
    markSelectedResult();
    els.entry.innerHTML = `<div class="entry-empty"><div class="loading-card"></div></div>`;
    try {
      const entry = await getJson(
        appUrl(`/api/entries/${encodeURIComponent(xmlId)}`)
      );
      renderEntry(entry, focusSenseId);
      if (navigate) {
        const target = focusSenseId || xmlId;
        window.history.pushState(
          {},
          "",
          appUrl(`/id/${encodeURIComponent(target)}`)
        );
      }
    } catch {
      renderEmptyEntry("Entrada indisponível", "O identificador não foi encontrado ou é ambíguo.");
    }
  }

  async function loadPersistentId(xmlId) {
    els.entry.innerHTML = `<div class="entry-empty"><div class="loading-card"></div></div>`;
    try {
      const resolution = await getJson(
        appUrl(`/api/resolve/${encodeURIComponent(xmlId)}`)
      );
      const entry = resolution.matches[0];
      state.selectedId = entry.xml_id;
      state.focusSenseId = resolution.kind === "sense" ? xmlId : null;
      state.query = entry.lemma || "";
      els.input.value = state.query;
      renderEntry(entry, state.focusSenseId);
      state.offset = 0;
      await runSearch({ selectFirst: false });
      markSelectedResult();
    } catch {
      renderEmptyEntry("Identificador não encontrado", `Não foi possível resolver “${xmlId}”.`);
    }
  }

  function renderEntry(entry, focusSenseId) {
    els.entry.classList.remove("lexical-links-off");
    const lexical = entry.lexical || {};
    const pronunciation = [
      ...(lexical.syllabifications || []).map((value) => `Sílabas: ${value}`),
      ...(lexical.pronunciations || []).map((value) => `Pronúncia: ${value}`),
    ];
    const source = sourceLabel(entry);
    const imageMarkup = (images = []) => {
      const unique = [...new Map(images.map((image) => [
        image.url,
        image,
      ])).values()];
      if (!unique.length) return "";
      return `<div class="entry-images">${unique.map((image) => {
        const caption = image.caption || entry.lemma || "Imagem da entrada";
        return `<figure>
          <img src="${appUrl(`/entry-images/${encodeURIComponent(image.url)}`)}"
               alt="${h(caption)}" loading="lazy">
          ${image.caption ? `<figcaption>${h(image.caption)}</figcaption>` : ""}
        </figure>`;
      }).join("")}</div>`;
    };
    const glosses = (lexical.gloss_items || []).map((gloss) =>
      `<p>${renderLinkedText(gloss.segments, gloss.value)}</p>`
    ).join("");

    let previousSection = null;
    let visibleIndex = 0;
    const senses = (lexical.senses || []).map((sense) => {
      visibleIndex += 1;
      let heading = "";
      if (sense.section && sense.section !== previousSection) {
        heading = `<h4 class="related-heading">${h(sense.section)}</h4>`;
        previousSection = sense.section;
      }
      const labels = (sense.labels || []).map((label) =>
        `<span class="usage-label">${h(fullLabel(label.label || label.value))}</span>`
      ).join("");
      const examples = (sense.examples || []).map((example) =>
        `<blockquote class="example">“${h(example.quote)}”${example.source ? ` <cite>— ${h(example.source)}</cite>` : ""}</blockquote>`
      ).join("");
      const notes = (sense.notes || []).map((note) =>
        `<aside class="sense__note">${h(note.value)}</aside>`
      ).join("");
      const sourceAttribution = sense.source?.code === "SPE" ? `
        <p class="sense__source">
          Enriquecimento terminológico:
          <a href="${h(sense.source.url)}" rel="noreferrer">Sociedade Portuguesa de Estatística</a>
          ${sense.source.license ? ` · ${h(sense.source.license)}` : ""}
        </p>` : "";
      const references = (sense.references || []).map((reference) => {
        const url = appUrl(`/?q=${encodeURIComponent(reference.value)}`);
        return `<a href="${url}" data-reference="${h(reference.value)}">${h(reference.value)}</a>`;
      }).join(" · ");
      const focused = sense.xml_id === focusSenseId ? " is-focused" : "";
      const depth = Math.min(Math.max(Number(sense.depth) || 1, 1), 3);
      return `${heading}
        <section class="sense sense--depth-${depth}${focused}" id="${h(sense.xml_id || `sense-${visibleIndex}`)}">
          <span class="sense__number">${h(sense.number || String(visibleIndex))}.</span>
          <div>
            ${labels ? `<div class="sense__labels">${labels}</div>` : ""}
            <p class="sense__definition">${renderLinkedText(
              sense.definition_segments,
              sense.definition || "[definição em preparação]"
            )}</p>
            ${examples ? `<div class="example-list">${examples}</div>` : ""}
            ${imageMarkup(sense.images)}
            ${notes ? `<div class="sense__notes">${notes}</div>` : ""}
            ${sourceAttribution}
            ${references ? `<div class="reference-list">Ver também: ${references}</div>` : ""}
          </div>
        </section>`;
    }).join("");

    const etymology = (lexical.etymologies || []).map((value) =>
      `<div class="etymology">${h(value)}</div>`
    ).join("");
    const entryReferences = (lexical.references || []).map((reference) => {
      const url = appUrl(`/?q=${encodeURIComponent(reference.value)}`);
      return `<a href="${url}" data-reference="${h(reference.value)}">${h(reference.value)}</a>`;
    }).join(" · ");
    const entryNotes = (lexical.notes || []).map((note) =>
      `<p class="entry-note">${h(note.value)}</p>`
    ).join("");
    const variants = (lexical.orthographies || [])
      .map((form) => form.value)
      .filter((value) => value && value !== entry.lemma);
    const debugJson = h(JSON.stringify(entry, null, 2));

    els.entry.innerHTML = `
      <header class="entry-header ${workflowClass(entry.workflow_status || "PUBLISHED")}">
        <div class="entry-header__meta">
          <span class="source-badge">${h(source)}</span>
          <span class="status-badge">${h(fullLabel(entry.source_status_label || statusLabel(entry.source_status)))}</span>
        </div>
        <div class="entry-title-row">
          <h2>${h(entry.lemma || "(sem lema)")}</h2>
          <span class="entry-grammar">${h(fullLabel(entry.grammatical_label || entry.grammatical_info || ""))}</span>
        </div>
        ${pronunciation.length ? `<div class="entry-pronunciation">${pronunciation.map((value) => `<span class="meta-chip">${h(value)}</span>`).join("")}</div>` : ""}
        ${variants.length ? `<p class="entry-variants"><strong>Outras formas:</strong> ${h(variants.join(" · "))}</p>` : ""}
      </header>
      ${glosses ? `
        <section class="entry-section entry-gloss">
          <h3 class="entry-section__title">Enquadramento da entrada</h3>
          ${glosses}
        </section>` : ""}
      ${lexical.images?.length ? `
        <section class="entry-section">
          <h3 class="entry-section__title">Imagens</h3>
          ${imageMarkup(lexical.images)}
        </section>` : ""}
      ${senses ? `
        <section class="entry-section">
          <div class="entry-section__heading-row">
            <h3 class="entry-section__title">Aceções e definições <span>(${lexical.senses.length})</span></h3>
            ${lexical.lexical_links?.count ? `
              <label class="lexical-toggle">
                <input id="lexical-links-toggle" type="checkbox" checked>
                Hiperligações lexicais no texto
              </label>` : ""}
          </div>
          ${senses}
        </section>` : `
        <section class="entry-section">
          <h3 class="entry-section__title">Informação ortográfica</h3>
          <p>Esta entrada pertence ao Vocabulário Ortográfico e não contém aceções lexicográficas.</p>
        </section>`}
      ${entryReferences ? `
        <section class="entry-section">
          <h3 class="entry-section__title">Relações e remissões</h3>
          <div class="reference-list">Ver também: ${entryReferences}</div>
        </section>` : ""}
      ${entryNotes ? `
        <section class="entry-section">
          <h3 class="entry-section__title">Notas</h3>
          ${entryNotes}
        </section>` : ""}
      ${etymology ? `
        <section class="entry-section">
          <h3 class="entry-section__title">Etimologia</h3>
          ${etymology}
        </section>` : ""}
      <details class="technical-details">
        <summary>Identificação e metadados técnicos</summary>
        <div class="technical-details__content">
          <p><strong>Identificador persistente:</strong> <code>${h(entry.xml_id || "(sem xml:id)")}</code></p>
          <p><strong>Origem:</strong> ${h(source)}${entry.source_url ? ` · <a href="${h(entry.source_url)}" rel="noreferrer">${h(entry.source_url)}</a>` : ""}</p>
          <p><strong>Workflow:</strong> ${h(entry.workflow_status)}</p>
          <p><strong>Alertas:</strong> ${h(entry.anomaly_flags || "nenhum")}</p>
          <div class="debug-compare">
            <section>
              <h4>JSON usado na interface</h4>
              <pre class="debug-output"><code data-debug-json>${debugJson}</code></pre>
            </section>
            <section>
              <h4>XML original</h4>
              <pre class="debug-output"><code data-debug-xml>A carregar XML…</code></pre>
            </section>
          </div>
        </div>
      </details>`;

    els.entry.querySelectorAll("[data-reference]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        state.query = link.dataset.reference;
        els.input.value = state.query;
        state.offset = 0;
        updateSearchUrl();
        runSearch({ selectFirst: true });
        window.scrollTo({ top: document.querySelector(".search-workspace").offsetTop - 90, behavior: "smooth" });
      });
    });

    const linksToggle = els.entry.querySelector("#lexical-links-toggle");
    linksToggle?.addEventListener("change", () => {
      els.entry.classList.toggle("lexical-links-off", !linksToggle.checked);
    });

    const technicalDetails = els.entry.querySelector(".technical-details");
    let rawXmlLoaded = false;
    technicalDetails.addEventListener("toggle", () => {
      if (!technicalDetails.open || rawXmlLoaded) return;
      rawXmlLoaded = true;
      const xmlCode = technicalDetails.querySelector("[data-debug-xml]");
      void getJson(
        appUrl(`/api/debug/entries/${encodeURIComponent(entry.xml_id)}`)
      ).then((debugEntry) => {
        xmlCode.textContent = debugEntry.raw_xml || "XML não disponível.";
      }).catch(() => {
        xmlCode.textContent = "Não foi possível carregar o XML original.";
      });
    });

    if (focusSenseId) {
      window.requestAnimationFrame(() => {
        document.getElementById(focusSenseId)?.scrollIntoView({ block: "center", behavior: "smooth" });
      });
    }
  }

  function updateFacets(facets = {}) {
    state.currentFacets = facets;
    const global = state.globalFacets || facets;
    updateSelect(els.grammar, "Todas as classes", facets.grammar || [], global.grammar || [], state.grammar);
    updateSelect(els.domain, "Todos os domínios", facets.domains || [], global.domains || [], state.domain);
    const statuses = (facets.statuses || [])
      .filter((item) => item.value)
      .map((item) => ({
        ...item,
        label: item.label || statusLabel(item.value),
      }));
    const globalStatuses = (global.statuses || []).filter(item => item.value).map(item => ({...item,label:item.label || statusLabel(item.value)}));
    updateSelect(els.status, "Todos os estados", statuses, globalStatuses, state.status);
    updateCollectionCounts(facets);
    updateActiveFilterCount();
  }

  function facetContextActive() { return Boolean(state.query || state.collection || state.grammar || state.domain || state.status || state.browseStart); }

  function updateSelect(select, emptyLabel, items, globalItems, selected) {
    const current = Object.fromEntries(items.map(item => [item.value,item]));
    const contextual = facetContextActive();
    const total = state.query ? state.total : (state.browseTotal ?? state.entryCounts?.entries ?? 0);
    const globalTotal = state.entryCounts?.entries ?? total;
    const countLabel = (available, global) => contextual ? `${number(available)}/${number(global)}` : number(global);
    select.innerHTML = `<option value="">${h(emptyLabel)} [${countLabel(total,globalTotal)}]</option>` + globalItems.map((item) => {
      const available = Number(current[item.value]?.count || 0); const disabled = contextual && available === 0 && item.value !== selected;
      return `<option value="${h(item.value)}"${item.value === selected ? " selected" : ""}${disabled ? " disabled" : ""}>${h(item.label || item.value || "Sem estado")} [${countLabel(available,Number(item.count)||0)}]</option>`;
    }).join("");
  }

  function updateCollectionCounts(facets = {}) {
    const current = Object.fromEntries((facets?.collections || []).map(item => [item.value,Number(item.count)||0]));
    const baseline = state.entryCounts?.collections || Object.fromEntries((state.globalFacets?.collections || []).map(item => [item.value,Number(item.count)||0]));
    const contextual = facetContextActive(); const show = (value, global) => contextual ? `[${number(value)}/${number(global)}]` : `[${number(global)}]`;
    const dlp = current.DLP ?? baseline.DLP ?? 0; const vocabulary = current.VOCABULARIO ?? baseline.VOCABULARIO ?? 0;
    els.countDlp.textContent = show(dlp,baseline.DLP ?? dlp); els.countVocabulary.textContent = show(vocabulary,baseline.VOCABULARIO ?? vocabulary);
    els.countAll.textContent = show(dlp + vocabulary,(baseline.DLP || 0) + (baseline.VOCABULARIO || 0));
  }

  function updateCollectionTabs() {
    els.collectionTabs.forEach((button) => {
      const active = (button.dataset.collection || "") === state.collection;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function updateActiveFilterCount() {
    const count = [state.grammar, state.domain, state.status].filter(Boolean).length;
    els.activeFilterCount.textContent = count ? `(${count})` : "";
  }

  function markSelectedResult() {
    els.results.querySelectorAll("[data-entry-id]").forEach((button) => {
      button.classList.toggle("is-selected", button.dataset.entryId === state.selectedId);
    });
  }

  function updateSearchUrl() {
    const params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.collection) params.set("source", state.collection);
    if (state.grammar) params.set("grammar", state.grammar);
    if (state.domain) params.set("domain", state.domain);
    if (state.status) params.set("status", state.status);
    if (!state.query && state.browseStart) params.set("letter", state.browseStart);
    window.history.replaceState(
      {},
      "",
      appUrl(`/${params.size ? `?${params}` : ""}`)
    );
  }

  function renderEmptyEntry(title, message) {
    els.entry.innerHTML = `<div class="error-state"><h2>${h(title)}</h2><p>${h(message)}</p></div>`;
  }

  function renderInitialState() {
    if (state.searchController) state.searchController.abort();
    state.offset = 0;
    state.total = 0;
    state.selectedId = null;
    state.browsing = false;
    state.browseCursor = null;
    state.browseLoaded = 0;
    state.browseTotal = null;
    state.browseHasMore = false;
    els.alphabet.hidden = false;
    els.alphabetGuide.hidden = false;
    updateAlphabet();
    if (state.globalFacets) updateFacets(state.globalFacets);
    void browseCatalogue({ reset: true });
  }

  async function browseCatalogue({ reset = false } = {}) {
    if (state.browseLoading) return;
    state.browseLoading = true;
    if (reset) {
      state.browsing = true;
      state.browseCursor = null;
      state.browseLoaded = 0;
      state.browseTotal = null;
      state.browseHasMore = false;
      state.selectedId = null;
      els.results.scrollTop = 0;
      els.results.scrollLeft = 0;
      updateAlphabet();
      els.results.innerHTML = loadingCards();
      els.entry.innerHTML = `<div class="entry-empty"><p>A carregar a primeira entrada…</p></div>`;
    }

    const params = new URLSearchParams({ limit: "60" });
    if (state.collection) params.set("collection", state.collection);
    if (state.grammar) params.set("grammar", state.grammar);
    if (state.domain) params.set("domain", state.domain);
    if (state.status) params.set("status", state.status);
    if (state.browseCursor) params.set("cursor", state.browseCursor);
    if (state.browseStart) params.set("letter", state.browseStart);
    try {
      const data = await getJson(appUrl(`/api/catalogue?${params}`));
      renderResults(data.items, { append: !reset });
      state.browseLoaded += data.items.length;
      if (data.total !== null && data.total !== undefined) {
        state.browseTotal = data.total;
      }
      state.browseCursor = data.next_cursor;
      state.browseHasMore = data.has_more;
      updateFacets(data.facets || {});
      els.loadMore.hidden = true;
      renderBrowseSummary();
      if (reset && data.items.length) await loadEntry(data.items[0].xml_id,{navigate:false});
      else if (reset) renderEmptyEntry("Sem resultados", "Remova alguns filtros ou escolha outra letra.");
    } catch {
      if (reset) {
        els.results.innerHTML = `<div class="error-state"><p>Não foi possível carregar o catálogo.</p></div>`;
      }
    } finally {
      state.browseLoading = false;
    }
  }

  function renderBrowseSummary() {
    els.workspaceMeta.hidden = false;
    const total = state.browseTotal;
    const start = state.browseStart
      ? ` a partir de <strong>${h(state.browseStart)}</strong>`
      : "";
    els.summary.innerHTML = total !== null
      ? `<strong>${number(total)}</strong> resultados${start}`
      : "";
  }

  function loadingCards() {
    return `<div class="loading-card"></div><div class="loading-card"></div><div class="loading-card"></div>`;
  }

  async function getJson(url, options = {}) {
    const response = await window.fetch(url, {
      headers: { Accept: "application/json" },
      ...options,
    });
    if (!response.ok) {
      const error = new Error(`HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function appUrl(path = "/") {
    const normalisedPath = path.startsWith("/") ? path : `/${path}`;
    return `${basePath}${normalisedPath}` || "/";
  }

  function currentAppPath() {
    const pathname = decodeURIComponent(window.location.pathname);
    if (!basePath) return pathname;
    if (pathname === basePath) return "/";
    return pathname.startsWith(`${basePath}/`)
      ? pathname.slice(basePath.length)
      : pathname;
  }

  function renderAlphabet() {
    const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
    els.alphabet.innerHTML = `
      ${letters.map((letter) => `
        <button type="button" data-letter="${letter}"
                aria-label="Mostrar entradas começadas por ${letter}"
                title="Mostrar entradas começadas por ${letter}"
                aria-pressed="false">${letter}</button>
      `).join("")}`;
    updateAlphabet();
  }

  function updateAlphabet() {
    els.alphabet.querySelectorAll("[data-letter]").forEach((button) => {
      const active = button.dataset.letter === state.browseStart;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-current", active ? "true" : "false");
      button.setAttribute("aria-pressed", String(active));
    });
    els.alphabetCurrent.textContent = state.browseStart
      ? `A mostrar entradas começadas por ${state.browseStart}.`
      : "A mostrar entradas de todas as letras.";
    els.alphabetAll.disabled = !state.browseStart;
  }

  function fullLabel(value) {
    const match = String(value || "").match(/^[^(]+\s+\((.+)\)$/);
    return match ? match[1] : value;
  }

  function statusLabel(value) {
    const labels = {
      "": "Sem estado",
      imported: "Importada",
      draft: "Em preparação",
      new: "Nova",
      edited: "Editada",
      reviewed: "Revista editorialmente",
      revised: "Revisada",
      validated: "Validada",
      "needs revision": "Precisa de revisão",
      "Importação SPE": "Importação SPE",
    };
    return labels[value || ""] || value;
  }

  function workflowClass(value) {
    return {
      IMPORTED: "workflow-imported",
      EDITING: "workflow-editing",
      REVIEW: "workflow-review",
      VALIDATED: "workflow-validated",
      PUBLISHED: "workflow-published",
    }[String(value || "").toUpperCase()] || "workflow-imported";
  }

  function sourceLabel(entry) {
    const code = typeof entry === "string"
      ? entry
      : (entry.source_code || entry.collection_code);
    return code === "DLP" ? "Dicionário" :
      code === "VOCABULARIO" ? "Vocabulário" : code;
  }

  function renderLinkedText(segments, fallback) {
    if (!Array.isArray(segments) || !segments.length) return h(fallback);
    return segments.map((segment) => {
      if (!segment.query) return h(segment.text);
      const url = appUrl(`/?q=${encodeURIComponent(segment.query)}`);
      return `<a class="lexical-link" href="${url}" data-reference="${h(segment.query)}">${h(segment.text)}</a>`;
    }).join("");
  }

  function number(value) {
    if (value === "—") return value;
    return new Intl.NumberFormat("pt-PT").format(Number(value) || 0);
  }

  function h(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function highlight(value, query) {
    const escaped = h(value);
    if (!query) return escaped;
    const terms = query.trim().split(/\s+/).filter(Boolean).map(escapeRegExp);
    if (!terms.length) return escaped;
    return escaped.replace(new RegExp(`(${terms.join("|")})`, "giu"), "<mark>$1</mark>");
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
})();
