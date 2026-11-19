#!/usr/bin/env python3
# ============================================================================
# Enxoval do Aprovado — conferência planilha × Supabase (só LÊ; não grava nada).
# Compara campo a campo o que o site hoje lê direto da planilha (abas Concursos,
# Contatos e Cupons) com o que está no banco. Itens, preços e catálogo já são
# conferidos pelo relatório do importador e pela comparação do site
# (?fonte=sheets × ?fonte=supabase).
#
# Uso:  bash migracao/importar.sh   (antes, se quiser o banco em dia)
#       set -a; . migracao/.env; set +a; python3 migracao/conferir.py
# Sai com código 1 se houver diferença.
# ============================================================================
import os, sys, csv, io, re, json, subprocess

SUPA_URL = os.environ.get("SUPABASE_URL", "https://zjvxatpdajxdtpmifkxg.supabase.co").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_ROLE", "").strip() or sys.exit("ERRO: defina SUPABASE_SERVICE_ROLE.")
PLANILHA = "1Yi-czpyFQeRk58tUx95wkevEG3lkwtcZQANR1KspV8U"
GID = {"concursos": 205883308, "contatos": 1665715047, "cupons": 2033305666}

norm = lambda s: re.sub(r"\s+", " ", str(s or "").strip()).casefold()
sim = lambda s: norm(s) in {"true", "verdadeiro", "sim", "s", "x", "1", "ativo"}
vazio = lambda s: (str(s or "").strip() or None)

def get(q):
    p = subprocess.run(["curl", "-sS", "--fail-with-body", SUPA_URL + "/rest/v1/" + q,
                        "-H", "apikey: " + KEY, "-H", "Authorization: Bearer " + KEY], capture_output=True, text=True)
    if p.returncode: sys.exit(f"ERRO Supabase {q}: {p.stdout[:300]}")
    return json.loads(p.stdout)

def aba(gid):
    p = subprocess.run(["curl", "-sSL", "--fail-with-body",
                        f"https://docs.google.com/spreadsheets/d/{PLANILHA}/export?format=csv&gid={gid}"], capture_output=True, text=True)
    if p.returncode: sys.exit(f"ERRO ao baixar aba {gid}")
    rows = list(csv.reader(io.StringIO(p.stdout)))
    h = [c.strip().rstrip(":").strip().lower() for c in rows[0]]
    col = lambda *ns: next((h.index(n.lower()) for n in ns if n.lower() in h), -1)
    return rows[1:], col

DIF = []
def confere(onde, campo, planilha, banco):
    if planilha != banco: DIF.append(f"{onde} · {campo}: planilha={planilha!r} banco={banco!r}")

# ---- Concursos ----
rows, col = aba(GID["concursos"])
iE, iC, iA, iI = col("Estado"), col("Concurso"), col("Status de Ativação"), col("Botão de Itens Iniciais")
banco = {c["nome"]: c for c in get("concursos?select=nome,estado,ativo,itens_iniciais")}
vistos = set()
for r in rows:
    nome = r[iC].strip() if iC < len(r) else ""
    if not nome: continue
    vistos.add(nome); b = banco.get(nome)
    if not b: DIF.append(f"Concurso '{nome}': falta no banco"); continue
    confere(nome, "estado", vazio(r[iE]), b["estado"])
    confere(nome, "ativo", sim(r[iA]), b["ativo"])
    confere(nome, "itens_iniciais", sim(r[iI]), b["itens_iniciais"])
for nome in set(banco) - vistos: DIF.append(f"Concurso '{nome}': está no banco e não na planilha")
print(f"Concursos: {len(vistos)} conferidos")

# ---- Contatos (lojas) ----
rows, col = aba(GID["contatos"])
iN, iT, iIg, iM = col("Loja", "Nome"), col("Telefone"), col("Instagram"), col("Maps")
iAt, iP, iEs, iCi = col("Status de Ativação"), col("Parceira"), col("Estado"), col("Cidade/Município", "Cidade")
iC1, iC2 = col("Concurso (1)", "Concurso"), col("Concurso (2)")
banco = {l["nome"]: l for l in get("lojas?select=nome,telefone,instagram,link_maps,ativo,parceira,estado,cidade,loja_concurso(concursos(nome))")}
n = 0
for r in rows:
    nome = r[iN].strip() if iN < len(r) else ""
    if not nome: continue
    n += 1; b = banco.get(nome)
    if not b: DIF.append(f"Loja '{nome}': falta no banco"); continue
    ig = r[iIg].strip() if iIg < len(r) else ""
    maps = r[iM].strip() if iM < len(r) else ""
    confere(nome, "telefone", vazio(re.sub(r"^telefone[:\s]*", "", r[iT], flags=re.I)), b["telefone"])
    confere(nome, "instagram", "https://www.instagram.com/" + ig.lstrip("@").strip() if ig.startswith("@") else None, b["instagram"])
    confere(nome, "maps", maps if re.match(r"https?://", maps) else None, b["link_maps"])
    confere(nome, "ativo", sim(r[iAt]), b["ativo"])
    confere(nome, "parceira", sim(r[iP]), b["parceira"])
    confere(nome, "estado", vazio(r[iEs]), b["estado"])
    confere(nome, "cidade", vazio(r[iCi]), b["cidade"])
    concs = {r[i].strip() for i in (iC1, iC2) if 0 <= i < len(r) and r[i].strip().strip("-–— ")}
    confere(nome, "concursos", sorted(concs), sorted(x["concursos"]["nome"] for x in b["loja_concurso"]))
print(f"Lojas (aba Contatos): {n} conferidas")

# ---- Cupons ----
rows, col = aba(GID["cupons"])
iA, iCo, iTx, iL, iPr = col("Ativo"), col("Código", "Codigo"), col("Texto do balão", "Texto"), col("Loja"), col("Produtos")
banco = {c["codigo"]: c for c in get("cupons?select=codigo,ativo,texto,lojas(nome),"
                                     "cupom_produtos(produtos_online(nome)),cupom_itens(itens_enxoval(nome_padronizado))")}
n = 0
for r in rows:
    cod = r[iCo].strip() if iCo < len(r) else ""
    if not cod: continue
    n += 1; b = banco.get(cod)
    if not b: DIF.append(f"Cupom '{cod}': falta no banco"); continue
    confere(cod, "ativo", sim(r[iA]), b["ativo"])
    confere(cod, "texto", vazio(r[iTx]), b["texto"])
    confere(cod, "loja", vazio(r[iL]), (b["lojas"] or {}).get("nome"))
    nomes = {norm(x) for x in r[iPr].split(";") if x.strip()}
    no_banco = {norm(x["produtos_online"]["nome"]) for x in b["cupom_produtos"]} | \
               {norm(x["itens_enxoval"]["nome_padronizado"]) for x in b["cupom_itens"]}
    confere(cod, "produtos", sorted(nomes), sorted(no_banco))
print(f"Cupons: {n} conferidos")

print()
if DIF:
    print(f"❌ {len(DIF)} diferença(s):"); [print("  -", d) for d in DIF]; sys.exit(1)
print("✅ Planilha e banco iguais.")
