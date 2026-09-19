#!/usr/bin/env python3
# ============================================================================
# Enxoval do Aprovado — Migração Sheets -> Supabase (Etapa 1)
#
# Reexecutável: limpa as 5 tabelas e repopula a partir da planilha (ao vivo).
# NÃO altera o site. Usa a service_role key (ignora RLS), lida de variável de
# ambiente — NUNCA hardcode a chave aqui.
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
GID = {"enx": 0, "equip": 1966974743, "contatos": 1665715047}
VALID = {"CBMDF - 2025", "CFP PMDF - 2023"}

# Mapa coluna de preço (nome curto no enxoval) -> nome canônico (nome completo).
# Colunas não listadas usam o próprio nome como canônico (auto-cria a loja).
CANON = {
    "Demir": "Demir Fardas Militares",
    "Cezar": "Cezar Uniformes",
    "Brito": "Brito Uniformes Militares",
    "Forte": "Forte Militar",
    "VL":    "VL Artigos Militares",
}
ONLINE_RE = re.compile(r"mercado ?livre|shopee|magazine|magalu|centauro|amazon|loja online", re.I)

# ---------------------------------------------------------------- util Supabase
# Usa curl (tem CA bundle próprio) para evitar problemas de SSL do Python no macOS,
# mantendo a verificação de certificado (TLS) intacta.
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
    """Insere em lotes e devolve as linhas criadas (com id)."""
    out = []
    for i in range(0, len(rows), 500):
        data = _req("POST", f"/rest/v1/{table}", rows[i:i+500], "return=representation")
        out.extend(data or [])
    return out

def delete_all(table):
    _req("DELETE", f"/rest/v1/{table}?id=not.is.null", None, "return=minimal")

def count(table):
    data = _req("GET", f"/rest/v1/{table}?select=id")
    return len(data or [])

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
        if str(c).strip().lower() in alvo:
            return i
    return -1

def num(s):
    s = re.sub(r"[^\d,.-]", "", str(s)).replace(",", ".")
    s = re.sub(r"\.(?=\d{3}(\D|$))", "", s)  # remove separador de milhar
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None

def canon(col):
    col = col.strip()
    return CANON.get(col, col)

def tipo_de(nome):
    return "online" if ONLINE_RE.search(nome) else "fisica"

# ---------------------------------------------------------------- ler abas
def ler_itens_tab(rows, item_headers):
    """Devolve (lojas_colunas, itens[]) de uma aba de preços guiada pelo cabeçalho."""
    h = rows[0]
    iConc = achar(h, "Concurso"); iCat = achar(h, "Categoria")
    iItem = achar(h, *item_headers); iQtd = achar(h, "Qtd Sugerida", "Qtd")
    iFoto = achar(h, "Link da Foto", "Foto")
    meta_max = max(iCat, iItem, iQtd, iFoto)
    loja_ini = meta_max + 1
    lojas_cols = [c.strip() for c in h[loja_ini:] if c.strip()]
    itens = []
    for r in rows[1:]:
        if len(r) <= iItem: continue
        conc = r[iConc].strip() if iConc >= 0 and iConc < len(r) else ""
        nome = r[iItem].strip()
        if conc not in VALID or not nome:
            continue
        precos = {}
        for k, col in enumerate(lojas_cols):
            idx = loja_ini + k
            if idx < len(r):
                v = num(r[idx])
                if v is not None:
                    precos[col.strip()] = v
        itens.append({
            "concurso": conc,
            "categoria": (r[iCat].strip() if iCat >= 0 and iCat < len(r) else None) or None,
            "nome": nome,
            "qtd": (int(num(r[iQtd])) if iQtd >= 0 and iQtd < len(r) and num(r[iQtd]) else None),
            "foto": (r[iFoto].strip() if iFoto >= 0 and iFoto < len(r) and re.match(r"https?://", r[iFoto].strip()) else None),
            "precos": precos,
        })
    return lojas_cols, itens

# ============================================================================ RUN
print("Baixando abas da planilha…")
enx_rows   = baixar_csv(GID["enx"])
equip_rows = baixar_csv(GID["equip"])
ct_rows    = baixar_csv(GID["contatos"])

enx_lojas,   enx_itens   = ler_itens_tab(enx_rows,   ["Item Padronizado", "Item"])
equip_lojas, equip_itens = ler_itens_tab(equip_rows, ["Acessórios", "Acessorios", "Item"])
todos_itens = enx_itens + equip_itens

descartes = []  # (motivo, detalhe)

# ---- limpar (ordem inversa das FKs) ----
print("Limpando tabelas…")
for t in ["precos", "loja_concurso", "itens_enxoval", "lojas", "concursos"]:
    delete_all(t)

# ---- 1) concursos ----
estado_de = {}
for r in enx_rows[1:] + equip_rows[1:]:
    if len(r) > 1 and r[1].strip() in VALID:
        estado_de[r[1].strip()] = (r[0].strip() or "Distrito Federal")
conc_rows = [{"nome": c, "estado": estado_de.get(c, "Distrito Federal"), "ativo": True} for c in sorted(VALID)]
conc_ins = insert("concursos", conc_rows)
conc_id = {c["nome"]: c["id"] for c in conc_ins}
print(f"  concursos: {len(conc_id)} -> {list(conc_id)}")

# ---- 2) lojas ----
# contatos (nome completo = canônico) + colunas de preço (mapeadas) + colunas novas (auto)
ct_h = ct_rows[0]
iNome = achar(ct_h, "Loja", "Nome"); iTel = achar(ct_h, "Telefone")
iIns = achar(ct_h, "Instagram"); iMaps = achar(ct_h, "Maps")
iC1 = achar(ct_h, "Concurso (1)", "Concurso 1", "Concurso"); iC2 = achar(ct_h, "Concurso (2)", "Concurso 2")

contato_por_loja = {}   # nome canônico -> dict de contato
for r in ct_rows[1:]:
    if len(r) <= iNome: continue
    nome = r[iNome].strip()
    if not nome: continue
    insta = r[iIns].strip() if iIns >= 0 and iIns < len(r) else ""
    ig = "https://www.instagram.com/" + insta.lstrip("@").strip() if insta.startswith("@") else None
    maps = r[iMaps].strip() if iMaps >= 0 and iMaps < len(r) else ""
    if not re.match(r"https?://", maps or ""): maps = None
    tel = re.sub(r"^telefone[:\s]*", "", r[iTel] if iTel >= 0 and iTel < len(r) else "", flags=re.I).strip() or None
    concs = []
    for ic in (iC1, iC2):
        if ic >= 0 and ic < len(r):
            v = r[ic].strip()
            if v in VALID: concs.append(v)
    contato_por_loja[nome] = {"telefone": tel, "instagram": ig, "link_maps": maps, "concursos": concs}

# conjunto canônico de lojas = contatos + todas as colunas de preço (mapeadas)
todas_lojas = set(contato_por_loja.keys())
col_para_canon = {}   # nome de coluna (trim) -> canônico  (p/ montar precos)
lojas_auto = []
for col in set(enx_lojas + equip_lojas):
    c = canon(col)
    col_para_canon[col.strip()] = c
    if c not in todas_lojas:
        todas_lojas.add(c)
        if c not in contato_por_loja:
            lojas_auto.append(c)

loja_rows = []
for nome in sorted(todas_lojas):
    ct = contato_por_loja.get(nome, {})
    loja_rows.append({
        "nome": nome, "tipo": tipo_de(nome),
        "telefone": ct.get("telefone"), "instagram": ct.get("instagram"),
        "link_maps": ct.get("link_maps"), "ativo": True,
    })
loja_ins = insert("lojas", loja_rows)
loja_id = {l["nome"]: l["id"] for l in loja_ins}
print(f"  lojas: {len(loja_id)}  (auto-criadas sem contato: {lojas_auto})")

# ---- 3) loja_concurso ----
lc_rows = []
for nome, info in contato_por_loja.items():
    for c in info["concursos"]:
        if nome in loja_id and c in conc_id:
            lc_rows.append({"loja_id": loja_id[nome], "concurso_id": conc_id[c]})
lc_ins = insert("loja_concurso", lc_rows) if lc_rows else []
print(f"  loja_concurso: {len(lc_ins)}")

# ---- 4) itens_enxoval ----
item_rows = []
chave_item = []  # paralela a item_rows: (concurso, nome)
vistos = set()
for it in todos_itens:
    ch = (it["concurso"], it["nome"])
    if ch in vistos:
        descartes.append(("item duplicado (concurso+nome)", ch)); continue
    vistos.add(ch)
    item_rows.append({
        "concurso_id": conc_id[it["concurso"]],
        "categoria": it["categoria"], "nome_padronizado": it["nome"],
        "qtd_sugerida": it["qtd"], "cargo": None, "link_foto": it["foto"],
    })
    chave_item.append(ch)
item_ins = insert("itens_enxoval", item_rows)
# casa retorno com a chave (PostgREST preserva a ordem do POST por lote de 500)
item_id = {}
for ch, row in zip(chave_item, item_ins):
    item_id[ch] = row["id"]
print(f"  itens_enxoval: {len(item_id)}")

# ---- 5) precos ----
preco_rows = []
for it in todos_itens:
    ch = (it["concurso"], it["nome"])
    iid = item_id.get(ch)
    if not iid: continue
    for col, val in it["precos"].items():
        cnome = col_para_canon.get(col.strip(), col.strip())
        lid = loja_id.get(cnome)
        if not lid:
            descartes.append(("preço sem loja correspondente", (it["nome"], col, val))); continue
        preco_rows.append({"item_id": iid, "loja_id": lid, "preco": val, "link_produto": None})
preco_ins = insert("precos", preco_rows)
print(f"  precos: {len(preco_ins)}")

# ============================================================================ CONFERÊNCIA
print("\n================ CONFERÊNCIA ================")
for t in ["concursos", "lojas", "loja_concurso", "itens_enxoval", "precos"]:
    print(f"  {t:15s}: {count(t)} linhas")

# itens por concurso
por_conc = Counter((it["concurso"]) for it in todos_itens if (it["concurso"], it["nome"]) in item_id)
print("\n  itens por concurso:", dict(por_conc))

# query de exemplo: preços de 'Coturno' para CBMDF, com nome da loja
print("\n  Exemplo — preços de Coturno (CBMDF - 2025):")
q = ("/rest/v1/precos?select=preco,lojas(nome),itens_enxoval!inner(nome_padronizado,categoria,"
     "concursos!inner(nome))"
     "&itens_enxoval.categoria=eq.Coturno&itens_enxoval.concursos.nome=eq." +
     urllib.parse.quote("CBMDF - 2025") + "&order=preco.asc&limit=20")
ex = _req("GET", q)
for row in (ex or [])[:20]:
    print(f"    {row['itens_enxoval']['nome_padronizado'][:34]:34s} | "
          f"{row['lojas']['nome'][:28]:28s} | R$ {row['preco']}")
if not ex:
    print("    (nenhuma linha — confira a categoria 'Coturno' na planilha)")

# descartes
print(f"\n  DESCARTES ({len(descartes)}):")
for motivo, det in descartes[:40]:
    print("    -", motivo, "->", det)
print("\nMigração concluída.")
