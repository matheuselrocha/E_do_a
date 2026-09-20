#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualizador de preços — Enxoval CBMDF
=====================================

Lê a aba "Compras Online" da planilha do Google Sheets, entra em cada link de
produto (Mercado Livre / Shopee / Magazine Luiza), espera o DOM carregar,
extrai o preço e grava o valor numérico de volta na coluna "Preço" da MESMA
linha.

Cada link é processado dentro de um try/except: se o preço não for encontrado
(seletor mudou, site bloqueou, link quebrado...), o script anota o motivo e
segue para o próximo — nunca quebra no meio.

Uso:
    python3 atualizar_precos.py                 # roda tudo (headless)
    python3 atualizar_precos.py --headful       # mostra o navegador (debug)
    python3 atualizar_precos.py --limit 5       # só as 5 primeiras linhas (teste)
    python3 atualizar_precos.py --so-mercadolivre   # só uma plataforma
    python3 atualizar_precos.py --dry-run       # extrai mas NÃO grava na planilha

Config por variáveis de ambiente (todas opcionais, têm padrão):
    SHEET_ID     -> ID da planilha         (padrão: a planilha do projeto)
    SHEET_TAB    -> nome da aba            (padrão: "Compras Online")
    GOOGLE_CREDENTIALS -> caminho do .json (padrão: ./credenciais.json)
"""

import os
import re
import sys
import time
import argparse

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
  const amounts = Array.from(document.querySelectorAll('.andes-money-amount'));
  for (const a of amounts) {
    const cls = (a.className && a.className.toString) ? a.className.toString() : '';
    if (/andes-money-amount--previous/.test(cls)) continue;      // pula riscado
    if (a.tagName && a.tagName.toLowerCase() === 's') continue;  // pula <s>
    const frac = a.querySelector('.andes-money-amount__fraction');
    if (frac && frac.textContent.trim()) return frac.textContent.trim();
  }
  // Fallback: metadado estruturado, se existir.
  const meta = document.querySelector('meta[itemprop="price"]');
  if (meta && meta.content) return meta.content;
  // Último recurso: primeira fração qualquer.
  const any = document.querySelector('.andes-money-amount__fraction');
  return any ? any.textContent.trim() : null;
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
            valor = parse_brl(ultimo)
            if valor is not None:
                return valor, None
        time.sleep(1)
    if ultimo:
        return None, f"achou texto mas não virou número: {ultimo!r}"
    return None, "preço não encontrado (seletor/erro/bloqueio)"


def processar(args):
    # imports pesados só quando realmente vai rodar
    try:
        import gspread
        from gspread.cell import Cell
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("Falta instalar dependências. Rode:  pip install -r requirements.txt")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Falta o Playwright. Rode:  pip install -r requirements.txt && playwright install chromium")

    if not os.path.exists(CRED_PATH):
        sys.exit(
            f"Credencial não encontrada em: {CRED_PATH}\n"
            "Veja o README (seção 'Setup da conta de serviço')."
        )

    # ---- Conecta na planilha ----
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

    i_link = idx(COL_LINK)
    i_preco = idx(COL_PRECO)
    i_plat = idx(COL_PLATAFORMA)
    i_prod = idx(COL_PRODUTO) if COL_PRODUTO in cabecalho else i_plat
    col_preco_1based = i_preco + 1

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

    # ---- Sobe o navegador ----
    atualizacoes = []          # (linha_1based, valor_float)
    ok = falhas = pulados = 0
    print(f"Aba: {SHEET_TAB} | {len(dados)-1} linhas de dados\n")

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
        for r, linha in enumerate(dados[1:], start=2):  # linha 2 = 1ª de dados
            if args.limit and processadas >= args.limit:
                break
            url = (linha[i_link] if i_link < len(linha) else "").strip()
            plataforma = detectar_plataforma(
                linha[i_plat] if i_plat < len(linha) else "", url
            )
            nome = (linha[i_prod] if i_prod < len(linha) else "")[:45]

            if not url:
                continue
            if filtro and plataforma != filtro:
                pulados += 1
                continue

            processadas += 1
            etiqueta = f"[{r:>3}] {plataforma[:12]:<12} {nome:<45}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                valor, erro = extrair_preco(page, plataforma)
            except Exception as e:  # noqa: BLE001 — nunca deixa a execução quebrar
                valor, erro = None, f"erro ao abrir: {type(e).__name__}"

            if valor is not None:
                atualizacoes.append((r, valor))
                ok += 1
                print(f"{etiqueta} -> R$ {valor:.2f}")
            else:
                falhas += 1
                print(f"{etiqueta} -> FALHOU ({erro})")

            time.sleep(PAUSE_ENTRE_LINKS_S)

        navegador.close()

    # ---- Grava tudo de uma vez (1 chamada de API) ----
    print()
    if not atualizacoes:
        print("Nada extraído — planilha não foi alterada.")
    elif args.dry_run:
        print(f"[DRY-RUN] {len(atualizacoes)} valores extraídos, mas NÃO gravei nada.")
    else:
        cells = [Cell(row=r, col=col_preco_1based, value=v) for r, v in atualizacoes]
        ws.update_cells(cells, value_input_option="RAW")
        print(f"Planilha atualizada: {len(atualizacoes)} preços gravados na coluna '{COL_PRECO}'.")

    print(f"\nResumo: {ok} ok | {falhas} falhas | {pulados} pulados")


def main():
    ap = argparse.ArgumentParser(description="Atualiza preços na aba 'Compras Online'.")
    ap.add_argument("--headful", action="store_true", help="mostra o navegador")
    ap.add_argument("--limit", type=int, default=0, help="processa só N linhas (teste)")
    ap.add_argument("--dry-run", action="store_true", help="extrai mas não grava")
    ap.add_argument("--todas", action="store_true",
                    help="processa TODAS as plataformas (padrão é só Mercado Livre)")
    ap.add_argument("--so-mercadolivre", action="store_true")
    ap.add_argument("--so-shopee", action="store_true")
    ap.add_argument("--so-magalu", action="store_true")
    processar(ap.parse_args())


if __name__ == "__main__":
    main()
