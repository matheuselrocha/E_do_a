#!/usr/bin/env python3
# ============================================================================
# Enxoval do Aprovado — Migração Sheets -> Supabase (Etapa 1)
#
# Reexecutável: limpa as tabelas e repopula a partir da planilha (ao vivo).
# NÃO altera o site. Usa a service_role key (ignora RLS), lida de variável de
# ambiente — NUNCA hardcode a chave aqui.
#
# Requer: 01_schema.sql e 02_online.sql já rodados no SQL Editor.
#
# Novidades:
#  - Liga itens_enxoval.produto_online_id ao catálogo POR NOME (com relatório
#    de divergências: item vendido online cujo nome não casa com o catálogo).
#  - Suporta (opcional) a coluna "Obrigatório em" na aba Compras Online:
#    um produto do catálogo marcado com um concurso vira item obrigatório desse
#    concurso automaticamente (caminho do "cadastro único" da Etapa 2). Se a
#    coluna não existir, nada muda.
#
# Uso:
#   SUPABASE_SERVICE_ROLE='eyJ...'  python3 migrate_supabase.py
#   (opcional) SUPABASE_URL='https://<proj>.supabase.co'
# ============================================================================
import os, sys, csv, io, re, json, subprocess, tempfile, urllib.parse
from collections import defaultdict, Counter

SUPA_URL = os.environ.get("SUPABASE_URL", "https://zjvxatpdajxdtpmifkxg.supabase.co").rstrip("/")
KEY      = os.environ.get("SUPABASE_SERVICE_ROLE", "").strip()
if not KEY:
    sys.exit("ERRO: defina SUPABASE_SERVICE_ROLE (service_role key) na variável de ambiente.")

PLANILHA = "1Yi-czpyFQeRk58tUx95wkevEG3lkwtcZQANR1KspV8U"
GID = {"enx": 0, "equip": 1966974743, "contatos": 1665715047, "online": 223946766}
VALID = {"CBMDF - 2025", "CFP PMDF - 2023"}

CANON = {"Demir": "Demir Fardas Militares", "Cezar": "Cezar Uniformes",
         "Brito": "Brito Uniformes Militares", "Forte": "Forte Militar",
         "VL": "VL Artigos Militares"}
ONLINE_RE = re.compile(r"mercado ?livre|shopee|magazine|magalu|centauro|amazon|loja online", re.I)
DOM2LOJA = [("mercadolivre", "Mercado Livre"), ("magazineluiza", "Magazine Luiza"),
            ("magalu", "Magazine Luiza"), ("shopee", "Shopee"),
            ("centauro", "Centauro"), ("amazon", "Amazon")]

def norm(s):  # normaliza nome p/ casar item <-> produto (sem caixa, espaços colapsados)
    return re.sub(r"\s+", " ", str(s or "").strip()).casefold()

# ---------------------------------------------------------------- util Supabase (via curl)
def _req(method, path, body=None, prefer=None):
    url = SUPA_URL + path
    args = ["curl", "-sS", "--fail-with-body", "-X", method, url,
            "-H", "apikey: " + KEY, "-H", "Authorization: Bearer " + KEY,
            "-H", "Content-Type: application/json"]
    if prefer: args += ["-H", "Prefer: " + prefer]
    tmp = None
    if body is not None:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(body, tmp); tmp.close()
        args += ["--data-binary", "@" + tmp.name]
    p = subprocess.run(args, capture_output=True, text=True)
    if tmp: os.unlink(tmp.name)
    if p.returncode != 0:
        sys.exit(f"ERRO Supabase {method} {path}: {p.stdout[:500]} {p.stderr[:200]}")
    out = p.stdout.strip()
    return json.loads(out) if out else None

def insert(table, rows):
    out = []
    for i in range(0, len(rows), 500):
        data = _req("POST", f"/rest/v1/{table}", rows[i:i+500], "return=representation")
        out.extend(data or [])
    return out

def delete_all(table): _req("DELETE", f"/rest/v1/{table}?id=not.is.null", None, "return=minimal")
def count(table):      return len(_req("GET", f"/rest/v1/{table}?select=id") or [])

# ---------------------------------------------------------------- util planilha
def baixar_csv(gid):
    url = f"https://docs.google.com/spreadsheets/d/{PLANILHA}/export?format=csv&gid={gid}"
    p = subprocess.run(["curl", "-sSL", "--fail-with-body", url], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"ERRO ao baixar aba gid={gid}: {p.stderr[:200]}")
    return list(csv.reader(io.StringIO(p.stdout)))

def achar(header, *nomes):
    alvo = [n.strip().lower() for n in nomes]
    for i, c in enumerate(header):
        if str(c).strip().lower() in alvo: return i
    return -1

def num(s):
    s = re.sub(r"[^\d,.-]", "", str(s)).replace(",", ".")
    s = re.sub(r"\.(?=\d{3}(\D|$))", "", s)
    try: v = float(s)
    except ValueError: return None
    return v if v > 0 else None

def canon(col):    return CANON.get(col.strip(), col.strip())
def tipo_de(nome): return "online" if ONLINE_RE.search(nome) else "fisica"
def loja_do_link(u):
    m = re.search(r"https?://([^/]+)", u or ""); host = m.group(1).lower() if m else ""
    for key, nome in DOM2LOJA:
        if key in host: return nome
    return None

# ---------------------------------------------------------------- ler aba de preços
def ler_itens_tab(rows, item_headers):
    h = rows[0]
    iConc = achar(h, "Concurso"); iCat = achar(h, "Categoria")
    iItem = achar(h, *item_headers); iQtd = achar(h, "Qtd Sugerida", "Qtd")
    iFoto = achar(h, "Link da Foto", "Foto")
    loja_ini = max(iCat, iItem, iQtd, iFoto) + 1
    lojas_cols = [c.strip() for c in h[loja_ini:] if c.strip()]
    itens, brancos = [], 0
    for r in rows[1:]:
        if len(r) <= iItem: continue
        conc = r[iConc].strip() if 0 <= iConc < len(r) else ""
        nome = r[iItem].strip()
        if conc not in VALID: continue
        if not nome: brancos += 1; continue
        precos = []
        for k, col in enumerate(lojas_cols):
            idx = loja_ini + k
            if idx < len(r):
                v = num(r[idx])
                if v is not None:
                    precos.append({"loja": canon(col), "preco": v, "link": None})
        itens.append({"concurso": conc,
                      "categoria": (r[iCat].strip() if 0 <= iCat < len(r) else "") or None,
                      "nome": nome,
                      "qtd": (int(num(r[iQtd])) if 0 <= iQtd < len(r) and num(r[iQtd]) else None),
                      "foto": (r[iFoto].strip() if 0 <= iFoto < len(r) and re.match(r"https?://", r[iFoto].strip()) else None),
                      "precos": precos, "origem": "planilha"})
    return lojas_cols, itens, brancos

# ============================================================================ RUN
print("Baixando abas da planilha…")
enx_rows   = baixar_csv(GID["enx"])
equip_rows = baixar_csv(GID["equip"])
ct_rows    = baixar_csv(GID["contatos"])
onl_rows   = baixar_csv(GID["online"])

enx_lojas,   enx_itens,   enx_brancos   = ler_itens_tab(enx_rows,   ["Item Padronizado", "Item"])
equip_lojas, equip_itens, equip_brancos = ler_itens_tab(equip_rows, ["Acessórios", "Acessorios", "Item"])

descartes = []
if enx_brancos:   descartes.append(("linhas em branco na aba Enxoval (sem nome)", enx_brancos))
if equip_brancos: descartes.append(("linhas em branco na aba Acessórios (sem nome)", equip_brancos))

# ---- limpar (ordem inversa das FKs) ----
print("Limpando tabelas…")
for t in ["precos", "itens_enxoval", "produtos_online", "loja_concurso", "lojas", "concursos"]:
    delete_all(t)

# ---- 1) concursos ----
estado_de = {}
for r in enx_rows[1:] + equip_rows[1:]:
    if len(r) > 1 and r[1].strip() in VALID:
        estado_de[r[1].strip()] = (r[0].strip() or "Distrito Federal")
conc_ins = insert("concursos", [{"nome": c, "estado": estado_de.get(c, "Distrito Federal"), "ativo": True}
                                for c in sorted(VALID)])
conc_id = {c["nome"]: c["id"] for c in conc_ins}
print(f"  concursos: {len(conc_id)} -> {list(conc_id)}")

# ---- 2) lojas ----
ct_h = ct_rows[0]
iNome = achar(ct_h, "Loja", "Nome"); iTel = achar(ct_h, "Telefone")
iIns = achar(ct_h, "Instagram"); iMaps = achar(ct_h, "Maps")
iC1 = achar(ct_h, "Concurso (1)", "Concurso 1", "Concurso"); iC2 = achar(ct_h, "Concurso (2)", "Concurso 2")
contato_por_loja = {}
for r in ct_rows[1:]:
    if len(r) <= iNome: continue
    nome = r[iNome].strip()
    if not nome: continue
    insta = r[iIns].strip() if 0 <= iIns < len(r) else ""
    ig = "https://www.instagram.com/" + insta.lstrip("@").strip() if insta.startswith("@") else None
    maps = r[iMaps].strip() if 0 <= iMaps < len(r) else ""
    if not re.match(r"https?://", maps or ""): maps = None
    tel = re.sub(r"^telefone[:\s]*", "", r[iTel] if 0 <= iTel < len(r) else "", flags=re.I).strip() or None
    concs = [r[ic].strip() for ic in (iC1, iC2) if 0 <= ic < len(r) and r[ic].strip() in VALID]
    contato_por_loja[nome] = {"telefone": tel, "instagram": ig, "link_maps": maps, "concursos": concs}

online_h = onl_rows[0]
iOLink = achar(online_h, "Link do Produto", "Link")
market_online = set()
for r in onl_rows[1:]:
    if 0 <= iOLink < len(r):
        ln = loja_do_link(r[iOLink])
        if ln: market_online.add(ln)

col_para_canon, todas_lojas, lojas_auto = {}, set(contato_por_loja.keys()), []
for col in set(enx_lojas + equip_lojas):
    c = canon(col); col_para_canon[col.strip()] = c
    if c not in todas_lojas:
        todas_lojas.add(c)
        if c not in contato_por_loja: lojas_auto.append(c)
for m in market_online:
    if m not in todas_lojas:
        todas_lojas.add(m)
        if m not in contato_por_loja: lojas_auto.append(m)

loja_rows = [{"nome": n, "tipo": tipo_de(n), "telefone": contato_por_loja.get(n, {}).get("telefone"),
              "instagram": contato_por_loja.get(n, {}).get("instagram"),
              "link_maps": contato_por_loja.get(n, {}).get("link_maps"), "ativo": True}
             for n in sorted(todas_lojas)]
loja_id = {l["nome"]: l["id"] for l in insert("lojas", loja_rows)}
lojas_online = sorted([n for n in todas_lojas if tipo_de(n) == "online"])
print(f"  lojas: {len(loja_id)}  (online: {lojas_online})")

# ---- 3) produtos_online (catálogo global) ----
iOCat = achar(online_h, "Categoria"); iOProd = achar(online_h, "Produto")
iOImg = achar(online_h, "Link da Imagem", "Imagem")
iOObrig = achar(online_h, "Obrigatório em", "Obrigatorio em", "Obrigatório", "Obrigatorio")  # opcional
iOPreco = achar(online_h, "Preço", "Preco")            # opcional (p/ itens vindos do catálogo)
iOQtd   = achar(online_h, "Qtd Sugerida", "Qtd")       # opcional
prod_rows, prod_meta = [], []   # prod_meta paralelo: dados p/ "Obrigatório em"
for r in onl_rows[1:]:
    if len(r) <= iOProd: continue
    nome = r[iOProd].strip()
    if not nome: continue
    link = r[iOLink].strip() if 0 <= iOLink < len(r) else ""
    ln = loja_do_link(link)
    prod_rows.append({"categoria": (r[iOCat].strip() if 0 <= iOCat < len(r) else "") or None,
                      "nome": nome, "link_produto": link or None,
                      "link_imagem": (r[iOImg].strip() if 0 <= iOImg < len(r) and r[iOImg].strip() else None),
                      "loja_id": loja_id.get(ln) if ln else None, "ativo": True})
    obrig = []
    if iOObrig >= 0 and iOObrig < len(r):
        obrig = [c.strip() for c in re.split(r"[;,/\n]+", r[iOObrig]) if c.strip() in VALID]
    prod_meta.append({"nome": nome, "categoria": (r[iOCat].strip() if 0 <= iOCat < len(r) else "") or None,
                      "link": link or None, "imagem": (r[iOImg].strip() if 0 <= iOImg < len(r) else None),
                      "loja": ln, "obrig": obrig,
                      "preco": (num(r[iOPreco]) if 0 <= iOPreco < len(r) else None),
                      "qtd": (int(num(r[iOQtd])) if 0 <= iOQtd < len(r) and num(r[iOQtd]) else None)})
prod_ins = insert("produtos_online", prod_rows)
print(f"  produtos_online: {len(prod_ins)}")

# mapa nome->produto_id (p/ ligar) + detecta nomes duplicados no catálogo
prod_by_nome = {}
dup_prod = []
for p, meta in zip(prod_ins, prod_meta):
    k = norm(p["nome"])
    if k in prod_by_nome: dup_prod.append(p["nome"])
    else: prod_by_nome[k] = p["id"]
if dup_prod:
    descartes.append(("nomes repetidos no catálogo online (liga só o 1º)", dup_prod[:10]))

# ---- itens vindos do catálogo via "Obrigatório em" (cadastro único) ----
tagged_itens = []
for p, meta in zip(prod_ins, prod_meta):
    for c in meta["obrig"]:
        precos = []
        if meta["loja"] and (meta["preco"] is not None or meta["link"]):
            precos = [{"loja": meta["loja"], "preco": meta["preco"], "link": meta["link"]}]
        tagged_itens.append({"concurso": c, "categoria": meta["categoria"], "nome": meta["nome"],
                             "qtd": meta["qtd"] if meta["qtd"] else 1,
                             "foto": meta["imagem"] if (meta["imagem"] and re.match(r"https?://", meta["imagem"])) else None,
                             "precos": precos, "origem": "catalogo", "produto_id": p["id"]})
if tagged_itens:
    print(f"  (itens marcados via 'Obrigatório em': {len(tagged_itens)})")

todos_itens = enx_itens + equip_itens + tagged_itens

# ---- 4) loja_concurso: físicas (via contatos) + TODA loja online em TODOS os concursos ----
lc, seen = [], set()
def add_lc(loja, conc):
    if loja in loja_id and conc in conc_id:
        k = (loja_id[loja], conc_id[conc])
        if k not in seen: seen.add(k); lc.append({"loja_id": k[0], "concurso_id": k[1]})
for nome, info in contato_por_loja.items():
    for c in info["concursos"]: add_lc(nome, c)
for nome in lojas_online:
    for c in conc_id: add_lc(nome, c)
insert("loja_concurso", lc)
print(f"  loja_concurso: {len(lc)}")

# ---- 5) itens_enxoval (com produto_online_id ligado por nome ou por construção) ----
item_rows, chave_item, vistos = [], [], set()
online_sem_match = []   # relatório: item vendido online sem produto no catálogo
for it in todos_itens:
    ch = (it["concurso"], it["nome"])
    if ch in vistos:
        if it["origem"] == "catalogo":
            descartes.append(("produto 'Obrigatório em' já existia na planilha (mantida a linha da planilha)", ch))
        else:
            descartes.append(("item duplicado (concurso+nome)", ch))
        continue
    vistos.add(ch)
    pid = it.get("produto_id") or prod_by_nome.get(norm(it["nome"]))
    if not pid and any(tipo_de(p["loja"]) == "online" for p in it["precos"]):
        online_sem_match.append(ch)
    item_rows.append({"concurso_id": conc_id[it["concurso"]], "categoria": it["categoria"],
                      "nome_padronizado": it["nome"], "qtd_sugerida": it["qtd"],
                      "cargo": None, "link_foto": it["foto"], "produto_online_id": pid})
    chave_item.append(ch)
item_id = {}
for ch, row in zip(chave_item, insert("itens_enxoval", item_rows)):
    item_id[ch] = row["id"]
ligados = sum(1 for r in item_rows if r["produto_online_id"])
print(f"  itens_enxoval: {len(item_id)}  (ligados ao catálogo online: {ligados})")

# ---- 6) precos ----
preco_rows = []
for it in todos_itens:
    iid = item_id.get((it["concurso"], it["nome"]))
    if not iid: continue
    por_loja = {}   # de-dup por loja dentro do item
    for p in it["precos"]:
        lid = loja_id.get(p["loja"])
        if not lid:
            descartes.append(("preço sem loja correspondente", (it["nome"], p["loja"]))); continue
        if p["preco"] is None and not p["link"]: continue
        por_loja[lid] = {"item_id": iid, "loja_id": lid, "preco": p["preco"], "link_produto": p["link"]}
    preco_rows.extend(por_loja.values())
insert("precos", preco_rows)
print(f"  precos: {len(preco_rows)}")

# ============================================================================ CONFERÊNCIA
print("\n================ CONFERÊNCIA ================")
for t in ["concursos", "lojas", "loja_concurso", "produtos_online", "itens_enxoval", "precos"]:
    print(f"  {t:16s}: {count(t)} linhas")
por_conc = Counter(it["concurso"] for it in todos_itens if (it["concurso"], it["nome"]) in item_id)
print("\n  itens por concurso:", dict(por_conc))
print(f"  itens ligados ao catálogo online (produto_online_id): {ligados}")

print("\n  DIVERGÊNCIAS DE NOME (item vendido online sem produto no catálogo — confira a grafia):")
if online_sem_match:
    for ch in online_sem_match[:30]: print("    -", ch)
else:
    print("    nenhuma 🎉")

print(f"\n  DESCARTES ({len(descartes)}):")
for motivo, det in descartes[:40]: print("    -", motivo, "->", det)
print("\nMigração concluída.")
