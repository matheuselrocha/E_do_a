#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backup do banco (Supabase) em JSON — etapa 6, passo A6 (usar no dia D, passo B2, e quando quiser).

Exporta TODAS as tabelas do schema público (descobertas pela própria API, então tabela nova entra
sozinha), uma por arquivo, numa pasta FORA do projeto (nunca vai para o git):

    ~/Backups/enxoval-do-aprovado/AAAA-MM-DD_HHMM/<tabela>.json
                                                 /resumo.json   (contagens + data)

  * Usa a chave service_role (lê tudo, inclusive equipe, histórico, leads e métricas).
    A chave vem da variável SUPABASE_SERVICE_ROLE ou de migracao/.env — nunca versionar.
  * O backup tem DADO PESSOAL (leads: nome/e-mail/telefone). Guarde num lugar seguro.
  * Pagina de 1.000 em 1.000, ordenando pela chave primária, e confere a contagem de cada
    tabela com a contagem exata do banco (Prefer: count=exact). Diferença = erro.
  * Não exporta auth.users (contas de login): fica no próprio Supabase.

Uso:
    python3 migracao/backup_banco.py                    # pasta padrão
    python3 migracao/backup_banco.py --saida /caminho   # outra pasta
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
SUPA_URL = os.environ.get("SUPABASE_URL", "https://zjvxatpdajxdtpmifkxg.supabase.co").rstrip("/")
PAGINA = 1000
# chave primária quando não é "id" (para paginar em ordem estável)
PK = {"admins": "user_id", "loja_usuarios": "user_id"}


def _chave():
    k = os.environ.get("SUPABASE_SERVICE_ROLE", "").strip()
    env = os.path.join(AQUI, ".env")
    if not k and os.path.exists(env):
        for ln in open(env, encoding="utf-8"):
            m = re.match(r"\s*(?:export\s+)?SUPABASE_SERVICE_ROLE\s*=\s*['\"]?([^'\"\s]+)", ln)
            if m:
                k = m.group(1)
    if not k:
        sys.exit("Defina SUPABASE_SERVICE_ROLE (ou coloque em migracao/.env).")
    return k


def _ssl_ctx():
    import ssl
    try:
        import certifi  # Python do macOS sem certificados
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(caminho, chave, extra=None):
    h = {"apikey": chave, "Authorization": "Bearer " + chave, "Accept": "application/json"}
    h.update(extra or {})
    req = urllib.request.Request(SUPA_URL + caminho, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
            return json.loads(r.read().decode("utf-8")), dict(r.headers)
    except urllib.error.HTTPError as e:
        sys.exit(f"ERRO {e.code} em {caminho.split('?')[0]}: {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERRO de conexão com o Supabase: {e.reason}")


def tabelas(chave):
    """Tabelas expostas pela API (OpenAPI da raiz do PostgREST), sem as funções rpc."""
    spec, _ = _get("/rest/v1/", chave, {"Accept": "application/openapi+json"})
    return sorted(p.strip("/") for p in spec.get("paths", {}) if p.count("/") == 1 and p != "/" and not p.startswith("/rpc"))


def exportar(tab, chave):
    pk = PK.get(tab, "id")
    linhas, ini = [], 0
    total = None
    while True:
        dados, hdr = _get(f"/rest/v1/{tab}?select=*&order={pk}", chave,
                          {"Range-Unit": "items", "Range": f"{ini}-{ini + PAGINA - 1}", "Prefer": "count=exact"})
        if total is None:   # Content-Range: 0-999/1234
            m = re.search(r"/(\d+)$", hdr.get("Content-Range", "") or hdr.get("content-range", ""))
            total = int(m.group(1)) if m else None
        linhas += dados
        if len(dados) < PAGINA:
            break
        ini += PAGINA
    return linhas, total


def main():
    ap = argparse.ArgumentParser(description="Backup de todas as tabelas do Supabase em JSON (fora do projeto).")
    ap.add_argument("--saida", default=os.path.join(os.path.expanduser("~"), "Backups", "enxoval-do-aprovado",
                                                     datetime.now().strftime("%Y-%m-%d_%H%M")))
    a = ap.parse_args()
    if os.path.abspath(a.saida).startswith(os.path.dirname(AQUI) + os.sep):
        sys.exit("A pasta do backup não pode ficar dentro do projeto (o repositório é público).")
    chave = _chave()
    os.makedirs(a.saida, exist_ok=True)

    resumo, erros = {}, []
    for tab in tabelas(chave):
        linhas, total = exportar(tab, chave)
        with open(os.path.join(a.saida, f"{tab}.json"), "w", encoding="utf-8") as f:
            json.dump(linhas, f, ensure_ascii=False, indent=1)
        ok = total is None or total == len(linhas)
        resumo[tab] = len(linhas)
        print(f"  {tab:<18} {len(linhas):>6}" + ("" if ok else f"   ← ERRO: o banco diz {total}"))
        if not ok:
            erros.append(tab)
    with open(os.path.join(a.saida, "resumo.json"), "w", encoding="utf-8") as f:
        json.dump({"gerado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "supabase": SUPA_URL, "tabelas": resumo}, f, ensure_ascii=False, indent=1)
    print(f"\nBackup em: {a.saida}  ({len(resumo)} tabelas, {sum(resumo.values())} linhas)")
    if erros:
        sys.exit("ATENÇÃO: contagem diferente em " + ", ".join(erros) + " — rode de novo.")


if __name__ == "__main__":
    main()
