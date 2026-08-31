const $ = selector => document.querySelector(selector);
const editorialBasePath = (document.querySelector('meta[name="acl-editor-base-path"]')?.content || "").replace(/\/+$/, "");
let overviewData = null;
let selectedId = null;
let selectedEntry = null;
let controlledValues = [];
let publicationItems = [];
let publicationTimer = null;
let backupTimer = null;
let entryOffset = 0;
let activeAlphabet = "";
let xmlComparison = null;
let comparedFileKey = "";
let accessSession = {mode:"basic",read_only:false};
let readOnlyAccess = false;
let helpMode = localStorage.getItem("acl-editorial-help") === "on";
const HELP_TARGET_SELECTOR = 'button:not(:disabled),a,select:not(:disabled),input:not([type="hidden"]):not(:disabled),textarea:not(:disabled),summary,[role="button"]';

const HELP_TEXT_BY_ID = {
  "current-user": ["Seleciona a pessoa responsável pela operação e o respetivo perfil.", "Muda o responsável ativo; as permissões e os registos seguintes passam a usar essa conta."],
  "help-guide": ["Abre o guia geral da aplicação editorial.", "Abre o documento de ajuda num novo separador."],
  "help-toggle": ["Ativa ou desativa a ajuda contextual.", "Mostra ou remove os indicadores (i) junto dos elementos interativos."],
  "add-controlled": ["Cria uma opção na lista controlada atual.", "Pede o valor e a descrição e adiciona-os à lista."],
  "controlled-category": ["Escolhe a lista controlada que está a ser gerida.", "Carrega as classes gramaticais ou os domínios correspondentes."],
  "controlled-sort": ["Define o critério de ordenação da lista.", "Reordena os valores por alfabeto ou quantidade de utilizações."],
  "controlled-direction": ["Define o sentido da ordenação.", "Alterna entre ordem crescente e decrescente."],
  "save-canonical": ["Cria uma salvaguarda canónica TEI/XML do trabalho atual.", "Exporta os dados e disponibiliza o XML e o respetivo log para descarga."],
  "sync-github": ["Cria um snapshot restaurável da aplicação.", "Com confirmação, comprime o estado atual e sincroniza-o com o repositório privado de dados."],
  "tei-upload": ["Seleciona o ficheiro TEI/XML que representa os dados canónicos.", "Permite comparar o ficheiro com a base atual antes de qualquer substituição."],
  "compare-xml": ["Compara o XML selecionado com a base de dados atual.", "Não altera dados; apresenta entradas adicionadas, removidas e alteradas e prepara um CSV."],
  "replace-corpus": ["Substitui integralmente os dados lexicais pelo XML comparado.", "Depois das confirmações, valida e ativa o novo corpus, preservando contas e configuração."],
  "download-comparison-csv": ["Descarrega o relatório integral das diferenças.", "Guarda um CSV com a identificação e os campos alterados de cada entrada."],
  "query": ["Pesquisa entradas por lema ou prefixo.", "Permite escrever o texto que será aplicado quando pesquisar."],
  "resource-filter": ["Filtra pelo Dicionário ou Vocabulário.", "Atualiza os resultados e as contagens dos restantes filtros."],
  "workflow-filter": ["Filtra pelo estado do ciclo editorial interno.", "Mostra apenas entradas no estado de workflow escolhido."],
  "grammar-filter": ["Filtra pela classe gramatical.", "Mostra apenas entradas com a classe escolhida e recalcula as restantes contagens."],
  "domain-filter": ["Filtra pelo domínio ou marca temática.", "Mostra apenas entradas associadas ao domínio escolhido."],
  "severity-filter": ["Filtra pelo tipo concreto de problema de validação.", "Mostra as entradas afetadas pela regra escolhida, por exemplo Falta de lema ou Relax NG inválido."],
  "clear-filters": ["Limpa a pesquisa e todos os filtros.", "Volta a mostrar o conjunto completo de entradas."],
  "alphabet-all": ["Remove o filtro por letra inicial.", "Volta a apresentar entradas de todas as letras."],
  "load-more-editorial": ["Carrega o bloco seguinte de resultados.", "Acrescenta mais entradas ao fim da lista atual."],
  "edit-entry": ["Abre o conteúdo da entrada em modo de edição.", "Substitui a leitura pelos campos editáveis, sem gravar nada automaticamente."],
  "cancel-edit": ["Fecha o modo de edição.", "Descarta alterações ainda não gravadas e volta à leitura da entrada."],
  "select-visible": ["Seleciona as operações prontas atualmente visíveis.", "Adiciona essas entradas à próxima publicação; ainda não publica."],
  "unselect-visible": ["Retira da seleção as operações visíveis.", "Remove-as da próxima publicação sem alterar o seu estado de workflow."],
  "release-description": ["Define a descrição da nova versão.", "O texto escrito acompanhará a release no histórico de publicações."],
  "publish-selected": ["Publica as entradas e remoções selecionadas.", "Depois da confirmação, gera e guarda o TEI/XML, atualiza a Interface Pública e muda as entradas para Publicada."],
  "readonly-key": ["Define a chave URL do acesso temporário só de leitura.", "A nova chave substitui imediatamente qualquer chave anterior; o servidor guarda apenas resumos criptográficos."],
  "generate-readonly-key": ["Gera localmente uma chave aleatória de 32 caracteres.", "Preenche o campo sem enviar ou guardar a chave até clicar em Guardar chave."],
  "save-readonly-key": ["Ativa o acesso de avaliação com a chave indicada.", "Invalida a chave anterior e cria o URL que pode ser partilhado."],
  "disable-readonly-key": ["Desativa o acesso por chave.", "Apaga a configuração e invalida imediatamente todas as sessões de avaliação."],
  "refresh-audit": ["Atualiza o relatório de auditoria.", "Volta a consultar o servidor e substitui o resumo e a atividade recente pelos dados mais atuais."],
  "download-audit": ["Exporta o relatório completo de auditoria em JSON.", "Descarrega um ficheiro estruturado, adequado a processamento técnico."],
  "download-audit-csv": ["Exporta as operações recentes em CSV.", "Descarrega a tabela visível num formato prático para folhas de cálculo."],
};

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

function interactiveLabel(node) {
  const explicit = node.getAttribute("aria-label") || node.getAttribute("title") || node.placeholder;
  const owner = node.id ? document.querySelector(`label[for="${CSS.escape(node.id)}"]`) : null;
  return String(explicit || owner?.textContent || node.textContent || node.value || node.name || "este controlo").replace(/\s+/g," ").trim().replace(/\(i\)$/i,"");
}
function contextualHelp(node) {
  if (HELP_TEXT_BY_ID[node.id]) return HELP_TEXT_BY_ID[node.id];
  if (node.dataset.view) return [`Abre a área “${interactiveLabel(node)}” da aplicação.`, "Muda a secção visível sem alterar dados."];
  if (node.dataset.entryId) return [`Representa a entrada “${interactiveLabel(node)}” na lista de resultados.`, "Carrega a entrada no painel de detalhe para consulta e eventual edição."];
  if (node.dataset.letter) return [`Filtra o catálogo pela letra ${node.dataset.letter.toUpperCase()}.`, "Mostra as entradas cujo lema começa por esta letra."];
  if (node.dataset.workflow) return [`Representa o estado editorial ${workflowLabel(node.dataset.workflow)}.`, "Se estiver disponível para o perfil ativo, muda esta entrada para esse estado depois das validações necessárias."];
  if (node.dataset.publicationId) return ["Inclui ou exclui esta entrada da próxima publicação.", "Altera apenas a seleção; a interface pública só muda quando o aprovador publicar."];
  if (node.dataset.cvSave) return ["Grava o valor e a descrição desta opção controlada.", "Atualiza a lista e propõe uma união se o valor passar a coincidir com outro."];
  if (node.dataset.cvDelete) return ["Apaga esta opção controlada.", "Pede confirmação e só remove o valor se não estiver a ser utilizado por entradas."];
  if (node.dataset.restore) return ["Repõe uma revisão anterior desta entrada.", "Pede confirmação e restaura o conteúdo guardado nessa revisão, mantendo um novo registo de auditoria."];
  if (node.dataset.waive) return ["Dispensa este erro de validação para a versão atual da entrada.", "Pede uma justificação obrigatória e permite prosseguir sem apagar o registo do erro."];
  if (node.dataset.publishedWorkflow === "REMOVED") return ["Marca uma entrada publicada para remoção.", "Depois de confirmar, adiciona a remoção à próxima publicação."];
  if (node.dataset.publishedWorkflow === "NEEDS_REVISION") return ["Devolve uma entrada publicada para revisão.", "Retira-a do estado Publicada no trabalho editorial para permitir novas correções."];
  if (node.dataset.releaseAction) return ["Repõe uma versão pública anterior.", "Pede confirmação, verifica a integridade e volta a ativar essa release."];
  if (node.matches("summary")) return [`Expande ou recolhe “${interactiveLabel(node)}”.`, "Mostra ou oculta os dados incluídos nesta secção."];
  if (node.matches("select")) return [`Campo de escolha: ${interactiveLabel(node)}.`, "Abre a lista de opções e aplica o valor selecionado."];
  if (node.matches('input[type="checkbox"]')) return [`Opção: ${interactiveLabel(node)}.`, "Alterna esta opção entre ligada e desligada."];
  if (node.matches("input,textarea")) return [`Campo: ${interactiveLabel(node)}.`, "Permite introduzir ou alterar o valor; a mudança só é persistida pela ação de gravação associada."];
  if (node.matches("a")) return [`Ligação: ${interactiveLabel(node)}.`, "Abre o destino indicado pela ligação."];
  return [`Controlo “${interactiveLabel(node)}”.`, "Executa a ação indicada no próprio controlo."];
}
function positionHelpPopover(marker) {
  const margin = 12;
  const gap = 7;
  const rect = marker.getBoundingClientRect();
  const width = Math.min(330, Math.max(0, window.innerWidth - margin * 2));
  const centered = rect.left + rect.width / 2 - width / 2;
  const left = Math.min(Math.max(margin, centered), Math.max(margin, window.innerWidth - width - margin));
  marker.style.setProperty("--help-popover-left", `${Math.round(left)}px`);
  marker.style.setProperty("--help-popover-top", `${Math.round(rect.bottom + gap)}px`);
}
function addHelpMarker(node) {
  if (!helpMode || node.dataset.helpReady || node.classList.contains("context-help")) return;
  const [what, action] = contextualHelp(node);
  const marker = document.createElement("span");
  marker.className = "context-help";
  marker.setAttribute("aria-hidden","true");
  marker.dataset.help = `O que é: ${what}\nO que acontece se clicar: ${action}`;
  marker.title = `${what} ${action}`;
  marker.textContent = "(i)";
  ["click","mousedown","pointerdown","keydown"].forEach(name => marker.addEventListener(name,event => event.stopPropagation()));
  marker.addEventListener("pointerenter",()=>positionHelpPopover(marker));
  marker.addEventListener("focus",()=>positionHelpPopover(marker));
  if (node.id === "help-toggle") node.closest("label")?.insertAdjacentElement("afterend",marker);
  else if (node.matches("input,select,textarea") && node.closest("label")) {
    const host = node.closest("label");
    host.classList.add("context-help-field-host");
    marker.classList.add("context-help--field");
    host.appendChild(marker);
  }
  else if (node.matches("input,select,textarea") && node.closest(".search-box")) {
    marker.classList.add("context-help--search-field");
    node.closest(".search-box").appendChild(marker);
  }
  else if (node.matches("button,a,summary")) {
    node.classList.add("context-help-host");
    marker.classList.add("context-help--inside");
    node.appendChild(marker);
  }
  else node.insertAdjacentElement("afterend",marker);
  node.dataset.helpReady = "true";
}
function refreshHelpMarkers(root = document) {
  if (!helpMode) return;
  root.querySelectorAll?.(HELP_TARGET_SELECTOR).forEach(addHelpMarker);
}
function setHelpMode(enabled) {
  helpMode = Boolean(enabled);
  localStorage.setItem("acl-editorial-help", helpMode ? "on" : "off");
  document.body.classList.toggle("help-mode",helpMode);
  $("#help-toggle").checked = helpMode;
  $("#help-toggle-state").textContent = helpMode ? "Ativada" : "Desativada";
  document.querySelectorAll(".context-help").forEach(node=>node.remove());
  document.querySelectorAll(".context-help-host").forEach(node=>node.classList.remove("context-help-host"));
  document.querySelectorAll(".context-help-field-host").forEach(node=>node.classList.remove("context-help-field-host"));
  document.querySelectorAll("[data-help-ready]").forEach(node=>delete node.dataset.helpReady);
  if (helpMode) refreshHelpMarkers();
}
$("#help-toggle").addEventListener("change",event=>setHelpMode(event.target.checked));
new MutationObserver(records=>{if(!helpMode)return;records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===1){if(node.matches?.(HELP_TARGET_SELECTOR))addHelpMarker(node);refreshHelpMarkers(node);}}));}).observe(document.body,{childList:true,subtree:true});

function activateView(viewId, load = true) {
  if (viewId === "admin-panel" && readOnlyAccess) return;
  document.querySelectorAll(".nav-link").forEach(item => item.classList.toggle("active", item.dataset.view === viewId));
  document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === viewId));
  if (!load) return;
  if (viewId === "governance-panel") loadControlledValues();
  if (viewId === "entries-panel") loadEntries();
  if (viewId === "publication-panel") { renderPersistence(); loadPublication(); }
  if (viewId === "audit-panel") loadAudit();
  if (viewId === "admin-panel") loadReadonlyKeyStatus();
}
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => activateView(button.dataset.view)));

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
async function loadAccessSession() {
  accessSession = await api("/api/editorial/session");
  readOnlyAccess = Boolean(accessSession.read_only);
  document.body.classList.toggle("read-only-access", readOnlyAccess);
  $("#admin-nav").hidden = readOnlyAccess;
}
function populateUsers(users) {
  const select = $("#current-user");
  const saved = localStorage.getItem("acl-editorial-user") || "editor.demo";
  select.innerHTML = users.map(user => `<option value="${h(user.username)}">${h(user.display_name)} — ${h(roleLabel(user.role))}</option>`).join("");
  select.value = users.some(user => user.username === saved) ? saved : users[0]?.username || "";
  select.disabled = readOnlyAccess;
  select.onchange = () => { localStorage.setItem("acl-editorial-user", select.value); updateRoleIndicator(); if (selectedEntry && !$("#edit-form")) renderPublicEntry(selectedEntry); if ($("#governance-panel").classList.contains("active")) loadControlledValues(); if ($("#publication-panel").classList.contains("active")) loadPublicationEntries(); };
  updateRoleIndicator();
}
function updateRoleIndicator() { const role = currentUser()?.role || ""; const node = $("#role-indicator"); node.textContent = readOnlyAccess ? "Consulta" : roleLabel(role); node.className = readOnlyAccess ? "role-indicator role-readonly" : `role-indicator role-${role}`; document.body.dataset.editorialRole = readOnlyAccess ? "readonly" : role; $("#add-controlled").disabled = readOnlyAccess || !["reviewer","approver","administrator"].includes(role); updateImportControls(); $("#publish-selected").disabled = readOnlyAccess || !["approver","administrator"].includes(role); if ($("#sync-github")) $("#sync-github").disabled = readOnlyAccess || !["approver","administrator"].includes(role); }
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
function problemLabel(value) { return {LEMMA_REQUIRED:"Falta de lema",RNG_INVALID:"Relax NG inválido",XML_ID_REQUIRED:"Identificador XML em falta",XML_ID_DUPLICATE:"Identificador XML repetido",GRAMMAR_UNMAPPED:"Classe gramatical não controlada",DOMAIN_UNMAPPED:"Domínio não controlado",RELATION_TARGET_MISSING:"Destino de remissão inexistente"}[value] || String(value || "").replaceAll("_"," ").toLocaleLowerCase("pt-PT").replace(/^./,letter=>letter.toLocaleUpperCase("pt-PT")); }
function entryFiltersActive() { return Boolean($("#query").value.trim() || ["#resource-filter","#workflow-filter","#grammar-filter","#domain-filter","#severity-filter"].some(selector => $(selector).value)); }
function renderEntryFacets(current = {}, total = 0, contextual = entryFiltersActive()) {
  if (!overviewData?.active_import) return;
  const global = overviewData.filter_counts; const globalTotal = overviewData.active_import.entry_count;
  const workflowGlobal = completeCounts(global.workflow,["DRAFT","EDITED","REVIEWED","NEEDS_REVISION","VALIDATED","PUBLISHED","REMOVED"]);
  const workflowCurrent = completeCounts(current.workflow || [],["DRAFT","EDITED","REVIEWED","NEEDS_REVISION","VALIDATED","PUBLISHED","REMOVED"]);
  countedSelect("#resource-filter",current.resource || [],global.resource,"Dicionário e Vocabulário",total,globalTotal,resourceLabel,contextual);
  countedSelect("#workflow-filter",workflowCurrent,workflowGlobal,"Todos",total,globalTotal,workflowLabel,contextual);
  countedSelect("#grammar-filter",current.grammar || [],global.grammar,"Todas",total,globalTotal,value => controlledLabel("grammar",value),contextual);
  countedSelect("#domain-filter",current.domains || [],global.domains,"Todos",total,globalTotal,value => controlledLabel("domain",value),contextual);
  countedSelect("#severity-filter",current.problems || [],global.problems || [],"Todos os problemas",total,globalTotal,problemLabel,contextual);
}

function renderPersistence(result = null) {
  if (!overviewData?.persistence) return;
  const state = overviewData.persistence;
  $("#persistence-status").textContent = state.has_unsaved_changes ? "Existem alterações por guardar em TEI/XML." : "A base de dados está sincronizada com o último TEI/XML guardado.";
  $("#persistence-status").className = state.has_unsaved_changes ? "persistence-warning" : "persistence-ok";
  $("#last-saved").textContent = state.last_saved_at ? `Última salvaguarda: ${formatDate(state.last_saved_at)}${state.last_saved_path ? ` · ${state.last_saved_path.split("/").pop()}` : ""}` : "Ainda não foi guardado um TEI/XML por esta aplicação.";
  const xmlName = result?.xml_name || state.last_saved_path?.split("/").pop();
  const logName = result?.log_name || (xmlName?.endsWith(".xml") ? xmlName.replace(/\.xml$/, ".log.json") : "");
  $("#saved-downloads").innerHTML = xmlName ? `<a class="button-link" href="${apiUrl(`/api/editorial/exports/${encodeURIComponent(xmlName)}`)}" download>Descarregar TEI/XML</a>${logName ? `<a class="button-link secondary" href="${apiUrl(`/api/editorial/exports/${encodeURIComponent(logName)}`)}" download>Descarregar log</a>` : ""}` : "";
}

$("#save-canonical").addEventListener("click", async () => {
  const button = $("#save-canonical"); button.disabled = true;
  try { const result = await api("/api/editorial/save-canonical", {method:"POST",body:JSON.stringify({actor:actor()})}); await loadOverview(); renderPersistence(result); toast(`TEI/XML guardado com ${n(result.entries)} entradas.`); }
  catch(error){toast(error.message,true);} finally{button.disabled=false;}
});

function renderGithubBackup(state) {
  const status = $("#github-backup-status");
  const detail = $("#github-backup-detail");
  const button = $("#sync-github");
  if (!status || !detail || !button) return;
  const configured = Boolean(state?.configured);
  status.textContent = state?.message || (configured ? "Ainda não foi executada uma sincronização." : "A sincronização GitHub não está configurada.");
  status.className = state?.state === "succeeded" ? "persistence-ok" : state?.state === "running" ? "muted" : "persistence-warning";
  const parts = [];
  if (state?.finished_at) parts.push(`Última tentativa: ${formatDate(state.finished_at)}`);
  if (state?.release_id) parts.push(`Release: ${state.release_id}`);
  if (state?.commit) parts.push(`Commit: ${state.commit.slice(0,12)}`);
  detail.textContent = parts.join(" · ") || "O backup inclui a base editorial, contas, workflow, auditoria, configuração e a release ativa.";
  button.disabled = !configured || state?.state === "running" || !["approver","administrator"].includes(currentUser()?.role);
  if (state?.state === "running" && !backupTimer) backupTimer = setInterval(loadGithubBackupStatus,2000);
  if (state?.state !== "running" && backupTimer) { clearInterval(backupTimer); backupTimer=null; }
}
async function loadGithubBackupStatus() {
  try { renderGithubBackup(await api("/api/editorial/github-backup/status")); }
  catch(error) { $("#github-backup-status").textContent=error.message; $("#github-backup-status").className="persistence-warning"; }
}
$("#sync-github").addEventListener("click", async () => {
  if (!confirm("Criar agora um snapshot completo e enviá-lo para o repositório privado do GitHub?")) return;
  const button=$("#sync-github"); button.disabled=true;
  try { const state=await api("/api/editorial/github-backup/sync",{method:"POST",body:JSON.stringify({actor:actor()})}); renderGithubBackup(state); toast("Sincronização GitHub iniciada."); }
  catch(error){toast(error.message,true);await loadGithubBackupStatus();}
});

function xmlFileKey(file) { return file ? `${file.name}:${file.size}:${file.lastModified}` : ""; }
function updateImportControls() {
  const file = $("#tei-upload")?.files?.[0];
  const compared = Boolean(xmlComparison && comparedFileKey === xmlFileKey(file));
  const allowed = ["approver","administrator"].includes(currentUser()?.role);
  if ($("#replace-corpus")) $("#replace-corpus").disabled = !compared || !allowed || Boolean(overviewData?.persistence?.has_unsaved_changes);
}
function resetXmlComparison() {
  xmlComparison = null; comparedFileKey = "";
  $("#xml-comparison").hidden = true;
  $("#xml-comparison-summary").innerHTML = "";
  $("#xml-comparison-table").innerHTML = "";
  updateImportControls();
}
function renderXmlComparison(report) {
  const summary = report.summary;
  $("#xml-comparison").hidden = false;
  $("#xml-comparison-summary").innerHTML = `
    <article><strong>${n(summary.added)}</strong><span>Adicionadas</span></article>
    <article><strong>${n(summary.removed)}</strong><span>Removidas</span></article>
    <article><strong>${n(summary.changed)}</strong><span>Alteradas</span></article>
    <article><strong>${n(summary.unchanged)}</strong><span>Sem alterações</span></article>`;
  const labels={added:"Adicionada",removed:"Removida",changed:"Alterada"};
  $("#xml-comparison-table").innerHTML = report.preview.length ? `<table><thead><tr><th>Tipo</th><th>Entrada</th><th>Lema na base</th><th>Lema no XML</th><th>Campos diferentes</th></tr></thead><tbody>${report.preview.map(item=>`<tr><td>${h(labels[item.type]||item.type)}</td><td><code>${h(item.public_id)}</code></td><td>${h(item.current_lemma||"—")}</td><td>${h(item.xml_lemma||"—")}</td><td>${h((item.fields||[]).join(", ")||"—")}</td></tr>`).join("")}</tbody></table>` : "<p>Não foram encontradas diferenças.</p>";
  $("#xml-comparison-note").textContent = report.preview_truncated ? `A tabela mostra as primeiras ${n(report.preview.length)} diferenças; o CSV contém as ${n(report.different)}.` : `${n(report.different)} diferenças no total.`;
  const download=$("#download-comparison-csv"); download.href=apiUrl(`/api/editorial/exports/${encodeURIComponent(report.csv_name)}`); download.hidden=false;
  refreshHelpMarkers($("#xml-comparison"));
}
async function compareXml() {
  const file = $("#tei-upload").files[0];
  if (!file) { toast("Selecione primeiro um ficheiro TEI/XML ou XML.XZ.",true); return; }
  const button=$("#compare-xml"); button.disabled=true;
  $("#import-status").textContent="A enviar e comparar o XML sem alterar os dados…";
  try {
    const response=await fetch(apiUrl("/api/editorial/compare-xml"),{method:"POST",headers:{"Content-Type":"application/octet-stream","X-ACL-Actor":actor(),"X-ACL-Filename":file.name},body:file});
    const report=await response.json(); if(!response.ok) throw new Error(report.message||report.error||"A comparação falhou.");
    xmlComparison=report; comparedFileKey=xmlFileKey(file); renderXmlComparison(report);
    $("#import-status").textContent=`Comparação concluída: ${n(report.different)} diferenças.`;
  } catch(error) { resetXmlComparison(); $("#import-status").textContent=error.message; toast(error.message,true); }
  finally { button.disabled=false; updateImportControls(); }
}
async function replaceCorpus() {
  const file=$("#tei-upload").files[0];
  if(!file || !xmlComparison || comparedFileKey!==xmlFileKey(file)){toast("Compare primeiro este mesmo ficheiro XML.",true);return;}
  if(overviewData?.persistence?.has_unsaved_changes){toast("Guarde primeiro as alterações atuais em TEI/XML.",true);return;}
  if(!confirm(`Substituir integralmente os dados pelo XML comparado? Foram encontradas ${n(xmlComparison.different)} diferenças.`))return;
  if(!confirm("Segunda confirmação: contas e configurações serão preservadas, mas o corpus atual será substituído. Continuar?"))return;
  const button=$("#replace-corpus"); button.disabled=true; $("#import-status").textContent="A validar e substituir integralmente os dados…";
  try {
    const response=await fetch(apiUrl("/api/editorial/import"),{method:"POST",headers:{"Content-Type":"application/octet-stream","X-ACL-Actor":actor(),"X-ACL-Filename":file.name,"X-ACL-Comparison-SHA256":xmlComparison.source_sha256},body:file});
    const result=await response.json(); if(!response.ok)throw new Error(result.message||result.error||"A importação falhou.");
    $("#import-status").textContent=`${n(result.imported)} entradas substituídas sem erros.`; $("#tei-upload").value=""; resetXmlComparison(); await Promise.all([loadOverview(),loadControlledValues(),loadEntries()]);
  } catch(error){$("#import-status").textContent=error.message;toast(error.message,true);}
  finally{updateImportControls();}
}
$("#tei-upload").addEventListener("change",()=>{resetXmlComparison();$("#import-status").textContent="";});
$("#compare-xml").addEventListener("click",compareXml);
$("#replace-corpus").addEventListener("click",replaceCorpus);

// Listas controladas
["#controlled-category","#controlled-sort","#controlled-direction"].forEach(selector => $(selector).addEventListener("change", loadControlledValues));
async function loadControlledValues() {
  const params = new URLSearchParams({category:$("#controlled-category").value,sort:$("#controlled-sort").value,direction:$("#controlled-direction").value});
  try {
    const data = await api(`/api/editorial/controlled-values?${params}`); controlledValues = data.items;
    const locked = !readOnlyAccess && ["reviewer","approver","administrator"].includes(currentUser()?.role) ? "" : " disabled";
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
  const filters = {"#resource-filter":"resource","#workflow-filter":"workflow","#grammar-filter":"grammar","#domain-filter":"domain","#severity-filter":"issue_rule"};
  Object.entries(filters).forEach(([selector,key]) => { if ($(selector).value) params.set(key,$(selector).value); });
  try {
    const payload = await api(`/api/editorial/entries?${params}`);
    const markup = payload.items.map(item => {
      const missingLemma = !String(item.lemma || "").trim();
      const lemma = missingLemma ? "Entrada sem lema" : item.lemma;
      const issues = item.error_count ? `${n(item.error_count)} erros` : item.warning_count ? `${n(item.warning_count)} avisos` : "";
      return `<button class="result-card ${workflowClass(item.workflow_status)}${item.public_id === selectedId ? " is-selected" : ""}" data-entry-id="${h(item.public_id)}"><span class="result-card__top"><h3 class="${missingLemma ? "is-missing-lemma" : ""}">${h(lemma)}</h3><span class="result-card__badges"><span class="result-card__source">${h(resourceLabel(item.resource))}</span><span class="result-card__state">${h(workflowEntryLabel(item))}</span></span></span><p class="result-card__grammar">${h(item.grammatical_info || "sem classificação")}</p>${issues ? `<small class="result-card__issues">${h(issues)}</small>` : ""}</button>`;
    }).join("");
    if (append) $("#entries").insertAdjacentHTML("beforeend",markup);
    else $("#entries").innerHTML = markup || `<div class="entry-empty"><p>Sem resultados.</p></div>`;
    entryOffset += payload.items.length;
    $("#status").textContent = `${n(payload.total)} resultados`;
    $("#load-more-editorial").hidden = entryOffset >= payload.total;
    renderEntryFacets(payload.facets,payload.total);
    document.querySelectorAll("[data-entry-id]:not([data-bound])").forEach(node => {
      node.dataset.bound = "true";
      node.addEventListener("click", () => showEntry(node.dataset.entryId));
    });
    if (!append && payload.items.length) await showEntry(payload.items[0].public_id);
    else if (!append) {
      selectedId=null; selectedEntry=null;
      $("#detail").innerHTML=`<div class="entry-empty"><h2>Sem resultados</h2><p>Remova alguns filtros ou altere a pesquisa.</p></div>`;
    }
  } catch (error) { $("#status").textContent = error.message; }
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
  const missingLemma = !String(entry.lemma || "").trim();
  const lemma = missingLemma ? "Entrada sem lema" : entry.lemma;
  const editBlocked = ["REMOVED","PUBLISHED"].includes(item.workflow_status);
  const editTitle = item.workflow_status === "REMOVED" ? "Mude primeiro a entrada para um estado editável" : item.workflow_status === "PUBLISHED" ? "Mude primeiro a entrada para um estado editável" : "";
  const metadata = `<section class="entry-metadata" aria-label="Metadados da entrada"><h3>Metadados da entrada</h3><dl><div><dt>Origem</dt><dd>${h(resourceLabel(item.resource))}</dd></div><div><dt>Estado recebido no XML</dt><dd>${h(entry.source_status_label || entry.source_status || "Sem estado")}</dd></div></dl></section>`;
  $("#detail").innerHTML = `${renderWorkflow(item,{mode:"view",editBlocked,editTitle})}${metadata}<header class="entry-header lexical-data-card"><span class="content-kicker">Conteúdo lexical</span><div class="entry-title-row"><h2 class="${missingLemma ? "is-missing-lemma" : ""}">${h(lemma)}</h2><span class="entry-grammar">${h(entry.grammatical_label || entry.grammatical_info || "")}</span></div>${missingLemma ? '<p class="missing-data-note">O campo do lema principal está vazio nesta entrada.</p>' : ""}${variants.length ? `<p class="entry-variants"><strong>Outras formas:</strong> ${h(variants.join(" · "))}</p>` : ""}</header><div class="lexical-content">${glosses ? `<section class="entry-section entry-gloss"><h3 class="entry-section__title">Enquadramento da entrada</h3>${glosses}</section>` : ""}${imageMarkup(lexical.images)}<section class="entry-section"><h3 class="entry-section__title">${senses ? `Aceções e definições <span>(${lexical.senses.length})</span>` : "Informação ortográfica"}</h3>${senses || "<p>Esta entrada não contém aceções lexicográficas.</p>"}</section>${references ? `<section class="entry-section"><h3 class="entry-section__title">Relações e remissões</h3><div class="reference-list">Ver também: ${references}</div></section>` : ""}${notes ? `<section class="entry-section"><h3 class="entry-section__title">Notas</h3>${notes}</section>` : ""}${etymology ? `<section class="entry-section"><h3 class="entry-section__title">Etimologia</h3>${etymology}</section>` : ""}</div>${renderIssues(item)}${renderDiagnostics(item)}${renderHistory(item)}`;
  $("#edit-entry").addEventListener("click", () => renderEditEntry(item));
  bindEntryActions(item);
}
function renderEditEntry(item) {
  const grammarOptions = optionList(overviewData.controlled_options.grammar,item.grammatical_info);
  $("#detail").innerHTML = `${renderWorkflow(item,{mode:"edit"})}<form id="edit-form" class="edit-form lexical-data-card"><span class="content-kicker">Edição do conteúdo lexical</span><div class="edit-grid"><label>Lema<input id="lemma" value="${h(item.lemma)}" required></label><label>Classe gramatical<select id="grammar">${grammarOptions}</select></label></div><h3>Formas e variantes (${item.forms.length})</h3><div class="editable-list">${item.forms.map(form => `<label class="inline-field">${h(form.kind || "Forma")}<input data-form-id="${form.id}" value="${h(form.value)}"></label>`).join("") || "<p>Sem formas.</p>"}</div><h3>Aceções e definições (${item.senses.length})</h3>${item.senses.map(sense => `<section class="sense-editor"><strong>Aceção ${h(sense.number_label || sense.position_path)}</strong><textarea data-sense-id="${sense.id}">${h(sense.definition || sense.gloss || "")}</textarea></section>`).join("")}<h3>Marcas e domínios (${item.labels.length})</h3><div class="editable-list">${item.labels.map(label => `<label class="inline-field">${h(label.label_type)}<input data-label-id="${label.id}" value="${h(label.value)}"></label>`).join("") || "<p>Sem valores.</p>"}</div><h3>Relações e remissões (${item.relations.length})</h3><div class="editable-list">${item.relations.map(relation => `<div class="inline-field"><input data-relation-text="${relation.id}" value="${h(relation.target_text || "")}"><input data-relation-target="${relation.id}" value="${h(relation.target_id || "")}" placeholder="Identificador"></div>`).join("") || "<p>Sem remissões.</p>"}</div><label>Nota da revisão<textarea id="comment"></textarea></label><div class="actions"><button type="submit">Gravar alterações</button><button id="reload-entry" class="secondary" type="button">Recarregar</button></div></form>${renderIssues(item)}${renderDiagnostics(item)}${renderHistory(item)}`;
  $("#cancel-edit").addEventListener("click", () => renderPublicEntry(item)); $("#reload-entry").addEventListener("click", () => showEntry(item.public_id)); $("#edit-form").addEventListener("submit", event => saveEntry(event,item)); bindEntryActions(item);
}
function optionList(items, selected) { const values = [...items.map(item => item.value)]; if (selected && !values.includes(selected)) values.unshift(selected); return `<option value="">Sem valor</option>` + values.map(value => `<option value="${h(value)}"${value === selected ? " selected" : ""}>${h(value)}</option>`).join(""); }
function workflowTargets(status) { if(readOnlyAccess)return []; const role=currentUser()?.role === "administrator" ? "approver" : currentUser()?.role; const targets={editor:["DRAFT","EDITED","REMOVED"],reviewer:["DRAFT","EDITED","REVIEWED","NEEDS_REVISION","REMOVED"],approver:["DRAFT","EDITED","REVIEWED","NEEDS_REVISION","VALIDATED","REMOVED"]}; return (targets[role]||[]).filter(target=>target!==status); }
function workflowActionLabel(target) { return {DRAFT:"Em preparação",EDITED:"Editar",REVIEWED:"Rever editorialmente",NEEDS_REVISION:"Precisa de revisão",VALIDATED:"Validar",PUBLISHED:"Publicada",REMOVED:"Apagar"}[target] || workflowLabel(target); }
function renderWorkflow(item, options = {}) {
  const targets = new Set(workflowTargets(item.workflow_status));
  const primary = ["DRAFT","EDITED","REVIEWED","VALIDATED","PUBLISHED"];
  const alternatives = ["NEEDS_REVISION","REMOVED"];
  const role = currentUser()?.role || "";
  const stateButton = (state, alternative = false) => {
    const current = item.workflow_status === state;
    const available = targets.has(state);
    const label = current ? (state === "DRAFT" ? workflowEntryLabel(item) : workflowLabel(state)) : workflowActionLabel(state);
    const classes = ["workflow-step",workflowClass(state),current?"is-current":"",available?"is-available":"",alternative?"is-alternative":""].filter(Boolean).join(" ");
    const title = current ? `Estado atual: ${label}` : available ? workflowActionLabel(state) : state === "PUBLISHED" ? "Este estado só é aplicado por uma publicação efetiva" : `O perfil ${roleLabel(role)} não pode mudar diretamente para este estado`;
    const instruction = current ? "Estado atual" : available ? "Clique para aplicar" : state === "PUBLISHED" ? "Apenas em Publicação" : "Indisponível";
    return `<button type="button" class="${classes}"${current?' aria-current="step"':""}${available?` data-workflow="${state}"`:" disabled"} title="${h(title)}"><span class="workflow-step__label">${h(label)}</span><small>${instruction}</small></button>`;
  };
  const availableCount = targets.size;
  const publishedNote = item.workflow_status === "PUBLISHED" ? '<p class="workflow-lifecycle__note">Para alterar o conteúdo, mude primeiro a entrada para um dos estados editáveis permitidos pelo seu perfil.</p>' : "";
  const modeAction = readOnlyAccess ? "" : options.mode === "edit"
    ? '<button id="cancel-edit" class="secondary" type="button">Sair da edição</button>'
    : `<button id="edit-entry" type="button"${options.editBlocked ? " disabled" : ""}${options.editTitle ? ` title="${h(options.editTitle)}"` : ""}>Editar conteúdo</button>`;
  return `<section class="workflow-lifecycle entry-metadata" aria-label="Ciclo de vida desta entrada"><div class="workflow-lifecycle__heading"><div><span class="eyebrow">Ciclo de vida desta entrada</span><span class="entry-technical-id"><strong>Identificador técnico</strong> ${h(item.public_id)}</span></div><div class="workflow-lifecycle__context"><span>Perfil ativo: <strong>${h(roleLabel(role))}</strong></span>${modeAction}</div></div><div class="workflow-track">${primary.map(state=>stateButton(state)).join("")}</div><div class="workflow-alternatives"><span>Ações alternativas</span>${alternatives.map(state=>stateButton(state,true)).join("")}</div>${!availableCount&&!publishedNote?'<p class="workflow-lifecycle__note">Este perfil não tem transições disponíveis para o estado atual.</p>':publishedNote}</section>`;
}
function renderIssues(item) { return `<div class="issues">${item.validation_issues.map(issue => `<div class="validation-issue"><strong>${h(issue.severity.toUpperCase())} · ${h(issue.rule_code)}</strong><p>${h(issue.message)}</p>${issue.waiver ? `<small>Dispensado por ${h(issue.waiver.actor)}: ${h(issue.waiver.reason)}</small>` : issue.severity === "error" ? `<button class="secondary compact" type="button" data-waive="${h(issue.rule_code)}">Dispensar com justificação</button>` : ""}</div>`).join("")}</div>`; }
function renderDiagnostics(item) { const projection = {...item}; delete projection.raw_xml; delete projection.revisions; return `<details class="technical-details"><summary>Identificação e metadados técnicos</summary><div class="diagnostic-grid"><pre>${h(JSON.stringify(projection,null,2))}</pre><pre>${h(item.raw_xml)}</pre></div></details>`; }
function renderHistory(item) { return `<details class="history-details"><summary>Histórico e auditoria (${item.revisions.length + item.audit_events.length})</summary><ol class="revisions">${item.revisions.map(rev => `<li>#${rev.revision_no} · ${h(rev.actor || "sem autor")} · ${h(rev.comment || "sem nota")} <button class="secondary compact" data-restore="${rev.revision_no}">Repor</button></li>`).join("")}${item.audit_events.map(event => `<li>${h(event.event_type)} · ${h(event.actor)} · ${h(event.previous_state || "—")} → ${h(event.resulting_state || "—")}</li>`).join("")}</ol></details>`; }
function bindEntryActions(item) { document.querySelectorAll("[data-workflow]").forEach(node => node.addEventListener("click", () => changeWorkflow(item.public_id,node.dataset.workflow))); document.querySelectorAll("[data-restore]").forEach(node => node.addEventListener("click", () => restoreRevision(item.public_id,Number(node.dataset.restore)))); document.querySelectorAll("[data-waive]").forEach(node => node.addEventListener("click", () => waiveIssue(item.public_id,node.dataset.waive))); }
async function saveEntry(event,item) { event.preventDefault(); const submit = event.submitter; submit.disabled = true; try { const updated = await api(`/api/editorial/entries/${encodeURIComponent(item.public_id)}`,{method:"PATCH",body:JSON.stringify({expected_updated_at:item.updated_at,actor:actor(),comment:$("#comment").value,lemma:$("#lemma").value,grammatical_info:$("#grammar").value,forms:[...document.querySelectorAll("[data-form-id]")].map(node => ({id:Number(node.dataset.formId),value:node.value})),senses:[...document.querySelectorAll("[data-sense-id]")].map(node => ({id:Number(node.dataset.senseId),definition:node.value})),labels:[...document.querySelectorAll("[data-label-id]")].map(node => ({id:Number(node.dataset.labelId),value:node.value})),relations:[...document.querySelectorAll("[data-relation-text]")].map(node => ({id:Number(node.dataset.relationText),target_text:node.value,target_id:document.querySelector(`[data-relation-target="${node.dataset.relationText}"]`).value}))})}); selectedEntry=updated; renderPublicEntry(updated); toast("Alterações gravadas; a entrada passou para Editada."); await Promise.all([loadOverview(),loadEntries()]); } catch(error){toast(error.message,true);} finally{submit.disabled=false;} }
async function changeWorkflow(id,target) { let confirmed=false; if(target==="REMOVED"){if(!confirm("A entrada será marcada como apagada e deixará de ser exportada e publicada. Pretende continuar?"))return;if(!confirm("Segunda confirmação: apagar esta entrada?"))return;confirmed=true;} try { const item=await api(`/api/editorial/entries/${encodeURIComponent(id)}/workflow`,{method:"POST",body:JSON.stringify({target,actor:actor(),confirmed,comment:""})}); selectedEntry=item; renderPublicEntry(item); toast(`Entrada movida para ${workflowLabel(target)}.`); await Promise.all([loadOverview(),loadEntries(),loadPublicationEntries()]); } catch(error){toast(error.message,true);} }
async function restoreRevision(id,revision) { if(!confirm(`Repor a revisão ${revision}?`))return; try{const item=await api(`/api/editorial/entries/${encodeURIComponent(id)}/revisions/${revision}/restore`,{method:"POST",body:JSON.stringify({actor:actor(),comment:`Reposição da revisão ${revision}`})});renderPublicEntry(item);toast("Revisão reposta.");}catch(error){toast(error.message,true);} }
async function waiveIssue(id,rule) { const reason=prompt("Justificação obrigatória para dispensar este erro:");if(reason===null)return;try{const item=await api(`/api/editorial/entries/${encodeURIComponent(id)}/issues/${encodeURIComponent(rule)}/waive`,{method:"POST",body:JSON.stringify({actor:actor(),reason})});renderPublicEntry(item);toast("Erro dispensado para esta versão da entrada.");}catch(error){toast(error.message,true);} }

// Publicação seletiva
async function loadPublicationEntries(){try{const data=await api("/api/editorial/publication-entries?limit=500");publicationItems=data.items;const canSelect=!readOnlyAccess&&["approver","administrator"].includes(currentUser()?.role);$("#select-visible").disabled=!canSelect;$("#unselect-visible").disabled=!canSelect;$("#publication-selection-summary").textContent=`${n(data.selected)} selecionadas de ${n(data.total)} operações prontas`;$("#publication-entries").innerHTML=data.items.map(item=>`<label class="publication-entry ${workflowClass(item.workflow_status)}"><input type="checkbox" data-publication-id="${h(item.public_id)}"${item.selected ? " checked" : ""}${canSelect ? "" : " disabled"}><span><strong>${h(item.lemma)}</strong><small>${h(resourceLabel(item.resource))} · ${h(workflowLabel(item.workflow_status))} · ${h(item.grammatical_info || "sem classe")}</small></span></label>`).join("")||"<p>Não existem operações prontas. Uma entrada tem de chegar a <strong>Validada</strong> no Estado editorial.</p>";document.querySelectorAll("[data-publication-id]").forEach(node=>node.addEventListener("change",()=>setPublicationSelection([node.dataset.publicationId],node.checked)));}catch(error){$("#publication-selection-summary").textContent=error.message;}}
async function setPublicationSelection(ids,selected){if(!ids.length){toast("Não existem entradas visíveis para alterar.");return;}try{await api("/api/editorial/publication-selection",{method:"POST",body:JSON.stringify({public_ids:ids,selected,actor:actor()})});await loadPublicationEntries();}catch(error){toast(error.message,true);await loadPublicationEntries();}}
$("#select-visible").addEventListener("click",()=>setPublicationSelection(publicationItems.map(item=>item.public_id),true));$("#unselect-visible").addEventListener("click",()=>setPublicationSelection(publicationItems.filter(item=>item.selected).map(item=>item.public_id),false));
$("#publish-selected").addEventListener("click",async()=>{if(!confirm("Publicar as entradas selecionadas? Esta operação vai gerar e guardar o TEI/XML, atualizar a Interface Pública e mudar as entradas para Publicada."))return;const button=$("#publish-selected");button.disabled=true;try{await api("/api/editorial/publish-selected",{method:"POST",body:JSON.stringify({actor:actor(),description:$("#release-description").value})});publicationTimer=setInterval(loadPublication,1000);loadPublication();}catch(error){toast(error.message,true);updateRoleIndicator();}});
async function loadPublication(){try{const job=await api("/api/editorial/publish/status");$("#publication-status").textContent=job.message||"Nenhuma operação em curso.";$("#publish-selected").disabled=readOnlyAccess||job.state==="running"||!["approver","administrator"].includes(currentUser()?.role);renderGithubBackup(job.repository_backup);renderReleases(job.releases||[],job.active_release);renderPersistence();await loadPublicationEntries();if(job.state!=="running"&&publicationTimer){clearInterval(publicationTimer);publicationTimer=null;if(job.state==="succeeded"){toast(job.message);await Promise.all([loadOverview(),loadEntries()]);renderPersistence();}if(job.state==="failed")toast(job.message,true);}}catch(error){$("#publication-status").textContent=error.message;}}
function releaseStateLabel(value){return {candidate:"Preparação técnica",approved:"Verificada",indexed:"Indexada",tested:"Testada",active:"Publicada",archived:"Anterior"}[value]||value;}
function renderReleases(releases,active){$("#releases").innerHTML=`<table><thead><tr><th>Versão</th><th>Estado</th><th>Responsável</th><th>Data</th><th>Ações</th></tr></thead><tbody>${releases.map(release=>{const actions=!readOnlyAccess&&release.state==="archived"?`<button class="secondary" data-release-action="rollback" data-release="${h(release.release_id)}">Reverter</button>`:"";return `<tr><td><strong>${h(release.release_id)}</strong>${release.release_id===active?'<br><span class="active-release">Ativa</span>':""}<br><small>${h(release.description||"")}</small></td><td>${h(releaseStateLabel(release.state))}</td><td>${h(release.approved_by||release.prepared_by||"—")}</td><td>${h(formatDate(release.created_at))}</td><td class="table-actions">${actions}</td></tr>`;}).join("")}</tbody></table>`;document.querySelectorAll("[data-release-action]").forEach(node=>node.addEventListener("click",()=>releaseAction(node.dataset.release)));}
async function releaseAction(id){if(!confirm(`Reverter a aplicação pública para ${id}?`))return;try{await api(`/api/editorial/releases/${encodeURIComponent(id)}/rollback`,{method:"POST",body:JSON.stringify({actor:actor(),comment:`Reversão para ${id}`})});publicationTimer=setInterval(loadPublication,1000);loadPublication();}catch(error){toast(error.message,true);}}

// Administração do acesso temporário só de leitura
function readonlyShareUrl(key){return `${location.origin}${editorialBasePath}/?x=${encodeURIComponent(key)}`;}
function renderReadonlyKeyStatus(status){$("#readonly-key-status").textContent=status.enabled?`Acesso ativo · chave ${status.hint||"configurada"}. Guardar outra chave invalida a atual.`:"Acesso por chave desativado.";$("#disable-readonly-key").disabled=!status.enabled;}
async function loadReadonlyKeyStatus(){if(readOnlyAccess)return;try{renderReadonlyKeyStatus(await api("/api/editorial/admin/access-key"));}catch(error){$("#readonly-key-status").textContent=error.message;}}
$("#generate-readonly-key").addEventListener("click",()=>{const bytes=new Uint8Array(24);crypto.getRandomValues(bytes);const key=btoa(String.fromCharCode(...bytes)).replaceAll("+","-").replaceAll("/","_").replaceAll("=","");$("#readonly-key").value=key;$("#readonly-url").value=readonlyShareUrl(key);$("#readonly-access-url").hidden=false;});
$("#save-readonly-key").addEventListener("click",async()=>{const key=$("#readonly-key").value.trim();if(!key){toast("Introduza uma chave ou use Desativar acesso.",true);return;}try{const status=await api("/api/editorial/admin/access-key",{method:"POST",body:JSON.stringify({actor:actor(),key})});renderReadonlyKeyStatus(status);$("#readonly-url").value=readonlyShareUrl(key);$("#readonly-access-url").hidden=false;$("#readonly-key").value="";toast("Chave de acesso só de leitura ativada.");}catch(error){toast(error.message,true);}});
$("#disable-readonly-key").addEventListener("click",async()=>{if(!confirm("Desativar o acesso por chave e invalidar imediatamente as sessões de avaliação?"))return;try{const status=await api("/api/editorial/admin/access-key",{method:"POST",body:JSON.stringify({actor:actor(),key:""})});renderReadonlyKeyStatus(status);$("#readonly-key").value="";$("#readonly-access-url").hidden=true;toast("Acesso por chave desativado.");}catch(error){toast(error.message,true);}});
$("#copy-readonly-url").addEventListener("click",async()=>{try{await navigator.clipboard.writeText($("#readonly-url").value);toast("URL copiado.");}catch{toast("Não foi possível copiar automaticamente; selecione o URL.",true);}});

// Relatório de auditoria
let lastAuditReport = null;
const AUDIT_OPERATION_LABELS = {
  BULK_PUBLICATION:"Publicar entradas em lote",
  BATCH_IMPORT:"Importar entradas",
  FULL_IMPORT:"Substituir dados pelo XML",
  CANONICAL_XML_SAVED:"Guardar TEI/XML",
  CONTROLLED_VALUE:"Alterar valor controlado",
  CONTROLLED_VALUE_CREATE:"Criar valor controlado",
  CONTROLLED_VALUE_DELETE:"Apagar valor controlado",
  CONTROLLED_VALUE_MERGE:"Unir valores controlados",
  ENTRY_EDIT:"Editar entrada",
  EXTERNAL_SOURCE_PUBLICATION:"Alterar publicação de fonte externa",
  RELEASE_APPROVED:"Aprovar versão",
  RELEASE_PREPARED:"Preparar versão",
  RELEASE_PUBLISHED:"Publicar versão",
  RELEASE_ROLLBACK:"Reverter versão",
  RELEASE_STATE:"Alterar estado da versão",
  REVISION_RESTORE:"Repor revisão",
  PUBLICATION_SELECTION_ADD:"Adicionar à seleção da publicação",
  PUBLICATION_SELECTION_REMOVE:"Retirar da seleção da publicação",
  READONLY_KEY_ENABLED:"Ativar acesso de avaliação",
  READONLY_KEY_DISABLED:"Desativar acesso de avaliação",
  VALIDATION_AUTO_FIX:"Corrigir validação automaticamente",
  VALIDATION_ERROR_WAIVED:"Dispensar erro de validação",
  WORKFLOW:"Alterar estado da entrada",
};
function auditOperationLabel(event) {
  if (event.event_type === "PUBLICATION_SELECTION") return event.resulting_state === "selected" ? "Adicionar à seleção da publicação" : "Retirar da seleção da publicação";
  return AUDIT_OPERATION_LABELS[event.event_type] || event.event_type;
}
function auditState(value) {
  if (!value) return "—";
  return {selected:"Selecionada",unselected:"Não selecionada",pending:"Não selecionada",candidate:"Em preparação",approved:"Aprovada",indexed:"Indexada",tested:"Testada",active:"Ativa",archived:"Anterior",synchronized:"Sincronizado",enabled:"Ativa",deferred:"Adiada",authorized:"Autorizado",obsolete:"Obsoleto",unmapped:"Por mapear"}[value] || workflowLabel(value);
}
function auditStateChange(event) {
  if (String(event.event_type||"").startsWith("PUBLICATION_SELECTION")) return "—";
  const previous=auditState(event.previous_state), resulting=auditState(event.resulting_state);
  return previous === "—" ? resulting : resulting === "—" ? previous : `${previous} → ${resulting}`;
}
function auditComment(event) {
  const comment=String(event.comment||"").trim();
  if (!comment) return "—";
  if (String(event.event_type||"").startsWith("PUBLICATION_SELECTION")) return "—";
  if (event.event_type === "WORKFLOW" && /^(Transição para|Decisão de publicação:)/.test(comment)) return "—";
  if (event.event_type === "REVISION_RESTORE" && /^Reposição da revisão /.test(comment)) return "—";
  return comment;
}
function auditSubject(event) {
  if (event.subject_type === "entry" && event.subject_id) return `<a class="audit-entry-link" href="?entry=${encodeURIComponent(event.subject_id)}#entries-panel" data-audit-entry="${h(event.subject_id)}" data-audit-lemma="${h(event.subject_label||"")}"><strong>${h(event.subject_label||"Entrada sem lema")}</strong><small>${h(event.subject_id)}</small></a>`;
  return `<span class="audit-subject"><strong>${h(event.subject_label||"—")}</strong>${event.subject_id&&event.subject_id!=="active-dataset"?`<small>${h(event.subject_id)}</small>`:""}</span>`;
}
async function openAuditEntry(event,id,lemma) {
  event.preventDefault();
  activateView("entries-panel",false);
  $("#query").value=lemma && lemma!=="Entrada sem lema" ? lemma : "";
  setActiveAlphabet("");
  await loadEntries();
  await showEntry(id);
  const url=new URL(window.location.href);url.searchParams.set("entry",id);url.hash="entries-panel";history.replaceState({},"",url);
}
function bindAuditEntryLinks() { document.querySelectorAll("[data-audit-entry]").forEach(node=>node.addEventListener("click",event=>openAuditEntry(event,node.dataset.auditEntry,node.dataset.auditLemma))); }
function renderValidationDashboard(report) {
  const rules=report.validation_rules||[];
  const occurrences=rules.reduce((total,item)=>total+Number(item.occurrences||0),0);
  $("#validation-dashboard-summary").textContent=`${n(occurrences)} ocorrências em ${n(report.validation_entries||0)} entradas distintas. Uma entrada pode ter mais do que um problema.`;
  $("#validation-rules").innerHTML=rules.length?`<table><thead><tr><th>Problema</th><th>Severidade</th><th>Ocorrências</th><th>Entradas afetadas</th><th>Exemplo</th><th>Ação</th></tr></thead><tbody>${rules.map(item=>`<tr><td><strong>${h(problemLabel(item.rule_code))}</strong><small class="validation-rule-code">${h(item.rule_code)}</small></td><td>${h({error:"Erro",warning:"Aviso",info:"Informação"}[item.severity]||item.severity)}</td><td>${n(item.occurrences)}</td><td>${n(item.entries)}</td><td>${h(item.example||"—")}</td><td><button type="button" class="secondary compact" data-problem-filter="${h(item.rule_code)}">Ver entradas</button></td></tr>`).join("")}</tbody></table>`:"<p>Sem problemas registados.</p>";
  document.querySelectorAll("[data-problem-filter]").forEach(node=>node.addEventListener("click",async()=>{activateView("entries-panel",false);$("#query").value="";setActiveAlphabet("");$("#severity-filter").value=node.dataset.problemFilter;await loadEntries();}));
}
function toggleValidationDashboard(show=true){$("#validation-dashboard").hidden=!show;if(show&&lastAuditReport){renderValidationDashboard(lastAuditReport);$("#validation-dashboard").scrollIntoView({behavior:"smooth",block:"start"});}}
async function loadAudit(){
  try {
    const report=await api("/api/editorial/audit"); lastAuditReport=report;
    const sum=items=>(items||[]).reduce((total,item)=>total+Number(item.count||0),0);
    const stateText=(report.states||[]).map(item=>`${workflowLabel(item.value)}: ${n(item.count)}`).join(" · ")||"—";
    const issueText=(report.validation_issues||[]).map(item=>`${item.value}: ${n(item.count)}`).join(" · ")||"Sem problemas registados";
    $("#audit-summary").innerHTML=`<section class="card"><h2>Conjunto de trabalho</h2><strong>${n(sum(report.resources))} entradas</strong><p>${h(stateText)}</p></section><section class="card"><h2>Persistência</h2><strong>${report.persistence?.has_unsaved_changes?"Alterações por guardar":"TEI/XML sincronizado"}</strong><p>Última salvaguarda: ${h(formatDate(report.persistence?.last_saved_at))}</p></section><button id="open-validation-dashboard" class="card audit-summary-action" type="button"><h2>Validação</h2><strong>${n(sum(report.validation_issues))} ocorrências</strong><p>${n(report.validation_entries||0)} entradas afetadas · ${h(issueText)}</p><small>Ver problemas por tipo</small></button><section class="card"><h2>Publicações</h2><strong>${n((report.releases||[]).length)} versões no relatório</strong><p>Relatório gerado em ${h(formatDate(report.generated_at))}</p></section>`;
    $("#audit-events").innerHTML=`<table><thead><tr><th>Data</th><th>Responsável</th><th>Entrada</th><th>Operação</th><th>Estado</th><th>Comentário</th></tr></thead><tbody>${(report.recent_events||[]).map(event=>`<tr><td>${h(formatDate(event.created_at))}</td><td>${h(event.actor)}</td><td>${auditSubject(event)}</td><td>${h(auditOperationLabel(event))}</td><td>${h(auditStateChange(event))}</td><td>${h(auditComment(event))}</td></tr>`).join("")}</tbody></table>`;
    bindAuditEntryLinks();
    $("#open-validation-dashboard").addEventListener("click",()=>toggleValidationDashboard(true));
    if(!$("#validation-dashboard").hidden)renderValidationDashboard(report);
  } catch(error){$("#audit-events").textContent=error.message;}
}
$("#refresh-audit").addEventListener("click",loadAudit);
$("#close-validation-dashboard").addEventListener("click",()=>toggleValidationDashboard(false));
function downloadAudit(content,type,extension){const blob=new Blob([content],{type});const link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=`auditoria-acl-${new Date().toISOString().slice(0,10)}.${extension}`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}
function csvCell(value){return `"${String(value??"").replace(/"/g,'""')}"`;}
$("#download-audit").addEventListener("click",()=>{if(!lastAuditReport){toast("Atualize primeiro o relatório.",true);return;}downloadAudit(JSON.stringify(lastAuditReport,null,2),"application/json","json");});
$("#download-audit-csv").addEventListener("click",()=>{if(!lastAuditReport){toast("Atualize primeiro o relatório.",true);return;}const rows=[["Data","Responsável","Entrada","Operação","Estado","Comentário"],...(lastAuditReport.recent_events||[]).map(event=>[formatDate(event.created_at),event.actor,event.subject_type==="entry"?`${event.subject_label||"Entrada sem lema"} [${event.subject_id}]`:event.subject_label||"—",auditOperationLabel(event),auditStateChange(event),auditComment(event)])];downloadAudit("\ufeff"+rows.map(row=>row.map(csvCell).join(";")).join("\r\n"),"text/csv;charset=utf-8","csv");});

setHelpMode(helpMode);
loadAccessSession().then(()=>Promise.all([loadOverview(),loadControlledValues()])).then(async()=>{const requested=new URLSearchParams(window.location.search).get("entry");if(requested){activateView("entries-panel",false);await loadEntries();await showEntry(requested);}}).catch(error=>toast(error.message,true));
