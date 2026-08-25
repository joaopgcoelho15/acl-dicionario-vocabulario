const $ = selector => document.querySelector(selector);
const editorialBasePath = (document.querySelector('meta[name="acl-editor-base-path"]')?.content || "").replace(/\/+$/, "");
let overviewData = null;
let selectedId = null;
let selectedEntry = null;
let controlledValues = [];
let publicationItems = [];
let publicationTimer = null;
let entryOffset = 0;
let activeAlphabet = "";

function apiUrl(path) { const value = path.startsWith("/") ? path : `/${path}`; return `${editorialBasePath}${value}` || "/"; }
function publicUrl(path) { const root = editorialBasePath.replace(/\/editor$/, ""); return `${root}/${String(path).replace(/^\//, "")}`; }
async function api(path, options = {}) {
  const response = await fetch(apiUrl(path), {...options, headers: {"Content-Type": "application/json", ...(options.headers || {})}});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || payload.error || "Pedido inválido");
  return payload;
}
function actor() { return $("#current-user").value; }
function currentUser() { return overviewData?.users?.find(user => user.username === actor()); }
function n(value) { return Number(value || 0).toLocaleString("pt-PT"); }
function h(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])); }
function formatDate(value) { if (!value) return "—"; const parsed = new Date(value.endsWith("Z") || value.includes("+") ? value : `${value}Z`); return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString("pt-PT"); }
function resourceLabel(value) { return value === "dictionary" ? "Dicionário" : "Vocabulário"; }
function roleLabel(value) { return {editor:"Editor",reviewer:"Revisor",approver:"Aprovador",administrator:"Administrador"}[value] || value; }
function workflowLabel(value) { return {DRAFT:"Em preparação",EDITED:"Editada",REVIEWED:"Revista editorialmente",NEEDS_REVISION:"Precisa de revisão",VALIDATED:"Validada",PUBLISHED:"Publicada",REMOVED:"Apagada"}[value] || value; }
function workflowEntryLabel(item) { if (item?.workflow_status !== "DRAFT") return workflowLabel(item?.workflow_status); const origin={new:"nova",imported:"importada",recovered:"recuperada"}[item.workflow_origin]||item.workflow_origin; return `Em preparação${origin?` (${origin})`:""}`; }
function workflowClass(value) { return {DRAFT:"workflow-draft",EDITED:"workflow-edited",REVIEWED:"workflow-reviewed",NEEDS_REVISION:"workflow-needs-revision",VALIDATED:"workflow-validated",PUBLISHED:"workflow-published",REMOVED:"workflow-removed"}[value] || "workflow-draft"; }
function toast(message, error = false) { const node = $("#toast"); node.textContent = message; node.className = `show${error ? " error" : ""}`; setTimeout(() => { node.className = ""; }, 5000); }

document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".nav-link").forEach(item => item.classList.toggle("active", item === button));
  document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === button.dataset.view));
  if (button.dataset.view === "governance-panel") loadControlledValues();
  if (button.dataset.view === "persistence-panel") renderPersistence();
  if (button.dataset.view === "entries-panel") loadEntries();
  if (button.dataset.view === "publication-panel") loadPublication();
  if (button.dataset.view === "audit-panel") loadAudit();
}));

async function loadOverview() {
  overviewData = await api("/api/editorial/overview");
  const run = overviewData.active_import;
  if (!run) { $("#overview").textContent = "Não existe uma importação ativa."; return; }
  const workflow = Object.fromEntries(overviewData.workflow.map(item => [item.value, item.count]));
  const persistence = overviewData.persistence;
  $("#overview").classList.toggle("has-unsaved", Boolean(persistence.has_unsaved_changes));
  $("#overview").textContent = `${n(run.entry_count)} entradas · ${n(workflow.EDITED)} editadas · ${n(workflow.REVIEWED)} revistas · ${n(workflow.VALIDATED)} validadas · ${persistence.has_unsaved_changes ? "Existem alterações por guardar em TEI/XML" : "TEI/XML sincronizado"}`;
  populateUsers(overviewData.users);
  const counts = overviewData.filter_counts;
  renderEntryFacets(counts, run.entry_count, false);
  renderPersistence();
}
function populateUsers(users) {
  const select = $("#current-user");
  const saved = localStorage.getItem("acl-editorial-user") || "editor.demo";
  select.innerHTML = users.map(user => `<option value="${h(user.username)}">${h(user.display_name)} — ${h(roleLabel(user.role))}</option>`).join("");
  select.value = users.some(user => user.username === saved) ? saved : users[0]?.username || "";
  select.onchange = () => { localStorage.setItem("acl-editorial-user", select.value); updateRoleIndicator(); if (selectedEntry && !$("#edit-form")) renderPublicEntry(selectedEntry); if ($("#governance-panel").classList.contains("active")) loadControlledValues(); if ($("#publication-panel").classList.contains("active")) loadPublicationEntries(); };
  updateRoleIndicator();
}
function updateRoleIndicator() { const role = currentUser()?.role || ""; const node = $("#role-indicator"); node.textContent = roleLabel(role); node.className = `role-indicator role-${role}`; document.body.dataset.editorialRole = role; $("#add-controlled").disabled = !["reviewer","approver","administrator"].includes(role); $("#replace-corpus").disabled = !["approver","administrator"].includes(role); $("#publish-selected").disabled = !["approver","administrator"].includes(role); }
function controlledLabel(category, value) { const item = overviewData?.controlled_options?.[category]?.find(option => option.value === value); return item?.display_label ? `${value} (${item.display_label})` : value; }
function countedSelect(selector, currentValues = [], globalValues = [], first, currentTotal, globalTotal, label = value => value, contextual = true) {
  const select = $(selector); const previous = select.value;
  const current = Object.fromEntries(currentValues.map(item => [item.value,Number(item.count)||0]));
  const baseline = Object.fromEntries(globalValues.map(item => [item.value,Number(item.count)||0]));
  const countText = (value, global) => contextual ? `${n(value)}/${n(global)}` : n(global);
  const options = globalValues.map(item => { const value = item.value; const available = current[value] || 0; const disabled = contextual && available === 0 && value !== previous; return `<option value="${h(value)}"${value === previous ? " selected" : ""}${disabled ? " disabled" : ""}>${h(label(value))} [${countText(available,baseline[value])}]</option>`; }).join("");
  select.innerHTML = `<option value="">${h(first)} [${countText(currentTotal,globalTotal)}]</option>${options}`;
  if (previous && baseline[previous] !== undefined) select.value = previous;
}
function completeCounts(items = [], values) { const found = Object.fromEntries(items.map(item => [item.value,item.count])); return values.map(value => ({value,count:found[value] || 0})); }
function entryFiltersActive() { return Boolean($("#query").value.trim() || ["#resource-filter","#workflow-filter","#source-status-filter","#grammar-filter","#domain-filter","#severity-filter"].some(selector => $(selector).value)); }
function renderEntryFacets(current = {}, total = 0, contextual = entryFiltersActive()) {
  if (!overviewData?.active_import) return;
  const global = overviewData.filter_counts; const globalTotal = overviewData.active_import.entry_count;
  const workflowGlobal = completeCounts(global.workflow,["DRAFT","EDITED","REVIEWED","NEEDS_REVISION","VALIDATED","PUBLISHED","REMOVED"]);
  const workflowCurrent = completeCounts(current.workflow || [],["DRAFT","EDITED","REVIEWED","NEEDS_REVISION","VALIDATED","PUBLISHED","REMOVED"]);
  const severityGlobal = completeCounts(global.severity,["error","warning","info"]); const severityCurrent = completeCounts(current.severity || [],["error","warning","info"]);
  countedSelect("#resource-filter",current.resource || [],global.resource,"Dicionário e Vocabulário",total,globalTotal,resourceLabel,contextual);
  countedSelect("#workflow-filter",workflowCurrent,workflowGlobal,"Todos",total,globalTotal,workflowLabel,contextual);
  countedSelect("#source-status-filter",current.editorial_statuses || [],global.editorial_statuses,"Todos",total,globalTotal,value => controlledLabel("editorial_status",value),contextual);
  countedSelect("#grammar-filter",current.grammar || [],global.grammar,"Todas",total,globalTotal,value => controlledLabel("grammar",value),contextual);
  countedSelect("#domain-filter",current.domains || [],global.domains,"Todos",total,globalTotal,value => controlledLabel("domain",value),contextual);
  countedSelect("#severity-filter",severityCurrent,severityGlobal,"Todos",total,globalTotal,value => ({error:"Erros",warning:"Avisos",info:"Informação"}[value]),contextual);
}

function renderPersistence(result = null) {
  if (!overviewData?.persistence) return;
  const state = overviewData.persistence;
  $("#persistence-status").textContent = state.has_unsaved_changes ? "Existem alterações por guardar em TEI/XML." : "A base de dados está sincronizada com o último TEI/XML guardado.";
  $("#persistence-status").className = state.has_unsaved_changes ? "persistence-warning" : "persistence-ok";
  $("#last-saved").textContent = state.last_saved_at ? `Última salvaguarda: ${formatDate(state.last_saved_at)}${state.last_saved_path ? ` · ${state.last_saved_path.split("/").pop()}` : ""}` : "Ainda não foi guardado um TEI/XML por esta aplicação.";
  if (result) {
    $("#saved-downloads").innerHTML = `<a class="button-link" href="${apiUrl(`/api/editorial/exports/${encodeURIComponent(result.xml_name)}`)}" download>Descarregar TEI/XML</a><a class="button-link secondary" href="${apiUrl(`/api/editorial/exports/${encodeURIComponent(result.log_name)}`)}" download>Descarregar log</a>`;
  }
}

$("#save-canonical").addEventListener("click", async () => {
  const button = $("#save-canonical"); button.disabled = true;
  try { const result = await api("/api/editorial/save-canonical", {method:"POST",body:JSON.stringify({actor:actor()})}); await loadOverview(); renderPersistence(result); toast(`TEI/XML guardado com ${n(result.entries)} entradas.`); }
  catch(error){toast(error.message,true);} finally{button.disabled=false;}
});

async function importTei(mode) {
  const file = $("#tei-upload").files[0];
  if (!file) { toast("Selecione primeiro um ficheiro TEI/XML ou XML.XZ.", true); return; }
  if (mode === "replace") {
    if (!confirm("Esta operação substituirá todo o conjunto de trabalho se o ficheiro for válido. Pretende continuar?")) return;
    if (!confirm("Segunda confirmação: substituir o conjunto de trabalho atual?")) return;
  }
  const button = mode === "replace" ? $("#replace-corpus") : $("#import-batch"); button.disabled = true;
  $("#import-status").textContent = "A enviar e processar o ficheiro…";
  try {
    const response = await fetch(apiUrl("/api/editorial/import"), {method:"POST",headers:{"Content-Type":"application/octet-stream","X-ACL-Actor":actor(),"X-ACL-Import-Mode":mode,"X-ACL-Filename":file.name},body:file});
    const result = await response.json(); if (!response.ok) throw new Error(result.message || result.error || "A importação falhou.");
    $("#import-status").textContent = `${n(result.imported)} entradas importadas sem erros.`; $("#tei-upload").value = ""; await Promise.all([loadOverview(),loadControlledValues(),loadEntries()]);
  } catch(error) { $("#import-status").textContent = error.message; toast(error.message,true); }
  finally { updateRoleIndicator(); }
}
$("#import-batch").addEventListener("click",()=>importTei("batch"));
$("#replace-corpus").addEventListener("click",()=>importTei("replace"));

// Listas controladas
["#controlled-category","#controlled-sort","#controlled-direction"].forEach(selector => $(selector).addEventListener("change", loadControlledValues));
async function loadControlledValues() {
  const params = new URLSearchParams({category:$("#controlled-category").value,sort:$("#controlled-sort").value,direction:$("#controlled-direction").value});
  try {
    const data = await api(`/api/editorial/controlled-values?${params}`); controlledValues = data.items;
    const locked = ["reviewer","approver","administrator"].includes(currentUser()?.role) ? "" : " disabled";
    $("#controlled-values").innerHTML = `<table><thead><tr><th>Abreviatura / valor</th><th>Uso</th><th>Descrição completa</th><th>Ações</th></tr></thead><tbody>${controlledValues.map(item => `<tr data-cv-row="${item.id}"><td><input data-cv-value="${item.id}" value="${h(item.value)}"${locked}></td><td><strong>${n(item.usage_count)}</strong></td><td><input data-cv-label="${item.id}" value="${h(item.display_label || "")}"${locked}></td><td><div class="table-actions"><button class="compact" data-cv-save="${item.id}"${locked}>Gravar</button><button class="danger compact" data-cv-delete="${item.id}"${locked}>Apagar</button></div></td></tr>`).join("")}</tbody></table>`;
    document.querySelectorAll("[data-cv-save]").forEach(node => node.addEventListener("click", () => saveControlledValue(Number(node.dataset.cvSave))));
    document.querySelectorAll("[data-cv-delete]").forEach(node => node.addEventListener("click", () => deleteControlledValue(Number(node.dataset.cvDelete))));
  } catch (error) { $("#controlled-values").textContent = error.message; }
}
async function saveControlledValue(id) {
  const original = controlledValues.find(item => item.id === id); const value = document.querySelector(`[data-cv-value="${id}"]`).value.trim();
  const duplicate = controlledValues.find(item => item.id !== id && item.value.localeCompare(value, "pt", {sensitivity:"base"}) === 0);
  if (duplicate) {
    if (!confirm(`“${value}” já existe. Pretende unir “${original.value}” a esse valor? Os ${n(original.usage_count)} usos serão atualizados.`)) { await loadControlledValues(); toast("Merge recusado; a alteração foi anulada."); return; }
    try { await api("/api/editorial/controlled-values/merge", {method:"POST",body:JSON.stringify({source_id:id,target_id:duplicate.id,actor:actor(),comment:`Merge de ${original.value} em ${duplicate.value}`})}); toast("Valores unidos e entradas atualizadas."); await Promise.all([loadControlledValues(),loadOverview()]); } catch (error) { toast(error.message,true); }
    return;
  }
  try { await api(`/api/editorial/controlled-values/${id}`, {method:"PATCH",body:JSON.stringify({actor:actor(),value,display_label:document.querySelector(`[data-cv-label="${id}"]`).value,governance_status:original.governance_status,replacement_value:original.replacement_value || "",comment:"Revisão da lista controlada"})}); toast("Valor atualizado."); await Promise.all([loadControlledValues(),loadOverview()]); } catch (error) { toast(error.message,true); await loadControlledValues(); }
}
$("#add-controlled").addEventListener("click", async () => { const value = prompt("Novo valor ou abreviatura:"); if (!value?.trim()) return; const label = prompt("Descrição completa:") || ""; try { await api("/api/editorial/controlled-values", {method:"POST",body:JSON.stringify({actor:actor(),category:$("#controlled-category").value,value:value.trim(),display_label:label})}); toast("Nova opção adicionada."); loadControlledValues(); } catch (error) { toast(error.message,true); } });
async function deleteControlledValue(id) { const item = controlledValues.find(value => value.id === id); if (!confirm(`Apagar “${item.value}”? Só é possível se não estiver em uso.`)) return; try { await api(`/api/editorial/controlled-values/${id}`, {method:"DELETE",body:JSON.stringify({actor:actor(),comment:"Remoção de valor controlado"})}); toast("Valor apagado."); loadControlledValues(); } catch (error) { toast(error.message,true); } }

// Pesquisa, visualização pública e edição
$("#search-form").addEventListener("submit", event => { event.preventDefault(); setActiveAlphabet(""); loadEntries(); });
document.querySelectorAll("#entries-panel select").forEach(select => select.addEventListener("change", () => loadEntries()));
$("#clear-filters").addEventListener("click", () => { $("#query").value = ""; setActiveAlphabet(""); document.querySelectorAll("#entries-panel select").forEach(select => { select.value = ""; }); loadEntries(); });
async function loadEntries(append = false) {
  if (!append) entryOffset = 0;
  $("#status").textContent = "A pesquisar…"; const params = new URLSearchParams({q:$("#query").value,limit:"100",offset:String(entryOffset)});
  const filters = {"#resource-filter":"resource","#workflow-filter":"workflow","#source-status-filter":"editorial_status","#grammar-filter":"grammar","#domain-filter":"domain","#severity-filter":"severity"};
  Object.entries(filters).forEach(([selector,key]) => { if ($(selector).value) params.set(key,$(selector).value); });
  try { const payload = await api(`/api/editorial/entries?${params}`); const markup = payload.items.map(item => `<button class="result-card ${workflowClass(item.workflow_status)}${item.public_id === selectedId ? " is-selected" : ""}" data-entry-id="${h(item.public_id)}"><span class="result-card__top"><h3>${h(item.lemma || "(sem lema)")}</h3><span class="result-card__source">${h(resourceLabel(item.resource))}</span></span><p class="result-card__grammar">${h(item.grammatical_info || "sem classificação")}</p><small>${h(workflowEntryLabel(item))}${item.error_count ? ` · ${n(item.error_count)} erros` : item.warning_count ? ` · ${n(item.warning_count)} avisos` : ""}</small></button>`).join(""); if (append) $("#entries").insertAdjacentHTML("beforeend",markup); else $("#entries").innerHTML = markup || `<div class="entry-empty"><p>Sem resultados.</p></div>`; entryOffset += payload.items.length; $("#status").textContent = `${n(payload.total)} resultados`; $("#load-more-editorial").hidden = entryOffset >= payload.total; renderEntryFacets(payload.facets,payload.total); document.querySelectorAll("[data-entry-id]:not([data-bound])").forEach(node => { node.dataset.bound = "true"; node.addEventListener("click", () => showEntry(node.dataset.entryId)); }); if (!append && payload.items.length) await showEntry(payload.items[0].public_id); else if (!append) { selectedId=null; selectedEntry=null; $("#detail").innerHTML=`<div class="entry-empty"><h2>Sem resultados</h2><p>Remova alguns filtros ou altere a pesquisa.</p></div>`; } } catch (error) { $("#status").textContent = error.message; }
}
$("#load-more-editorial").addEventListener("click", () => loadEntries(true));
$("#editorial-alphabet").innerHTML = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").map(letter => `<button type="button" data-letter="${letter.toLowerCase()}" aria-pressed="false" title="Mostrar entradas começadas por ${letter}">${letter}</button>`).join("");
function setActiveAlphabet(letter) { activeAlphabet = letter; document.querySelectorAll("[data-letter]").forEach(node => { const active = node.dataset.letter === letter; node.classList.toggle("is-active",active); node.setAttribute("aria-pressed",String(active)); }); $("#alphabet-current").textContent = letter ? `A mostrar entradas começadas por ${letter.toUpperCase()}.` : "A mostrar entradas de todas as letras."; $("#alphabet-all").disabled = !letter; }
document.querySelectorAll("[data-letter]").forEach(node => node.addEventListener("click", () => { $("#query").value = node.dataset.letter; setActiveAlphabet(node.dataset.letter); loadEntries(); }));
$("#alphabet-all").addEventListener("click", () => { $("#query").value = ""; setActiveAlphabet(""); loadEntries(); });
setActiveAlphabet("");
async function showEntry(id) { try { selectedId = id; selectedEntry = await api(`/api/editorial/entries/${encodeURIComponent(id)}`); renderPublicEntry(selectedEntry); document.querySelectorAll("[data-entry-id]").forEach(node => node.classList.toggle("is-selected",node.dataset.entryId===id)); } catch (error) { toast(error.message,true); } }
function renderPublicEntry(item) {
  const entry = item.public_view; const lexical = entry.lexical || {}; let previousSection = null; let index = 0;
  const imageMarkup = images => (images || []).length ? `<div class="entry-images">${images.map(image => `<figure><img src="${publicUrl(`entry-images/${encodeURIComponent(image.url || image.filename || image)}`)}" alt="${h(image.caption || entry.lemma || "Imagem da entrada")}" loading="lazy">${image.caption ? `<figcaption>${h(image.caption)}</figcaption>` : ""}</figure>`).join("")}</div>` : "";
  const senses = (lexical.senses || []).map(sense => {
    index++; let heading = "";
    if (sense.section && sense.section !== previousSection) { heading = `<h4 class="related-heading">${h(sense.section)}</h4>`; previousSection = sense.section; }
    const labels = (sense.labels || []).map(label => `<span class="usage-label">${h(label.label || label.value)}</span>`).join("");
    const examples = (sense.examples || []).map(example => `<blockquote class="example">“${h(example.quote)}”${example.source ? ` <cite>— ${h(example.source)}</cite>` : ""}</blockquote>`).join("");
    const notes = (sense.notes || []).map(note => `<aside class="sense__note">${h(note.value)}</aside>`).join("");
    const references = (sense.references || []).map(ref => `<a href="${publicUrl(`?q=${encodeURIComponent(ref.value)}`)}">${h(ref.value)}</a>`).join(" · ");
    return `${heading}<section class="sense sense--depth-${Math.min(Number(sense.depth)||1,3)}"><span class="sense__number">${h(sense.number || index)}.</span><div>${labels ? `<div class="sense__labels">${labels}</div>` : ""}<p class="sense__definition">${h(sense.definition || "[definição em preparação]")}</p>${examples}${imageMarkup(sense.images)}${notes}${references ? `<div class="reference-list">Ver também: ${references}</div>` : ""}</div></section>`;
  }).join("");
  const glosses = (lexical.gloss_items || []).map(gloss => `<p>${h(gloss.value)}</p>`).join("");
  const variants = (lexical.orthographies || []).map(form => form.value).filter(value => value && value !== entry.lemma);
  const references = (lexical.references || []).map(ref => `<a href="${publicUrl(`?q=${encodeURIComponent(ref.value)}`)}">${h(ref.value)}</a>`).join(" · ");
  const notes = (lexical.notes || []).map(note => `<p class="entry-note">${h(note.value)}</p>`).join("");
  const etymology = (lexical.etymologies || []).map(value => `<div class="etymology">${h(value)}</div>`).join("");
  $("#detail").innerHTML = `<div class="entry-toolbar ${workflowClass(item.workflow_status)}"><div class="entry-toolbar__state"><span class="workflow-pill">${h(workflowLabel(item.workflow_status))}</span><span>${h(item.public_id)}</span></div><button id="edit-entry" type="button">Editar</button></div><header class="entry-header ${workflowClass(item.workflow_status)}"><div class="entry-header__meta"><span class="source-badge">${h(resourceLabel(item.resource))}</span><span class="status-badge">${h(entry.source_status_label || entry.source_status || "Sem estado")}</span></div><div class="entry-title-row"><h2>${h(entry.lemma || "(sem lema)")}</h2><span class="entry-grammar">${h(entry.grammatical_label || entry.grammatical_info || "")}</span></div>${variants.length ? `<p class="entry-variants"><strong>Outras formas:</strong> ${h(variants.join(" · "))}</p>` : ""}</header>${glosses ? `<section class="entry-section entry-gloss"><h3 class="entry-section__title">Enquadramento da entrada</h3>${glosses}</section>` : ""}${imageMarkup(lexical.images)}<section class="entry-section"><h3 class="entry-section__title">${senses ? `Aceções e definições <span>(${lexical.senses.length})</span>` : "Informação ortográfica"}</h3>${senses || "<p>Esta entrada não contém aceções lexicográficas.</p>"}</section>${references ? `<section class="entry-section"><h3 class="entry-section__title">Relações e remissões</h3><div class="reference-list">Ver também: ${references}</div></section>` : ""}${notes ? `<section class="entry-section"><h3 class="entry-section__title">Notas</h3>${notes}</section>` : ""}${etymology ? `<section class="entry-section"><h3 class="entry-section__title">Etimologia</h3>${etymology}</section>` : ""}${renderWorkflow(item)}${renderIssues(item)}${renderDiagnostics(item)}${renderHistory(item)}`;
  $("#detail .entry-toolbar").after($("#detail .workflow-lifecycle"));
  const editBlocked = ["REMOVED","PUBLISHED"].includes(item.workflow_status); $("#edit-entry").disabled = editBlocked; $("#edit-entry").title = item.workflow_status === "REMOVED" ? "Recupere a entrada antes de a editar" : item.workflow_status === "PUBLISHED" ? "Devolva a entrada para revisão na área Publicação" : ""; $("#edit-entry").addEventListener("click", () => renderEditEntry(item)); bindEntryActions(item);
}
function renderEditEntry(item) {
  const grammarOptions = optionList(overviewData.controlled_options.grammar,item.grammatical_info); const statusOptions = optionList(overviewData.controlled_options.editorial_status,item.editorial_status);
  $("#detail").innerHTML = `<div class="entry-toolbar"><div class="entry-toolbar__state"><span class="workflow-pill">${h(workflowLabel(item.workflow_status))}</span><strong>Modo de edição</strong></div><button id="cancel-edit" class="secondary" type="button">Cancelar</button></div><form id="edit-form" class="edit-form"><div class="edit-grid"><label>Lema<input id="lemma" value="${h(item.lemma)}" required></label><label>Classe gramatical<select id="grammar">${grammarOptions}</select></label><label>Estado da fonte<select id="source-status">${statusOptions}</select></label></div><h3>Formas e variantes (${item.forms.length})</h3><div class="editable-list">${item.forms.map(form => `<label class="inline-field">${h(form.kind || "Forma")}<input data-form-id="${form.id}" value="${h(form.value)}"></label>`).join("") || "<p>Sem formas.</p>"}</div><h3>Aceções e definições (${item.senses.length})</h3>${item.senses.map(sense => `<section class="sense-editor"><strong>Aceção ${h(sense.number_label || sense.position_path)}</strong><textarea data-sense-id="${sense.id}">${h(sense.definition || sense.gloss || "")}</textarea></section>`).join("")}<h3>Marcas e domínios (${item.labels.length})</h3><div class="editable-list">${item.labels.map(label => `<label class="inline-field">${h(label.label_type)}<input data-label-id="${label.id}" value="${h(label.value)}"></label>`).join("") || "<p>Sem valores.</p>"}</div><h3>Relações e remissões (${item.relations.length})</h3><div class="editable-list">${item.relations.map(relation => `<div class="inline-field"><input data-relation-text="${relation.id}" value="${h(relation.target_text || "")}"><input data-relation-target="${relation.id}" value="${h(relation.target_id || "")}" placeholder="Identificador"></div>`).join("") || "<p>Sem remissões.</p>"}</div><label>Nota da revisão<textarea id="comment"></textarea></label><div class="actions"><button type="submit">Gravar alterações</button><button id="reload-entry" class="secondary" type="button">Recarregar</button></div></form>${renderIssues(item)}${renderDiagnostics(item)}${renderHistory(item)}`;
  $("#detail .entry-toolbar").classList.add(workflowClass(item.workflow_status));
  $("#detail .entry-toolbar").insertAdjacentHTML("afterend", renderWorkflow(item));
  $("#cancel-edit").addEventListener("click", () => renderPublicEntry(item)); $("#reload-entry").addEventListener("click", () => showEntry(item.public_id)); $("#edit-form").addEventListener("submit", event => saveEntry(event,item)); bindEntryActions(item);
}
function optionList(items, selected) { const values = [...items.map(item => item.value)]; if (selected && !values.includes(selected)) values.unshift(selected); return `<option value="">Sem valor</option>` + values.map(value => `<option value="${h(value)}"${value === selected ? " selected" : ""}>${h(value)}</option>`).join(""); }
function workflowTargets(status) { const role=currentUser()?.role === "administrator" ? "approver" : currentUser()?.role; const maps={editor:{DRAFT:["EDITED","REMOVED"],NEEDS_REVISION:["EDITED"],REMOVED:["DRAFT"]},reviewer:{DRAFT:["EDITED","REVIEWED","REMOVED"],EDITED:["NEEDS_REVISION","REVIEWED","REMOVED"],NEEDS_REVISION:["EDITED","REVIEWED"],REMOVED:["DRAFT"]},approver:{DRAFT:["EDITED","REVIEWED","VALIDATED","REMOVED"],EDITED:["NEEDS_REVISION","REVIEWED","VALIDATED","REMOVED"],REVIEWED:["NEEDS_REVISION","VALIDATED","REMOVED"],NEEDS_REVISION:["EDITED","REVIEWED","VALIDATED","REMOVED"],VALIDATED:["NEEDS_REVISION","REMOVED"],REMOVED:["DRAFT"]}}; return maps[role]?.[status] || []; }
function workflowActionLabel(target) { return {DRAFT:"Recuperar entrada",EDITED:"Marcar como editada",REVIEWED:"Marcar como revista",NEEDS_REVISION:"Pedir revisão",VALIDATED:"Validar entrada",REMOVED:"Apagar entrada"}[target] || workflowLabel(target); }
function renderWorkflow(item) {
  const targets = new Set(workflowTargets(item.workflow_status));
  const primary = ["DRAFT","EDITED","REVIEWED","VALIDATED","PUBLISHED"];
  const alternatives = ["NEEDS_REVISION","REMOVED"];
  const role = currentUser()?.role || "";
  const stateButton = (state, alternative = false) => {
    const current = item.workflow_status === state;
    const available = targets.has(state);
    const label = current && state === "DRAFT" ? workflowEntryLabel(item) : workflowLabel(state);
    const classes = ["workflow-step",workflowClass(state),current?"is-current":"",available?"is-available":"",alternative?"is-alternative":""].filter(Boolean).join(" ");
    const title = current ? `Estado atual: ${label}` : available ? workflowActionLabel(state) : `O perfil ${roleLabel(role)} não pode mudar diretamente para este estado`;
    return `<button type="button" class="${classes}"${current?' aria-current="step"':""}${available?` data-workflow="${state}"`:" disabled"} title="${h(title)}"><span class="workflow-step__mark" aria-hidden="true"></span><span>${h(label)}</span>${current?'<small>Estado atual</small>':available?'<small>Clique para mudar</small>':""}</button>`;
  };
  const availableCount = targets.size;
  const publishedNote = item.workflow_status === "PUBLISHED" ? '<p class="workflow-lifecycle__note">As alterações de uma entrada publicada são iniciadas na área <strong>Publicação</strong>.</p>' : "";
  return `<section class="workflow-lifecycle" aria-label="Ciclo de vida desta entrada"><div class="workflow-lifecycle__heading"><div><span class="eyebrow">Ciclo de vida desta entrada</span><strong>${h(workflowEntryLabel(item))}</strong></div><span>Perfil ativo: <strong>${h(roleLabel(role))}</strong></span></div><div class="workflow-track">${primary.map(state=>stateButton(state)).join("")}</div><div class="workflow-alternatives"><span>Ações alternativas</span>${alternatives.map(state=>stateButton(state,true)).join("")}</div>${!availableCount&&!publishedNote?'<p class="workflow-lifecycle__note">Este perfil não tem transições disponíveis para o estado atual.</p>':publishedNote}</section>`;
}
function renderIssues(item) { return `<div class="issues">${item.validation_issues.map(issue => `<div class="validation-issue"><strong>${h(issue.severity.toUpperCase())} · ${h(issue.rule_code)}</strong><p>${h(issue.message)}</p>${issue.waiver ? `<small>Dispensado por ${h(issue.waiver.actor)}: ${h(issue.waiver.reason)}</small>` : issue.severity === "error" ? `<button class="secondary compact" type="button" data-waive="${h(issue.rule_code)}">Dispensar com justificação</button>` : ""}</div>`).join("")}</div>`; }
function renderDiagnostics(item) { const projection = {...item}; delete projection.raw_xml; delete projection.revisions; return `<details class="technical-details"><summary>Identificação e metadados técnicos</summary><div class="diagnostic-grid"><pre>${h(JSON.stringify(projection,null,2))}</pre><pre>${h(item.raw_xml)}</pre></div></details>`; }
function renderHistory(item) { return `<details class="history-details"><summary>Histórico e auditoria (${item.revisions.length + item.audit_events.length})</summary><ol class="revisions">${item.revisions.map(rev => `<li>#${rev.revision_no} · ${h(rev.actor || "sem autor")} · ${h(rev.comment || "sem nota")} <button class="secondary compact" data-restore="${rev.revision_no}">Repor</button></li>`).join("")}${item.audit_events.map(event => `<li>${h(event.event_type)} · ${h(event.actor)} · ${h(event.previous_state || "—")} → ${h(event.resulting_state || "—")}</li>`).join("")}</ol></details>`; }
function bindEntryActions(item) { document.querySelectorAll("[data-workflow]").forEach(node => node.addEventListener("click", () => changeWorkflow(item.public_id,node.dataset.workflow))); document.querySelectorAll("[data-restore]").forEach(node => node.addEventListener("click", () => restoreRevision(item.public_id,Number(node.dataset.restore)))); document.querySelectorAll("[data-waive]").forEach(node => node.addEventListener("click", () => waiveIssue(item.public_id,node.dataset.waive))); }
async function saveEntry(event,item) { event.preventDefault(); const submit = event.submitter; submit.disabled = true; try { const updated = await api(`/api/editorial/entries/${encodeURIComponent(item.public_id)}`,{method:"PATCH",body:JSON.stringify({expected_updated_at:item.updated_at,actor:actor(),comment:$("#comment").value,lemma:$("#lemma").value,grammatical_info:$("#grammar").value,editorial_status:$("#source-status").value,forms:[...document.querySelectorAll("[data-form-id]")].map(node => ({id:Number(node.dataset.formId),value:node.value})),senses:[...document.querySelectorAll("[data-sense-id]")].map(node => ({id:Number(node.dataset.senseId),definition:node.value})),labels:[...document.querySelectorAll("[data-label-id]")].map(node => ({id:Number(node.dataset.labelId),value:node.value})),relations:[...document.querySelectorAll("[data-relation-text]")].map(node => ({id:Number(node.dataset.relationText),target_text:node.value,target_id:document.querySelector(`[data-relation-target="${node.dataset.relationText}"]`).value}))})}); selectedEntry=updated; renderPublicEntry(updated); toast("Alterações gravadas; a entrada passou para Editada."); await Promise.all([loadOverview(),loadEntries()]); } catch(error){toast(error.message,true);} finally{submit.disabled=false;} }
async function changeWorkflow(id,target) { let confirmed=false; if(target==="REMOVED"){if(!confirm("A entrada será marcada como apagada e deixará de ser exportada e publicada. Pretende continuar?"))return;if(!confirm("Segunda confirmação: apagar esta entrada?"))return;confirmed=true;} try { const item=await api(`/api/editorial/entries/${encodeURIComponent(id)}/workflow`,{method:"POST",body:JSON.stringify({target,actor:actor(),confirmed,comment:`Transição para ${workflowLabel(target)}`})}); selectedEntry=item; renderPublicEntry(item); toast(`Entrada movida para ${workflowLabel(target)}.`); await Promise.all([loadOverview(),loadEntries(),loadPublicationEntries()]); } catch(error){toast(error.message,true);} }
async function restoreRevision(id,revision) { if(!confirm(`Repor a revisão ${revision}?`))return; try{const item=await api(`/api/editorial/entries/${encodeURIComponent(id)}/revisions/${revision}/restore`,{method:"POST",body:JSON.stringify({actor:actor(),comment:`Reposição da revisão ${revision}`})});renderPublicEntry(item);toast("Revisão reposta.");}catch(error){toast(error.message,true);} }
async function waiveIssue(id,rule) { const reason=prompt("Justificação obrigatória para dispensar este erro:");if(reason===null)return;try{const item=await api(`/api/editorial/entries/${encodeURIComponent(id)}/issues/${encodeURIComponent(rule)}/waive`,{method:"POST",body:JSON.stringify({actor:actor(),reason})});renderPublicEntry(item);toast("Erro dispensado para esta versão da entrada.");}catch(error){toast(error.message,true);} }

// Publicação seletiva
async function loadPublicationEntries(){try{const data=await api("/api/editorial/publication-entries?limit=500");publicationItems=data.items;const canSelect=["approver","administrator"].includes(currentUser()?.role);$("#select-visible").disabled=!canSelect;$("#unselect-visible").disabled=!canSelect;$("#publication-selection-summary").textContent=`${n(data.selected)} selecionadas de ${n(data.total)} operações prontas`;$("#publication-entries").innerHTML=data.items.map(item=>`<label class="publication-entry ${workflowClass(item.workflow_status)}"><input type="checkbox" data-publication-id="${h(item.public_id)}"${item.selected ? " checked" : ""}${canSelect ? "" : " disabled"}><span><strong>${h(item.lemma)}</strong><small>${h(resourceLabel(item.resource))} · ${h(workflowLabel(item.workflow_status))} · ${h(item.grammatical_info || "sem classe")}</small></span></label>`).join("")||"<p>Não existem entradas validadas nem remoções pendentes.</p>";document.querySelectorAll("[data-publication-id]").forEach(node=>node.addEventListener("change",()=>setPublicationSelection([node.dataset.publicationId],node.checked)));}catch(error){$("#publication-selection-summary").textContent=error.message;}}
async function setPublicationSelection(ids,selected){if(!ids.length){toast("Não existem entradas visíveis para alterar.");return;}try{await api("/api/editorial/publication-selection",{method:"POST",body:JSON.stringify({public_ids:ids,selected,actor:actor()})});await loadPublicationEntries();}catch(error){toast(error.message,true);await loadPublicationEntries();}}
$("#select-visible").addEventListener("click",()=>setPublicationSelection(publicationItems.map(item=>item.public_id),true));$("#unselect-visible").addEventListener("click",()=>setPublicationSelection(publicationItems.filter(item=>item.selected).map(item=>item.public_id),false));
$("#publish-selected").addEventListener("click",async()=>{if(!confirm("Publicar agora todas as entradas selecionadas na aplicação pública?"))return;const button=$("#publish-selected");button.disabled=true;try{await api("/api/editorial/publish-selected",{method:"POST",body:JSON.stringify({actor:actor(),description:$("#release-description").value})});publicationTimer=setInterval(loadPublication,1000);loadPublication();}catch(error){toast(error.message,true);updateRoleIndicator();}});
async function loadPublication(){try{const job=await api("/api/editorial/publish/status");$("#publication-status").textContent=job.message||"Nenhuma operação em curso.";$("#publish-selected").disabled=job.state==="running"||!["approver","administrator"].includes(currentUser()?.role);renderReleases(job.releases||[],job.active_release);await Promise.all([loadPublicationEntries(),loadPublishedEntries()]);if(job.state!=="running"&&publicationTimer){clearInterval(publicationTimer);publicationTimer=null;if(job.state==="succeeded"){toast(job.message);await Promise.all([loadOverview(),loadEntries()]);}if(job.state==="failed")toast(job.message,true);}}catch(error){$("#publication-status").textContent=error.message;}}
async function loadPublishedEntries(){const params=new URLSearchParams({workflow:"PUBLISHED",q:$("#published-query").value,limit:"100",offset:"0"});try{const data=await api(`/api/editorial/entries?${params}`);const allowed=["approver","administrator"].includes(currentUser()?.role);$("#published-summary").textContent=`${n(data.total)} entradas publicadas${data.total>data.items.length?` · a mostrar ${n(data.items.length)}`:""}`;$("#published-entries").innerHTML=data.items.map(item=>`<article class="publication-entry workflow-published"><span><strong>${h(item.lemma)}</strong><small>${h(resourceLabel(item.resource))} · ${h(item.grammatical_info||"sem classe")}</small></span><span class="publication-entry__actions"><button type="button" class="secondary compact" data-published-workflow="NEEDS_REVISION" data-published-id="${h(item.public_id)}"${allowed?"":" disabled"}>Pedir revisão</button><button type="button" class="danger compact" data-published-workflow="REMOVED" data-published-id="${h(item.public_id)}"${allowed?"":" disabled"}>Apagar</button></span></article>`).join("")||"<p>Não foram encontradas entradas publicadas.</p>";document.querySelectorAll("[data-published-workflow]").forEach(node=>node.addEventListener("click",()=>changePublishedWorkflow(node.dataset.publishedId,node.dataset.publishedWorkflow)));}catch(error){$("#published-summary").textContent=error.message;}}
$("#published-search-form").addEventListener("submit",event=>{event.preventDefault();loadPublishedEntries();});
async function changePublishedWorkflow(id,target){let confirmed=false;if(target==="REMOVED"){if(!confirm("A entrada publicada será assinalada para remoção. Continuar?"))return;if(!confirm("Segunda confirmação: remover esta entrada na próxima publicação?"))return;confirmed=true;}try{await api(`/api/editorial/entries/${encodeURIComponent(id)}/workflow`,{method:"POST",body:JSON.stringify({target,actor:actor(),confirmed,comment:`Decisão de publicação: ${workflowLabel(target)}`})});toast(target==="REMOVED"?"Remoção adicionada às operações pendentes.":"Entrada devolvida para revisão.");await Promise.all([loadOverview(),loadPublicationEntries(),loadPublishedEntries(),loadEntries()]);}catch(error){toast(error.message,true);}}
function releaseStateLabel(value){return {candidate:"Preparação técnica",approved:"Verificada",indexed:"Indexada",tested:"Testada",active:"Publicada",archived:"Anterior"}[value]||value;}
function renderReleases(releases,active){$("#releases").innerHTML=`<table><thead><tr><th>Versão</th><th>Estado</th><th>Responsável</th><th>Data</th><th>Ações</th></tr></thead><tbody>${releases.map(release=>{const actions=release.state==="archived"?`<button class="secondary" data-release-action="rollback" data-release="${h(release.release_id)}">Reverter</button>`:"";return `<tr><td><strong>${h(release.release_id)}</strong>${release.release_id===active?'<br><span class="active-release">Ativa</span>':""}<br><small>${h(release.description||"")}</small></td><td>${h(releaseStateLabel(release.state))}</td><td>${h(release.approved_by||release.prepared_by||"—")}</td><td>${h(formatDate(release.created_at))}</td><td class="table-actions">${actions}</td></tr>`;}).join("")}</tbody></table>`;document.querySelectorAll("[data-release-action]").forEach(node=>node.addEventListener("click",()=>releaseAction(node.dataset.release)));}
async function releaseAction(id){if(!confirm(`Reverter a aplicação pública para ${id}?`))return;try{await api(`/api/editorial/releases/${encodeURIComponent(id)}/rollback`,{method:"POST",body:JSON.stringify({actor:actor(),comment:`Reversão para ${id}`})});publicationTimer=setInterval(loadPublication,1000);loadPublication();}catch(error){toast(error.message,true);}}

// Relatório de auditoria
let lastAuditReport = null;
async function loadAudit(){
  try {
    const report=await api("/api/editorial/audit"); lastAuditReport=report;
    const sum=items=>(items||[]).reduce((total,item)=>total+Number(item.count||0),0);
    const stateText=(report.states||[]).map(item=>`${workflowLabel(item.value)}: ${n(item.count)}`).join(" · ")||"—";
    const issueText=(report.validation_issues||[]).map(item=>`${item.value}: ${n(item.count)}`).join(" · ")||"Sem problemas registados";
    $("#audit-summary").innerHTML=`<section class="card"><h2>Conjunto de trabalho</h2><strong>${n(sum(report.resources))} entradas</strong><p>${h(stateText)}</p></section><section class="card"><h2>Persistência</h2><strong>${report.persistence?.has_unsaved_changes?"Alterações por guardar":"TEI/XML sincronizado"}</strong><p>Última salvaguarda: ${h(formatDate(report.persistence?.last_saved_at))}</p></section><section class="card"><h2>Validação</h2><strong>${n(sum(report.validation_issues))} ocorrências</strong><p>${h(issueText)}</p></section><section class="card"><h2>Publicações</h2><strong>${n((report.releases||[]).length)} versões no relatório</strong><p>Relatório gerado em ${h(formatDate(report.generated_at))}</p></section>`;
    $("#audit-events").innerHTML=`<table><thead><tr><th>Data</th><th>Operação</th><th>Responsável</th><th>Estado</th><th>Comentário</th></tr></thead><tbody>${(report.recent_events||[]).map(event=>`<tr><td>${h(formatDate(event.created_at))}</td><td>${h(event.event_type)}</td><td>${h(event.actor)}</td><td>${h(event.previous_state||"—")} → ${h(event.resulting_state||"—")}</td><td>${h(event.comment||"—")}</td></tr>`).join("")}</tbody></table>`;
  } catch(error){$("#audit-events").textContent=error.message;}
}
$("#refresh-audit").addEventListener("click",loadAudit);
$("#download-audit").addEventListener("click",()=>{if(!lastAuditReport){toast("Atualize primeiro o relatório.",true);return;}const blob=new Blob([JSON.stringify(lastAuditReport,null,2)],{type:"application/json"});const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=`auditoria-acl-${new Date().toISOString().slice(0,10)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);});

Promise.all([loadOverview(),loadControlledValues()]).catch(error=>toast(error.message,true));
