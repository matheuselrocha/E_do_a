#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualizador de preços — Enxoval CBMDF
=====================================

Entra em cada link de produto (Mercado Livre / Shopee / Magazine Luiza), espera o
DOM carregar, extrai o preço e grava o valor numérico de volta. Dois destinos:

  * planilha (padrão até a virada): lê e grava a aba "Compras Online";
  * supabase (depois da virada):    lê `produtos_online` e grava pela função
    `robo_preco_produto` (produto + itens ligados; histórico com origem "robo").

Cada link é processado dentro de um try/except: se o preço não for encontrado
(seletor mudou, site bloqueou, link quebrado...), o script anota o motivo e
segue para o próximo — nunca quebra no meio.

Uso:
    python3 atualizar_precos.py                 # roda tudo (headless)
    python3 atualizar_precos.py --headful       # mostra o navegador (debug)
    python3 atualizar_precos.py --limit 5       # só as 5 primeiras linhas (teste)
    python3 atualizar_precos.py --so-mercadolivre   # só uma plataforma
    python3 atualizar_precos.py --dry-run       # extrai mas NÃO grava na planilha
    python3 atualizar_precos.py --destino supabase --simular   # lê do banco, NÃO grava
    python3 atualizar_precos.py --destino supabase             # grava no banco (só depois da virada)

Config por variáveis de ambiente (todas opcionais, têm padrão):
    SHEET_ID     -> ID da planilha         (padrão: a planilha do projeto)
    SHEET_TAB    -> nome da aba            (padrão: "Compras Online")
    GOOGLE_CREDENTIALS -> caminho do .json (padrão: ./credenciais.json)
    SUPABASE_URL / SUPABASE_SERVICE_ROLE -> destino supabase (a chave NUNCA vai para o git;
                          sem a variável, tenta ler de migracao/.env)
"""

import os
import re
import sys
import time
import argparse
import json
import urllib.request
import urllib.error

# ------------------------------------------------------------------ CONFIG ----
SHEET_ID = os.environ.get("SHEET_ID", "1Yi-czpyFQeRk58tUx95wkevEG3lkwtcZQANR1KspV8U")
SHEET_TAB = os.environ.get("SHEET_TAB", "Compras Online")
CRED_PATH = os.environ.get(
    "GOOGLE_CREDENTIALS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "credenciais.json"),
)

# Nomes de coluna esperados no cabeçalho (linha 1). Se a planilha mudar os
# títulos, ajuste aqui.
COL_LINK = "Link do Produto"
COL_PRECO = "Preço"
COL_PLATAFORMA = "Plataforma"
COL_PRODUTO = "Produto"

SUPA_URL = os.environ.get("SUPABASE_URL", "https://zjvxatpdajxdtpmifkxg.supabase.co").rstrip("/")

PAGE_TIMEOUT_MS = 45_000   # tempo máximo esperando a página abrir
PRICE_TIMEOUT_S = 20       # tempo máximo tentando achar o preço na página
PAUSE_ENTRE_LINKS_S = 1.5  # respiro entre requisições (educação + anti-bloqueio)

# ------------------------------------------------------ EXTRATORES POR SITE ---
# Cada extrator roda dentro da página (JS) e devolve o TEXTO cru do preço, ou
# null. O tratamento numérico é feito depois, em parse_brl().

JS_MERCADOLIVRE = r"""
() => {
  // A página de afiliado (/social/) lista vários produtos, o alvo em 1º lugar.
  // Cada produto tem DOIS preços com a MESMA classe .andes-money-amount__fraction:
  //   - riscado/antigo: dentro de <s class="... andes-money-amount--previous">
  //   - efetivo/atual : <span class="... andes-money-amount--cents-superscript">
  // Regra: pular todo preço marcado como "previous" (riscado) e pegar a 1ª
  // fração restante -> é o preço EFETIVO do produto-alvo. (Só a parte inteira.)
  // Devolve também o TÍTULO do anúncio de onde o preço saiu: se o link vencer, o
  // ML manda para a lista do perfil e o 1º preço é de OUTRO produto — o Python
  // confere o título contra o nome da planilha antes de aceitar.
  const tituloDe = (el) => {
    const h1 = document.querySelector('h1.ui-pdp-title, .ui-pdp-title');
    if (h1 && h1.textContent.trim() && !location.pathname.includes('/social/')) return h1.textContent.trim();
    let c = el;
    for (let i = 0; i < 10 && c; i++) {
      c = c.parentElement;
      const t = c && c.querySelector('a[title], h2, h3, [class*="title"]');
      if (t) return (t.getAttribute('title') || t.textContent || '').trim();
    }
    return '';
  };
  const amounts = Array.from(document.querySelectorAll('.andes-money-amount'));
  for (const a of amounts) {
    const cls = (a.className && a.className.toString) ? a.className.toString() : '';
    if (/andes-money-amount--previous/.test(cls)) continue;      // pula riscado
    if (a.tagName && a.tagName.toLowerCase() === 's') continue;  // pula <s>
    const frac = a.querySelector('.andes-money-amount__fraction');
    if (frac && frac.textContent.trim()) return { preco: frac.textContent.trim(), titulo: tituloDe(a) };
  }
  // Fallback: metadado estruturado, se existir.
  const meta = document.querySelector('meta[itemprop="price"]');
  if (meta && meta.content) return { preco: meta.content, titulo: tituloDe(meta) };
  return null;
}
"""

JS_SHOPEE = r"""
() => {
  // 1) Classe pedida no escopo (ATENÇÃO: hash da Shopee muda de tempos em tempos).
  let el = document.querySelector('.pyzxvq.pw3J3G');
  if (el && el.textContent) return el.textContent;
  // 2) Metadado estruturado.
  const og = document.querySelector('meta[property="product:price:amount"]');
  if (og && og.content) return og.content;
  // 3) JSON-LD (schema.org Product/Offer).
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const j = JSON.parse(s.textContent);
      const arr = Array.isArray(j) ? j : [j];
      for (const it of arr) {
        const off = it.offers || (it['@graph'] || []).map(x => x.offers).find(Boolean);
        const p = off && (off.price || off.lowPrice);
        if (p) return String(p);
      }
    } catch (e) {}
  }
  return null;
}
"""

JS_MAGALU = r"""
() => {
  // 1) Preço visível principal do Magalu.
  let el = document.querySelector('[data-testid="price-value"]');
  if (el && el.textContent) return el.textContent;
  // 2) Seletor pedido no escopo, por PREFIXO (o número final do id é dinâmico).
  el = document.querySelector('span.sr-only[id^="price-final-label"]')
    || document.querySelector('[id^="price-final-label"]');
  if (el && el.textContent) return el.textContent;
  // 3) Metadado estruturado.
  const meta = document.querySelector('meta[itemprop="price"], meta[property="product:price:amount"]');
  if (meta && meta.content) return meta.content;
  return null;
}
"""

EXTRATORES = {
    "mercado livre": JS_MERCADOLIVRE,
    "mercadolivre": JS_MERCADOLIVRE,
    "shopee": JS_SHOPEE,
    "magazine luiza": JS_MAGALU,
    "magalu": JS_MAGALU,
}


def detectar_plataforma(plataforma_celula: str, url: str) -> str:
    """Descobre a plataforma pela coluna C; se vazia, cai pra URL."""
    p = (plataforma_celula or "").strip().lower()
    if p in EXTRATORES:
        return p
    u = (url or "").lower()
    if "mercadolivre" in u or "mercadolibre" in u or "/social/" in u:
        return "mercado livre"
    if "shopee" in u:
        return "shopee"
    if "magazine" in u or "magalu" in u or "onelink" in u:
        return "magazine luiza"
    return p  # pode não bater com nenhum extrator -> tratado adiante


# ---------------------------------------------------- TRATAMENTO DE STRING ----
def parse_brl(texto: str):
    """
    Converte texto de preço brasileiro em float.
      "R$ 1.234,56" -> 1234.56
      "1.234"        -> 1234.0   (fração ML já vem sem centavos)
      "10,90"        -> 10.9
      "10.9"         -> 10.9     (metadado costuma vir com ponto decimal)
    Retorna None se não achar número.
    """
    if texto is None:
        return None
    s = str(texto)
    s = s.replace("\xa0", " ").replace("&nbsp;", " ")
    s = re.sub(r"(?i)pre[çc]o", "", s)   # remove a palavra "Preço" (label Magalu)
    s = s.replace("R$", "").strip()

    # Pega só o primeiro "número" plausível (dígitos, pontos e vírgulas).
    m = re.search(r"\d[\d\.\,]*", s)
    if not m:
        return None
    num = m.group(0)

    if "," in num:
        # Formato BR: ponto = milhar, vírgula = decimal.
        num = num.replace(".", "").replace(",", ".")
    else:
        # Sem vírgula. Se houver ponto, decidir se é milhar ou decimal.
        if num.count(".") == 1:
            inteiro, frac = num.split(".")
            if len(frac) == 3 and len(inteiro) <= 3:
                # "1.234" -> milhar (típico da fração do Mercado Livre)
                num = inteiro + frac
            # senão, "10.90" -> decimal, mantém
        else:
            # múltiplos pontos = separadores de milhar
            num = num.replace(".", "")
    try:
        return round(float(num), 2)
    except ValueError:
        return None


# ------------------------------------------------------- CONFERÊNCIAS ---------
ULTIMO_TITULO = [""]          # título do anúncio de onde saiu o último preço (ML)
VARIACAO_MAX = 0.6            # >60% pra cima ou pra baixo = suspeito, não grava

# palavras genéricas não contam (muitos anúncios do perfil têm "tático", "preto"...)
_GENERICAS = {"com", "para", "kit", "par", "pares", "unidade", "unidades", "tatica", "tatico", "taticas", "taticos",
              "militar", "preto", "preta", "masculina", "masculino", "feminina", "feminino", "esportiva", "esportivo"}

def _tokens(s):
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    out = set()
    for t in re.findall(r"[a-z0-9]+", s):
        if len(t) < 3 or t in _GENERICAS: continue
        out.add(re.sub(r"(oes|aes|es|s)$", "", t) if len(t) > 4 else t)   # plural simples: luvas = luva
    return out

def mesmo_produto(nome_planilha, titulo):
    """True se o título do anúncio parece ser o produto da planilha (ao menos 40%
    das palavras distintivas do nome da planilha aparecem no título)."""
    a, b = _tokens(nome_planilha), _tokens(titulo)
    if not a or not b: return True          # sem dado para comparar: não bloqueia
    return len(a & b) / len(a) >= 0.4

def preco_atual(txt):
    try: return parse_brl(txt) if str(txt or "").strip() else None
    except Exception: return None


# ------------------------------------------------------------ SCRAPING --------
def extrair_preco(page, plataforma: str):
    """Abre a URL já carregada e tenta extrair o preço, com pequenas retentativas."""
    js = EXTRATORES.get(plataforma)
    if not js:
        return None, "plataforma desconhecida (sem extrator)"

    fim = time.time() + PRICE_TIMEOUT_S
    ultimo = None
    while time.time() < fim:
        try:
            ultimo = page.evaluate(js)
        except Exception as e:  # noqa: BLE001
            ultimo = None
        if ultimo:
            texto = ultimo.get("preco") if isinstance(ultimo, dict) else ultimo
            valor = parse_brl(texto)
            if valor is not None:
                ULTIMO_TITULO[0] = ultimo.get("titulo", "") if isinstance(ultimo, dict) else ""
                return valor, None
        time.sleep(1)
    if ultimo:
        return None, f"achou texto mas não virou número: {ultimo!r}"
    return None, "preço não encontrado (seletor/erro/bloqueio)"


# ------------------------------------------------------------ FONTES ----------
# Cada fonte devolve (produtos, gravar):
#   produtos = [{"ref": ..., "url", "plataforma", "nome", "atual"}]
#   gravar(lista de (ref, valor, atual)) -> grava e imprime o resultado
def fonte_planilha(args):
    try:
        import gspread
        from gspread.cell import Cell
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("Falta instalar dependências. Rode:  pip install -r requirements.txt")
    if not os.path.exists(CRED_PATH):
        sys.exit(
            f"Credencial não encontrada em: {CRED_PATH}\n"
            "Veja o README (seção 'Setup da conta de serviço')."
        )
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(CRED_PATH, scopes=scopes)
    gc = gspread.authorize(creds)
    try:
        ws = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
    except gspread.exceptions.APIError as e:
        sys.exit(
            f"Não consegui abrir a planilha/aba. Confira se ela foi COMPARTILHADA "
            f"como Editor com o e-mail da conta de serviço.\nDetalhe: {e}"
        )
    dados = ws.get_all_values()
    if not dados:
        sys.exit("Planilha vazia.")
    cabecalho = [c.strip() for c in dados[0]]

    def idx(nome):
        try:
            return cabecalho.index(nome)
        except ValueError:
            sys.exit(f"Coluna '{nome}' não encontrada no cabeçalho: {cabecalho}")

    i_link, i_preco, i_plat = idx(COL_LINK), idx(COL_PRECO), idx(COL_PLATAFORMA)
    i_prod = idx(COL_PRODUTO) if COL_PRODUTO in cabecalho else i_plat
    cel = lambda ln, i: (ln[i] if i < len(ln) else "")
    produtos = []
    for r, ln in enumerate(dados[1:], start=2):  # linha 2 = 1ª de dados
        url = cel(ln, i_link).strip()
        if not url:
            continue
        produtos.append({"ref": r, "url": url, "plataforma": detectar_plataforma(cel(ln, i_plat), url),
                         "nome": cel(ln, i_prod), "atual": preco_atual(cel(ln, i_preco))})
    print(f"Destino: planilha · aba {SHEET_TAB} | {len(dados)-1} linhas de dados\n")

    def gravar(atualizacoes):
        cells = [Cell(row=r, col=i_preco + 1, value=v) for r, v, _ in atualizacoes]
        ws.update_cells(cells, value_input_option="RAW")
        print(f"Planilha atualizada: {len(atualizacoes)} preços gravados na coluna '{COL_PRECO}'.")
    return produtos, gravar


def _chave_supabase():
    k = os.environ.get("SUPABASE_SERVICE_ROLE", "").strip()
    if not k:   # uso local: migracao/.env (no .gitignore)
        env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "migracao", ".env")
        if os.path.exists(env):
            for ln in open(env, encoding="utf-8"):
                m = re.match(r"\s*(?:export\s+)?SUPABASE_SERVICE_ROLE\s*=\s*['\"]?([^'\"\s]+)", ln)
                if m:
                    k = m.group(1)
    if not k:
        sys.exit("Defina SUPABASE_SERVICE_ROLE (chave service_role) para o destino supabase.")
    return k


def _ssl_ctx():
    import ssl
    try:
        import certifi  # vem com o gspread/requests; resolve o Python do macOS sem certificados
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _supa(metodo, caminho, corpo=None, chave=""):
    req = urllib.request.Request(SUPA_URL + caminho, method=metodo,
                                 data=None if corpo is None else json.dumps(corpo).encode("utf-8"),
                                 headers={"apikey": chave, "Authorization": "Bearer " + chave,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        try:
            det = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            det = {}
        raise RuntimeError(f"{det.get('code', e.code)}: {det.get('message', e.reason)}") from None


def fonte_supabase(args):
    chave = _chave_supabase()
    rows = _supa("GET", "/rest/v1/produtos_online?select=id,nome,link_produto,preco,lojas(nome)&order=nome", chave=chave)
    produtos = []
    for p in rows:
        url = (p.get("link_produto") or "").strip()
        if not url:
            continue
        loja = ((p.get("lojas") or {}).get("nome") or "")
        produtos.append({"ref": p["id"], "url": url, "plataforma": detectar_plataforma(loja, url),
                         "nome": p.get("nome") or "", "atual": float(p["preco"]) if p.get("preco") is not None else None})
    fonte = _supa("POST", "/rest/v1/rpc/fonte_dados", {}, chave=chave)
    print(f"Destino: Supabase · {len(rows)} produtos no catálogo · fonte de dados agora: {fonte}\n")
    if fonte != "painel" and not args.dry_run:
        sys.exit("A fonte de dados ainda é a planilha: o banco recusaria (grave na planilha até a virada,\n"
                 "ou use --simular para só conferir).")

    def gravar(atualizacoes):
        gravados = sem_mudanca = 0
        for pid, v, atual in atualizacoes:
            if atual is not None and round(v, 2) == round(atual, 2):
                sem_mudanca += 1
                continue
            try:
                r = _supa("POST", "/rest/v1/rpc/robo_preco_produto", {"p_id": pid, "p_preco": v}, chave=chave)
                gravados += 1
                print(f"  gravado {pid[:8]}: {r.get('antes')} -> {r.get('depois')} (itens ligados: {r.get('itens')})")
            except RuntimeError as e:
                print(f"  NÃO gravado {pid[:8]}: {e}")
                if str(e).startswith("ENX01"):
                    break
        print(f"Supabase atualizado: {gravados} preços gravados | {sem_mudanca} sem mudança.")
    return produtos, gravar


# ------------------------------------------------------------ EXECUÇÃO --------
def processar(args):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Falta o Playwright. Rode:  pip install -r requirements.txt && playwright install chromium")

    produtos, gravar = (fonte_supabase if args.destino == "supabase" else fonte_planilha)(args)

    # Padrão: só Mercado Livre (plataforma majoritária). Shopee/Magalu são
    # pulados. Use --todas para processar todas as plataformas, ou as flags
    # --so-* para forçar uma específica.
    filtro = "mercado livre"
    if args.todas:
        filtro = None
    elif args.so_shopee:
        filtro = "shopee"
    elif args.so_magalu:
        filtro = "magazine luiza"
    elif args.so_mercadolivre:
        filtro = "mercado livre"

    atualizacoes = []          # (ref, valor, atual)
    ok = falhas = pulados = 0
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not args.headful)
        ctx = navegador.new_context(
            locale="pt-BR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()

        processadas = 0
        for n, pr in enumerate(produtos, start=1):
            if args.limit and processadas >= args.limit:
                break
            url, plataforma, nome_inteiro, atual = pr["url"], pr["plataforma"], pr["nome"], pr["atual"]
            if filtro and plataforma != filtro:
                pulados += 1
                continue

            processadas += 1
            ref = pr["ref"] if isinstance(pr["ref"], int) else n
            etiqueta = f"[{ref:>3}] {plataforma[:12]:<12} {nome_inteiro[:45]:<45}"
            ULTIMO_TITULO[0] = ""
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                valor, erro = extrair_preco(page, plataforma)
                # link de afiliado vencido/produto indisponível: o ML manda para a LISTA do
                # perfil (/social/<perfil>/lists) e o 1º preço é de outro produto
                if valor is not None and re.search(r"/social/[^/?#]+/lists", page.url):
                    valor, erro = None, "link levou à lista do perfil de afiliado (link vencido ou produto indisponível)"
                elif valor is not None and not mesmo_produto(nome_inteiro, ULTIMO_TITULO[0]):
                    valor, erro = None, f"anúncio diferente do produto: {ULTIMO_TITULO[0][:60]!r}"
                elif valor is not None and atual and abs(valor - atual) / atual > VARIACAO_MAX and not args.aceitar_variacao:
                    valor, erro = None, f"variação suspeita (R$ {atual:.0f} -> R$ {valor:.0f}); mantido o preço atual"
            except Exception as e:  # noqa: BLE001 — nunca deixa a execução quebrar
                valor, erro = None, f"erro ao abrir: {type(e).__name__}"

            if valor is not None:
                atualizacoes.append((pr["ref"], valor, atual))
                ok += 1
                print(f"{etiqueta} -> R$ {valor:.2f}" + (f"  (era R$ {atual:.2f})" if atual is not None and round(atual, 2) != round(valor, 2) else ""))
            else:
                falhas += 1
                print(f"{etiqueta} -> FALHOU ({erro})")

            time.sleep(PAUSE_ENTRE_LINKS_S)

        navegador.close()

    print()
    if not atualizacoes:
        print("Nada extraído — nada foi alterado.")
    elif args.dry_run:
        mudam = sum(1 for _, v, a in atualizacoes if a is None or round(a, 2) != round(v, 2))
        print(f"[SIMULAÇÃO] {len(atualizacoes)} valores extraídos ({mudam} mudariam), mas NÃO gravei nada.")
    else:
        gravar(atualizacoes)

    print(f"\nResumo: {ok} ok | {falhas} falhas | {pulados} pulados")


def main():
    ap = argparse.ArgumentParser(description="Atualiza os preços do catálogo online (planilha ou Supabase).")
    ap.add_argument("--destino", choices=["planilha", "supabase"], default="planilha",
                    help="onde ler e gravar (padrão: planilha; supabase depois da virada)")
    ap.add_argument("--headful", action="store_true", help="mostra o navegador")
    ap.add_argument("--limit", type=int, default=0, help="processa só N linhas (teste)")
    ap.add_argument("--aceitar-variacao", action="store_true",
                    help=f"grava mesmo quando o preço muda mais de {int(VARIACAO_MAX*100)}%% (normalmente fica o atual)")
    ap.add_argument("--dry-run", "--simular", dest="dry_run", action="store_true", help="extrai mas não grava")
    ap.add_argument("--todas", action="store_true",
                    help="processa TODAS as plataformas (padrão é só Mercado Livre)")
    ap.add_argument("--so-mercadolivre", action="store_true")
    ap.add_argument("--so-shopee", action="store_true")
    ap.add_argument("--so-magalu", action="store_true")
    processar(ap.parse_args())


if __name__ == "__main__":
    main()
