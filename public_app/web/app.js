const form = document.querySelector("#search-form");
const statusNode = document.querySelector("#status");
const results = document.querySelector("#results");

fetch("/api/v1/releases/current")
  .then(response => response.json())
  .then(data => {
    statusNode.textContent = data.available
      ? `Release ativa: ${data.release_id} · ${data.manifest.counts.entries.toLocaleString("pt-PT")} entradas`
      : "Ainda não existe uma release local ativa.";
  })
  .catch(() => { statusNode.textContent = "Não foi possível consultar a release."; });

form.addEventListener("submit", async event => {
  event.preventDefault();
  const data = new FormData(form);
  const params = new URLSearchParams({q: data.get("q") || ""});
  if (data.get("resource")) params.set("resource", data.get("resource"));
  statusNode.textContent = "A pesquisar…";
  results.replaceChildren();
  try {
    const response = await fetch(`/api/v1/search?${params}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Pesquisa indisponível");
    const hits = payload.results.flatMap(group => group.hits || []);
    statusNode.textContent = `${hits.length.toLocaleString("pt-PT")} resultados carregados`;
    for (const hit of hits) {
      const article = document.createElement("article");
      const title = document.createElement("h2");
      title.textContent = hit.lemma || "(sem lema)";
      const meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = hit.resource === "dictionary" ? "Dicionário" : "Vocabulário";
      const definition = document.createElement("p");
      definition.className = "definition";
      definition.textContent = hit.definitions_text || "";
      article.append(title, meta, definition);
      results.append(article);
    }
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

