/* =========================================================================
   Código comum ao painel da loja (painel.html) e à administração (admin.html):
   cliente Supabase, utilitários e os blocos de métricas (cards, gráfico, tabelas).
   As duas páginas usam a mesma sessão (storageKey), separada da do site.
   ========================================================================= */
const SUPA_URL = "https://zjvxatpdajxdtpmifkxg.supabase.co";
const SUPA_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpqdnhhdHBkYWp4ZHRwbWlma3hnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk1MTM5ODYsImV4cCI6MjEwNTA4OTk4Nn0.Y23MtmFlyQrk1sL9FSmem5SXPSUAQX2l__PxHNKrvnU";
// tipo do link de e-mail (convite / recuperação) — lido antes do cliente consumir o #hash
const LINK_EMAIL = (() => { const h = new URLSearchParams(location.hash.slice(1)); return { tipo: h.get("type"), erro: h.get("error_code") || h.get("error") }; })();
const sb = window.supabase.createClient(SUPA_URL, SUPA_KEY, {
  auth: { storageKey: "enxoval-painel-auth", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
});

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const norm = s => String(s||"").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g,"");
const brl = v => "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 });

function aviso(txt, erro){
  document.querySelectorAll(".toast").forEach(t=>t.remove());
  const t = document.createElement("div"); t.className = "toast" + (erro?" erro":""); t.setAttribute("role","status"); t.textContent = txt;
  document.body.appendChild(t); setTimeout(()=>t.remove(), 4500);
}

/* ---------------- métricas (migracao/06_metricas.sql e 07_admin.sql) ---------------- */
const TIPOS = {
  whatsapp:   ["WhatsApp", "pedidos enviados"],
  carrinho:   ["Carrinho", "itens adicionados"],
  pdf:        ["PDF", "orçamentos baixados"],
  contato:    ["Contatos", "cliques no WhatsApp, Instagram e mapa"],
  loja_unica: ["Loja única", "vezes escolhida"]
};
const inteiro = n => Number(n||0).toLocaleString("pt-BR");
const diaBR = d => d.slice(8,10) + "/" + d.slice(5,7);
const dataLonga = d => d.slice(8,10) + "/" + d.slice(5,7) + "/" + d.slice(0,4);
const somaDias = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0,10); };
const hojeBR = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Sao_Paulo" });   // AAAA-MM-DD
const pessoasTxt = n => `${inteiro(n)} pessoa${n!==1?"s":""}`;

function cardHTML(m, tipo){
  const t = m.totais || {}, a = m.anterior || {};
  let ev = 0, pes = 0, ant = 0, val = 0;
  const tipos = tipo === "contato" ? ["contato","instagram","mapa"] : [tipo];
  for(const k of tipos){ ev += t[k]?.eventos||0; pes += t[k]?.pessoas||0; val += Number(t[k]?.valor||0); ant += a[k]?.eventos||0; }
  let dl = "";
  if(ant > 0){ const pc = Math.round((ev - ant) / ant * 100); if(pc) dl = `<span class="dl ${pc>0?"up":"down"}" title="Comparado aos ${m.dias} dias anteriores">${pc>0?"+":""}${pc}%</span>`; }
  const sub = (tipo === "whatsapp" || tipo === "pdf") && val ? `<b>${brl(Math.round(val))}</b> em orçamentos`
            : tipo === "contato" ? "WhatsApp, Instagram e mapa" : pessoasTxt(pes);
  return `<div class="card" title="${esc(TIPOS[tipo][1])}"><div class="rot">${TIPOS[tipo][0]}</div>
    <div class="val">${inteiro(ev)}${dl}</div><div class="sub">${sub}</div></div>`;
}
function graficoHTML(m){
  const mapa = new Map((m.por_dia||[]).map(d=>[d.dia, d]));
  const dias = []; for(let i = 0; i < m.dias; i++) dias.push(somaDias(m.inicio, i));
  const tot = d => { const x = mapa.get(d); return x ? x.whatsapp + x.carrinho + x.pdf : 0; };
  const max = Math.max(1, ...dias.map(tot));
  const barras = dias.map(d=>{
    const x = mapa.get(d) || { whatsapp:0, carrinho:0, pdf:0 }, n = tot(d);
    const txt = `${diaBR(d)}: ${n} ${n!==1?"interações":"interação"} — ${x.whatsapp} WhatsApp, ${x.carrinho} carrinho, ${x.pdf} PDF`;
    return `<div class="b ${n?"":"zero"}" data-dica="${esc(txt)}" title="${esc(txt)}"><i style="height:${Math.round(n/max*100)}%"></i></div>`;
  }).join("");
  const gap = m.dias > 120 ? 0 : m.dias > 60 ? 1 : 2;          // períodos longos: barras mais juntas
  return `<div class="bloco"><h3>Interações por dia</h3>
    <div class="graf" style="gap:${gap}px" role="img" aria-label="Interações por dia; máximo de ${max} em um dia">${barras}</div>
    <div class="eixo"><span>${diaBR(dias[0])}</span><span>máx. ${max}/dia</span><span>${diaBR(dias[dias.length-1])}</span></div>
    <div class="dica" id="dica">Toque numa barra para ver o dia.</div></div>`;
}
// cards + gráfico + itens + concursos + nota (igual para loja e admin)
function corpoMetricas(m, variosConc, extra){
  let h = `<div class="cards">${Object.keys(TIPOS).map(k=>cardHTML(m, k)).join("")}</div>` + graficoHTML(m) + (extra||"");
  if((m.itens||[]).length){
    h += `<div class="bloco"><h3>Itens mais levados ao carrinho</h3><table class="tab">
      <thead><tr><th>Item</th><th class="n">Vezes</th><th class="n">Pessoas</th></tr></thead><tbody>
      ${m.itens.map(it=>`<tr><td>${esc(it.nome)}${variosConc && it.concurso ? `<small>${esc(it.concurso)}</small>` : ""}</td><td class="n num">${inteiro(it.vezes)}</td><td class="n num">${inteiro(it.pessoas)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }
  if((m.concursos||[]).length > 1){
    h += `<div class="bloco"><h3>Por concurso</h3><table class="tab">
      <thead><tr><th>Concurso</th><th class="n">WhatsApp</th><th class="n">Carrinho</th><th class="n">PDF</th></tr></thead><tbody>
      ${m.concursos.map(c=>`<tr><td>${esc(c.nome)}</td><td class="n num">${inteiro(c.whatsapp)}</td><td class="n num">${inteiro(c.carrinho)}</td><td class="n num">${inteiro(c.pdf)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }
  h += `<p class="nota">Pessoas = aparelhos diferentes (sem identificar ninguém). De ${dataLonga(m.inicio)} a ${dataLonga(m.fim)}, horário de Brasília. Percentuais comparam com os ${m.dias} dias anteriores.</p>`;
  return h;
}
// gráfico: tocar/passar o mouse numa barra mostra o dia
document.addEventListener("click", ev=>{
  const br = ev.target.closest(".graf .b"); if(!br) return;
  document.querySelectorAll(".graf .b.on").forEach(x=>x.classList.remove("on")); br.classList.add("on"); $("#dica").textContent = br.dataset.dica;
});
document.addEventListener("mouseover", ev=>{ const br = ev.target.closest(".graf .b"); if(br && $("#dica")) $("#dica").textContent = br.dataset.dica; });
