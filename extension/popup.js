/** Popup: mostra il progresso del riempimento (letto da storage.session). */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

const STATUS_IT = {
  added: ["ok", "aggiunto"],
  unconfirmed: ["no", "da verificare"],
  not_found: ["no", "non trovato"],
  blocked: ["no", "bloccato"],
  error: ["no", "errore"],
};

function render(p) {
  const c = $("content");
  if (!p) {
    c.innerHTML = '<p class="sub">Nessun piano in corso. Apri SpesaSmart → Lista → "Riempi il carrello".</p>';
    return;
  }
  if (p.state === "needs_login" || p.state === "unsupported" || p.state === "error") {
    c.innerHTML = `<div class="msg warn">${esc(p.message || "")}</div>`;
    return;
  }
  const total = p.total || 0;
  const done = p.done || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const list = (p.results || [])
    .map((r) => {
      const [cls, label] = STATUS_IT[r.status] || ["", r.status];
      return `<li><span>${esc(r.name)}</span><span class="${cls}">${label}</span></li>`;
    })
    .join("");
  const head =
    p.state === "done"
      ? `<div class="msg info">${esc(p.message || "")}</div>`
      : `<p class="sub">${esc(p.current ? "In corso: " + p.current : "Preparazione…")}</p>`;
  c.innerHTML = `${head}<div class="bar"><div class="fill" style="width:${pct}%"></div></div><p class="sub">${done}/${total}</p><ul>${list}</ul>`;
}

chrome.runtime.sendMessage({ type: "GET_PROGRESS" }, (res) => render(res?.progress));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.progress) render(changes.progress.newValue);
});
$("reset").addEventListener("click", () => chrome.runtime.sendMessage({ type: "RESET" }, () => render(null)));
