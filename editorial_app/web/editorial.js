const $ = (selector) => document.querySelector(selector);
const overview = $("#overview");
const entries = $("#entries");
const detail = $("#detail");
const statusNode = $("#status");
const toastNode = $("#toast");
let selectedId = null;
let publicationTimer = null;
let overviewData = null;
const editorialBasePath = (
  document.querySelector('meta[name="acl-editor-base-path"]')?.content || ""
).replace(/\/+$/, "");

function apiUrl(path) {
  const normalised = path.startsWith("/") ? path : `/${path}`;
  return `${editorialBasePath}${normalised}` || "/";
}

async function api(url, options = {}) {
  const response = await fetch(apiUrl(url), {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || payload.error || "Pedido inválido");
  return payload;
}

function actor() { return $("#current-user").value; }

function toast(message, error = false) {
  toastNode.textContent = message;
  toastNode.className = `show${error ? " error" : ""}`;
  window.setTimeout(() => { toastNode.className = ""; }, 5000);
}

document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".nav-link").forEach(item => item.classList.toggle("active", item === button));
  document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === button.dataset.view));
  if (button.dataset.view === "governance-panel") loadControlledValues();
  if (button.dataset.view === "publication-panel") loadPublication();
}));

async function loadOverview() {
  const data = await api("/api/editorial/overview");
  overviewData = data;
  const run = data.active_import;
  if (!run) { overview.textContent = "Não existe uma importação ativa."; return; }
  const workflow = Object.fromEntries(data.workflow.map(item => [item.value, item.count]));
  const issues = Object.fromEntries(data.validation_issues.map(item => [item.severity, item.count]));
  overview.textContent =
    `${n(run.entry_count)} entradas · ${n(run.error_count)} erros de importação · ` +
    `${n(workflow.EDITING || 0)} em edição · ${n(workflow.REVIEW || 0)} em revisão · ` +
    `${n(workflow.VALIDATED || 0)} validadas · ${n(issues.error || 0)} erros impeditivos`;
  populateUsers(data.users);
  const counts = data.filter_counts || {};
  populateCountedSelect("#resource-filter", counts.resource, "DLP e VOLP", run.entry_count, resourceLabel);
  populateCountedSelect("#workflow-filter", completeCounts(counts.workflow, ["IMPORTED", "EDITING", "REVIEW", "VALIDATED", "PUBLISHED"]), "Todos", run.entry_count);
  populateCountedSelect("#source-status-filter", counts.editorial_statuses, "Todos", run.entry_count);
  populateCountedSelect("#grammar-filter", counts.grammar, "Todas", run.entry_count);
  populateCountedSelect("#domain-filter", counts.domains, "Todos", run.entry_count);
  populateCountedSelect("#severity-filter", completeCounts(counts.severity, ["error", "warning", "info"]), "Todos", run.entry_count, severityLabel);
}

function populateUsers(users) {
  const select = $("#current-user");
  const current = localStorage.getItem("acl-editorial-user") || "editor.demo";
  select.replaceChildren(...users.map(user => option(
    user.username, `${user.display_name} — ${roleLabel(user.role)}`
  )));
  select.value = users.some(user => user.username === current) ? current : users[0]?.username || "";
  select.onchange = () => localStorage.setItem("acl-editorial-user", select.value);
}

function populateCountedSelect(selector, values = [], firstLabel, total, label = value => value) {
  const select = $(selector);
  const previous = select.value;
  select.replaceChildren(
    option("", `${firstLabel} (${n(total)})`),
    ...values.map(item => option(item.value, `${label(item.value)} (${n(item.count)})`)),
  );
  select.value = previous;
}

function completeCounts(items = [], values = []) {
  const counts = Object.fromEntries(items.map(item => [item.value, item.count]));
  return values.map(value => ({value, count: counts[value] || 0}));
}

function severityLabel(value) {
  return {error: "Erros impeditivos", warning: "Avisos", info: "Informação"}[value] || value;
}

function validationIssueDetails(issue) {
  if (!issue.details_json) return "";
  try {
    const details = JSON.parse(issue.details_json);
    if (Array.isArray(details.errors)) {
      return details.errors.map(error => {
        const location = [
          error.path ? `caminho ${error.path}` : "",
          error.line ? `linha ${error.line}` : "",
        ].filter(Boolean).join(", ");
        return `${error.description || error.message || "Erro estrutural"}${location ? ` (${location})` : ""}`;
      }).join("; ");
    }
    // Compatibilidade com validações anteriores, que guardavam somente
    // a representação textual do último erro do libxml2.
    if (details.last_error) {
      const message = String(details.last_error).replace(/^.*?RELAXNG_ERR_[A-Z]+:\s*/, "");
      return translateRngMessage(message);
    }
  } catch (_) {
    return String(issue.details_json);
  }
  return "";
}

function issueData(issue) {
  try { return JSON.parse(issue.details_json || "{}"); }
  catch (_) { return {}; }
}

function translateRngMessage(message) {
  let match = message.match(/^Invalid attribute (\S+) for element (\S+)$/);
  if (match) return `O atributo “${match[1]}” não é permitido no elemento <${match[2]}>`;
  match = message.match(/^Did not expect element (\S+) there$/);
  if (match) return `O elemento <${match[1]}> não é permitido nesta posição`;
  match = message.match(/^Expecting an element (\S+), got nothing$/);
  if (match) return `É obrigatório um elemento <${match[1]}>, mas não foi encontrado`;
  match = message.match(/^Expecting element (\S+), got (\S+)$/);
  if (match) return `Era esperado o elemento <${match[1]}>, mas foi encontrado <${match[2]}>`;
  return message;
}

$("#search-form").addEventListener("submit", event => { event.preventDefault(); loadEntries(); });
document.querySelectorAll("#search-form select").forEach(select => select.addEventListener("change", loadEntries));
$("#clear-filters").addEventListener("click", () => {
  $("#query").value = "";
  document.querySelectorAll("#search-form select").forEach(select => { select.value = ""; });
  loadEntries();
});

async function loadEntries() {
  statusNode.textContent = "A pesquisar…";
  const params = new URLSearchParams({q: $("#query").value, limit: "100"});
  const mappings = {
    "#resource-filter": "resource", "#workflow-filter": "workflow",
    "#source-status-filter": "editorial_status", "#grammar-filter": "grammar",
    "#domain-filter": "domain", "#severity-filter": "severity",
  };
  for (const [selector, key] of Object.entries(mappings)) if ($(selector).value) params.set(key, $(selector).value);
  try {
    const payload = await api(`/api/editorial/entries?${params}`);
    statusNode.textContent = `${n(payload.total)} entradas encontradas · primeiras ${n(payload.items.length)} apresentadas`;
    entries.replaceChildren(...payload.items.map(entryButton));
  } catch (error) { statusNode.textContent = error.message; }
}

function entryButton(item) {
  const button = element("button", `entry${item.public_id === selectedId ? " active" : ""}`);
  button.type = "button";
  const warning = item.error_count ? ` · ${item.error_count} erros` : item.waived_error_count ? ` · ${item.waived_error_count} erros dispensados` : item.warning_count ? ` · ${item.warning_count} avisos` : "";
  button.innerHTML = `<strong>${h(item.lemma || "(sem lema)")}</strong><small>${h(resourceLabel(item.resource))} · ${h(item.workflow_status)}${h(warning)} · ${h(item.public_id)}</small>`;
  button.addEventListener("click", () => showEntry(item.public_id));
  return button;
}

async function showEntry(id) {
  try {
    selectedId = id;
    renderEntry(await api(`/api/editorial/entries/${encodeURIComponent(id)}`));
  } catch (error) { toast(error.message, true); }
}

function renderEntry(item) {
  detail.replaceChildren();
  const heading = element("div", "entry-heading");
  heading.innerHTML = `<div><h2>${h(item.lemma || "(sem lema)")}</h2><p class="meta">${h(item.public_id)} · atualizado em ${h(formatDate(item.updated_at))}</p></div><div><span class="badge">${h(resourceLabel(item.resource))}</span><span class="badge workflow">${h(item.workflow_status)}</span></div>`;

  const form = element("form", "edit-form");
  const grid = element("div", "grid");
  grid.append(field("Lema", "lemma", item.lemma, true), field("Classe gramatical", "grammar", item.grammatical_info || ""), field("Estado da fonte", "source-status", item.editorial_status || ""));
  form.append(grid, sectionTitle(`Formas e variantes (${item.forms.length})`));
  const forms = editableRows(item.forms, "form", row => row.value, "Forma");
  form.append(forms, sectionTitle(`Aceções e definições (${item.senses.length + item.enrichments.length})`));
  for (const sense of item.senses) {
    const box = element("section", "sense");
    box.innerHTML = `<header><span>Aceção ${h(sense.number_label || sense.position_path)}</span><span>${h(sense.public_id || "")}</span></header>`;
    const textarea = document.createElement("textarea"); textarea.dataset.senseId = sense.id; textarea.value = sense.definition || sense.gloss || "";
    box.append(textarea); form.append(box);
  }
  for (const enrichment of item.enrichments) {
    const box = element("section", "sense readonly");
    box.innerHTML = `<header><span>Enriquecimento externo</span><span>${h(enrichment.source_code)}</span></header><p>${h(enrichment.definition)}</p>`;
    form.append(box);
  }
  form.append(sectionTitle(`Marcas e domínios (${item.labels.length})`), editableRows(item.labels, "label", row => row.value, "Valor"));
  form.append(sectionTitle(`Relações e remissões (${item.relations.length})`), relationRows(item.relations));
  form.append(field("Nota da revisão", "comment", "", false, true));
  const actions = element("div", "actions"); actions.append(button("Gravar alterações"), button("Recarregar", "secondary", "button"));
  actions.lastChild.addEventListener("click", () => showEntry(item.public_id)); form.append(actions);
  form.addEventListener("submit", event => saveEntry(event, item));

  const workflow = element("div", "workflow-actions"); workflow.append(elementText("strong", "Workflow:"));
  for (const target of workflowTargets(item.workflow_status)) {
    const control = button(workflowLabel(target), "secondary", "button");
    control.addEventListener("click", () => changeWorkflow(item.public_id, target)); workflow.append(control);
  }
  const issues = element("div", "issues");
  for (const issue of item.validation_issues) {
    const row = element("div", "validation-issue");
    row.append(elementText("strong", `${issue.severity.toUpperCase()} · ${issue.rule_code}: `));
    row.append(document.createTextNode(issue.message));
    const technical = validationIssueDetails(issue);
    if (technical && !issue.message.includes(technical)) {
      row.append(elementText("small", technical));
    }
    if (issue.waiver) {
      row.classList.add("is-waived");
      row.append(elementText("small", `Erro dispensado por ${issue.waiver.actor}: ${issue.waiver.reason}`));
    } else {
      const suggestion = (issueData(issue).suggestions || [])[0];
      if (suggestion) {
        const fix = button(suggestion.action || "Aplicar correção sugerida", "secondary compact", "button");
        fix.addEventListener("click", () => applyIssueFix(item.public_id, issue.rule_code, suggestion.code));
        row.append(fix);
      }
      if (issue.severity === "error") {
        const waive = button("Dispensar com justificação", "secondary compact", "button");
        waive.addEventListener("click", () => waiveIssue(item.public_id, issue.rule_code));
        row.append(waive);
      }
    }
    issues.append(row);
  }

  const diagnostics = document.createElement("details"); diagnostics.innerHTML = "<summary>Identificação e metadados técnicos</summary>";
  const diagnosticGrid = element("div", "diagnostic-grid");
  const projection = {...item}; delete projection.raw_xml; delete projection.revisions;
  const jsonPre = document.createElement("pre"); jsonPre.textContent = JSON.stringify(projection, null, 2);
  const xmlPre = document.createElement("pre"); xmlPre.textContent = item.raw_xml;
  diagnosticGrid.append(jsonPre, xmlPre); diagnostics.append(diagnosticGrid);

  const history = document.createElement("details"); history.innerHTML = `<summary>Histórico e auditoria (${item.revisions.length + item.audit_events.length})</summary>`;
  const list = element("ol", "revisions");
  for (const revision of item.revisions) {
    const row = document.createElement("li");
    row.append(document.createTextNode(`#${revision.revision_no} · ${revision.actor || "sem autor"} · ${revision.comment || "sem nota"} · ${formatDate(revision.created_at)} `));
    const restore = button("Repor", "secondary compact", "button"); restore.addEventListener("click", () => restoreRevision(item.public_id, revision.revision_no)); row.append(restore); list.append(row);
  }
  for (const event of item.audit_events) list.append(elementText("li", `${event.event_type} · ${event.actor} · ${event.previous_state || "—"} → ${event.resulting_state || "—"} · ${formatDate(event.created_at)}`));
  history.append(list);
  detail.append(heading, form, workflow, issues, diagnostics, history);
}

function editableRows(items, prefix, valueFn, label) {
  const wrap = element("div", "editable-list");
  for (const item of items) {
    const row = element("label", "inline-field"); row.append(document.createTextNode(`${item.kind || item.label_type || label} `));
    const input = document.createElement("input"); input.dataset[`${prefix}Id`] = item.id; input.value = valueFn(item); row.append(input); wrap.append(row);
  }
  if (!items.length) wrap.append(elementText("p", "Sem valores nesta entrada.", "muted"));
  return wrap;
}

function relationRows(items) {
  const wrap = element("div", "editable-list");
  for (const item of items) {
    const row = element("div", "relation-row");
    const text = document.createElement("input"); text.dataset.relationText = item.id; text.value = item.target_text || ""; text.placeholder = "Texto";
    const target = document.createElement("input"); target.dataset.relationTarget = item.id; target.value = item.target_id || ""; target.placeholder = "Identificador ou URL";
    row.append(text, target); wrap.append(row);
  }
  if (!items.length) wrap.append(elementText("p", "Sem remissões nesta entrada.", "muted"));
  return wrap;
}

async function saveEntry(event, item) {
  event.preventDefault(); const submit = event.submitter; submit.disabled = true;
  try {
    const updated = await api(`/api/editorial/entries/${encodeURIComponent(item.public_id)}`, {method: "PATCH", body: JSON.stringify({
      expected_updated_at: item.updated_at, actor: actor(), comment: $("#comment").value,
      lemma: $("#lemma").value, grammatical_info: $("#grammar").value, editorial_status: $("#source-status").value,
      forms: [...document.querySelectorAll("[data-form-id]")].map(node => ({id: Number(node.dataset.formId), value: node.value})),
      senses: [...document.querySelectorAll("[data-sense-id]")].map(node => ({id: Number(node.dataset.senseId), definition: node.value})),
      labels: [...document.querySelectorAll("[data-label-id]")].map(node => ({id: Number(node.dataset.labelId), value: node.value})),
      relations: [...document.querySelectorAll("[data-relation-text]")].map(node => ({id: Number(node.dataset.relationText), target_text: node.value, target_id: document.querySelector(`[data-relation-target="${node.dataset.relationText}"]`).value})),
    })});
    renderEntry(updated); toast("Alterações gravadas e revisão criada."); await Promise.all([loadOverview(), loadEntries()]);
  } catch (error) { toast(error.message, true); } finally { submit.disabled = false; }
}

async function changeWorkflow(id, target) {
  try {
    const updated = await api(`/api/editorial/entries/${encodeURIComponent(id)}/workflow`, {method: "POST", body: JSON.stringify({target, actor: actor(), comment: `Transição para ${target}`})});
    renderEntry(updated); toast(`Entrada movida para ${workflowLabel(target)}.`); await Promise.all([loadOverview(), loadEntries()]);
  } catch (error) { toast(error.message, true); }
}

async function restoreRevision(id, revisionNo) {
  if (!confirm(`Repor a revisão ${revisionNo}? O estado passará para Em edição.`)) return;
  try {
    const item = await api(`/api/editorial/entries/${encodeURIComponent(id)}/revisions/${revisionNo}/restore`, {method: "POST", body: JSON.stringify({actor: actor(), comment: `Reposição da revisão ${revisionNo}`})});
    renderEntry(item); toast("Revisão reposta; o estado passou para Em edição.");
  } catch (error) { toast(error.message, true); }
}

async function applyIssueFix(id, ruleCode, fixCode) {
  if (!confirm("Aplicar a correção sugerida? Será criada uma revisão e a entrada passará para Em edição.")) return;
  try {
    const item = await api(`/api/editorial/entries/${encodeURIComponent(id)}/issues/${encodeURIComponent(ruleCode)}/fix`, {
      method: "POST",
      body: JSON.stringify({actor: actor(), fix_code: fixCode, comment: `Correção assistida de ${ruleCode}`}),
    });
    renderEntry(item); toast("Correção aplicada e revisão criada. Valide novamente os dados.");
    await Promise.all([loadOverview(), loadEntries()]);
  } catch (error) { toast(error.message, true); }
}

async function waiveIssue(id, ruleCode) {
  const reason = prompt("Justificação obrigatória para dispensar este erro (fica registada na auditoria):");
  if (reason === null) return;
  try {
    const item = await api(`/api/editorial/entries/${encodeURIComponent(id)}/issues/${encodeURIComponent(ruleCode)}/waive`, {
      method: "POST", body: JSON.stringify({actor: actor(), reason}),
    });
    renderEntry(item); toast("Erro dispensado para esta versão exata da entrada.");
    await Promise.all([loadOverview(), loadEntries(), loadPublication()]);
  } catch (error) { toast(error.message, true); }
}

$("#controlled-category").addEventListener("change", loadControlledValues);
$("#controlled-status").addEventListener("change", loadControlledValues);
async function loadControlledValues() {
  const params = new URLSearchParams({category: $("#controlled-category").value});
  if ($("#controlled-status").value) params.set("status", $("#controlled-status").value);
  try {
    const data = await api(`/api/editorial/controlled-values?${params}`);
    $("#controlled-values").innerHTML = `<table><thead><tr><th>Valor</th><th>Uso</th><th>Descrição</th><th>Estado</th><th>Substituição</th><th></th></tr></thead><tbody>${data.items.map(item => `<tr><td>${h(item.value)}</td><td>${n(item.usage_count)}</td><td><input data-cv-label="${item.id}" value="${h(item.display_label || "")}"></td><td><select data-cv-status="${item.id}"><option value="authorized"${item.governance_status === "authorized" ? " selected" : ""}>Autorizado</option><option value="unmapped"${item.governance_status === "unmapped" ? " selected" : ""}>Por mapear</option><option value="obsolete"${item.governance_status === "obsolete" ? " selected" : ""}>Obsoleto</option></select></td><td><input data-cv-replacement="${item.id}" value="${h(item.replacement_value || "")}"></td><td><button class="compact" data-cv-save="${item.id}">Gravar</button></td></tr>`).join("")}</tbody></table>`;
    document.querySelectorAll("[data-cv-save]").forEach(button => button.addEventListener("click", () => saveControlledValue(Number(button.dataset.cvSave))));
  } catch (error) { $("#controlled-values").textContent = error.message; }
}

async function saveControlledValue(id) {
  try {
    await api(`/api/editorial/controlled-values/${id}`, {method: "PATCH", body: JSON.stringify({actor: actor(), governance_status: document.querySelector(`[data-cv-status="${id}"]`).value, display_label: document.querySelector(`[data-cv-label="${id}"]`).value, replacement_value: document.querySelector(`[data-cv-replacement="${id}"]`).value, comment: "Revisão da lista controlada"})});
    toast("Valor controlado atualizado."); loadControlledValues();
  } catch (error) { toast(error.message, true); }
}

$("#validate").addEventListener("click", async () => {
  $("#validate").disabled = true; $("#validation-status").textContent = "A validar o corpus…";
  try { const result = await api("/api/editorial/validate", {method: "POST", body: "{}"}); renderValidation(result); await loadOverview(); }
  catch (error) { toast(error.message, true); } finally { $("#validate").disabled = false; }
});

function renderValidation(result) {
  const waived = result.waived_errors ? ` · ${n(result.waived_errors)} erros dispensados` : "";
  $("#validation-status").textContent = `${result.valid ? "Válido para candidatura" : "Com erros bloqueantes"} · ${n(result.blocking_errors ?? result.errors)} erros bloqueantes${waived} · ${n(result.warnings)} avisos · esquema RNG ${result.schema_checked ? "verificado" : "por configurar"}`;
}

$("#prepare-release").addEventListener("click", async () => {
  try {
    await api("/api/editorial/releases/prepare", {method: "POST", body: JSON.stringify({actor: actor(), description: $("#release-description").value})});
    publicationTimer = setInterval(loadPublication, 1000); loadPublication();
  } catch (error) { toast(error.message, true); }
});

async function loadPublication() {
  try {
    const [job, validation] = await Promise.all([
      api("/api/editorial/publish/status"), api("/api/editorial/validation")
    ]);
    renderValidation(validation);
    $("#publication-status").textContent = job.message || "Nenhuma operação em curso.";
    $("#prepare-release").disabled = job.state === "running";
    renderReleases(job.releases || [], job.active_release);
    if (job.state !== "running" && publicationTimer) { clearInterval(publicationTimer); publicationTimer = null; if (job.state === "succeeded") toast(job.message); if (job.state === "failed") toast(job.message, true); }
  } catch (error) { $("#publication-status").textContent = error.message; }
}

function renderReleases(releases, active) {
  $("#releases").innerHTML = `<table><thead><tr><th>Versão</th><th>Estado</th><th>Responsáveis</th><th>Data</th><th>Ações</th></tr></thead><tbody>${releases.map(release => {
    const actions = [];
    if (release.state === "candidate") actions.push(`<button data-release-action="approve" data-release="${h(release.release_id)}">Aprovar</button>`);
    if (release.state === "approved") actions.push(`<button data-release-action="publish" data-release="${h(release.release_id)}">Publicar</button>`);
    if (release.state === "archived") actions.push(`<button class="secondary" data-release-action="rollback" data-release="${h(release.release_id)}">Reverter</button>`);
    const report = release.report ? `<details><summary>Relatório</summary><pre>${h(JSON.stringify(release.report, null, 2))}</pre></details>` : "";
    return `<tr><td><strong>${h(release.release_id)}</strong>${release.release_id === active ? "<br><span class=\"active-release\">Ativa</span>" : ""}<br><small>${h(release.description || "")}</small>${report}</td><td>${h(release.state)}</td><td>${h(release.prepared_by || "—")}<br>${h(release.approved_by || "—")}</td><td>${h(formatDate(release.created_at))}</td><td class="table-actions">${actions.join(" ")}</td></tr>`;
  }).join("")}</tbody></table>`;
  document.querySelectorAll("[data-release-action]").forEach(button => button.addEventListener("click", () => releaseAction(button.dataset.release, button.dataset.releaseAction)));
}

async function releaseAction(id, action) {
  const labels = {approve: "aprovar", publish: "publicar", rollback: "reverter para"};
  if (!confirm(`Confirma que pretende ${labels[action]} ${id}?`)) return;
  try {
    await api(`/api/editorial/releases/${encodeURIComponent(id)}/${action}`, {method: "POST", body: JSON.stringify({actor: actor(), comment: `${labels[action]} ${id}`})});
    if (action !== "approve") publicationTimer = setInterval(loadPublication, 1000);
    loadPublication();
  } catch (error) { toast(error.message, true); }
}

function workflowTargets(current) { return {IMPORTED: ["EDITING"], EDITING: ["REVIEW"], REVIEW: ["EDITING", "VALIDATED"], VALIDATED: ["EDITING"], PUBLISHED: ["EDITING"]}[current] || []; }
function workflowLabel(value) { return {EDITING: "Em edição", REVIEW: "Submeter para revisão", VALIDATED: "Validar"}[value] || value; }
function roleLabel(value) { return {editor: "editor", reviewer: "revisor", approver: "aprovador", administrator: "administrador"}[value] || value; }
function resourceLabel(value) { return value === "dictionary" ? "Dicionário" : "Vocabulário"; }
function formatDate(value) { if (!value) return "—"; const parsed = new Date(value.endsWith("Z") || value.includes("+") ? value : `${value}Z`); return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString("pt-PT"); }
function n(value) { return Number(value || 0).toLocaleString("pt-PT"); }
function h(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])); }
function element(tag, className = "") { const node = document.createElement(tag); node.className = className; return node; }
function elementText(tag, text, className = "") { const node = element(tag, className); node.textContent = text; return node; }
function option(value, label) { const node = document.createElement("option"); node.value = value; node.textContent = label; return node; }
function sectionTitle(text) { return elementText("h3", text); }
function button(text, className = "", type = "submit") { const node = element("button", className); node.type = type; node.textContent = text; return node; }
function field(labelText, id, value, required = false, wide = false) { const wrapper = element("div", `field${wide ? " field-wide" : ""}`); const label = document.createElement("label"); label.htmlFor = id; label.textContent = labelText; const input = document.createElement("input"); input.id = id; input.value = value; input.required = required; wrapper.append(label, input); return wrapper; }

Promise.all([loadOverview(), loadEntries(), loadPublication()]).catch(error => toast(error.message, true));
