#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera a cópia de reserva do site (dados.json) a partir do Supabase — etapa 6, passo A3.

O site lê do Supabase; se o Supabase cair, ele usa este arquivo (servido pelo próprio
GitHub Pages) e, em último caso, o SNAPSHOT embutido no HTML.

  * Usa a chave PÚBLICA (anon), a mesma do site: o arquivo tem exatamente o que um
    visitante vê (cupom e bizu desativados ficam de fora pela RLS). Nada de service_role.
  * Guarda o resultado CRU de cada consulta (mesmas consultas do site, em CONSULTAS),
    para o site montar a tela com o mesmo código, venha o dado do banco ou do arquivo.
  * Recusa gravar um arquivo suspeito (poucos itens/concursos/preços) — melhor manter a
    reserva anterior do que publicar uma vazia. Grava de forma atômica (arquivo temporário).

Uso:
    python3 migracao/gerar_reserva.py                 # grava dados.json na raiz do site
    python3 migracao/gerar_reserva.py --saida x.json  # outro caminho
    python3 migracao/gerar_reserva.py --conferir      # só consulta e mostra o resumo
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPA_URL = os.environ.get("SUPABASE_URL", "https://zjvxatpdajxdtpmifkxg.supabase.co").rstrip("/")
# chave pública (anon) — a mesma que está no index.html; pode ser versionada
ANON = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpqdnhhdHBkYWp4ZHRwbWlma3hnIiwicm9sZSI6"
        "ImFub24iLCJpYXQiOjE3ODk1MTM5ODYsImV4cCI6MjEwNTA4OTk4Nn0.Y23MtmFlyQrk1sL9FSmem5SXPSUAQX2l__PxHNKrvnU")

# As MESMAS consultas que o site faz (index.html). Mudou uma lá, mude aqui.
CONSULTAS = {
    "concursos": "concursos?select=id,nome,estado,ativo,itens_iniciais,rotulo,ano,selo,msg_whatsapp,descricao,ordem&order=ordem",
    "itens":     "itens_enxoval?select=id,nome_padronizado,categoria,qtd_sugerida,link_foto,item_inicial,feminino,"
                 "concursos!inner(nome),precos(preco,lojas(nome,ativo))&order=categoria,nome_padronizado",
    "produtos":  "produtos_online?select=id,categoria,nome,link_produto,link_imagem,preco,ativo,lojas(nome,ativo)&order=categoria,nome",
    "contatos":  "loja_concurso?select=concursos(nome),lojas!inner(nome,tipo,telefone,instagram,link_maps)"
                 "&lojas.tipo=eq.fisica&lojas.ativo=eq.true",
    "cupons":    "cupons?select=codigo,texto,lojas(nome),cupom_produtos(produtos_online(nome)),"
                 "cupom_itens(itens_enxoval(nome_padronizado,concursos(nome)))&order=codigo",
    "bizus":     "bizus?select=categoria,titulo,link,ordem,concursos(nome)&ativo=eq.true&order=ordem",
}
# mínimos para aceitar o arquivo (abaixo disso, algo deu errado na consulta)
MINIMOS = {"concursos": 1, "itens": 20, "produtos": 5, "contatos": 1}


def _ssl_ctx():
    import ssl
    try:
        import certifi  # Python do macOS sem certificados
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def consultar(q):
    req = urllib.request.Request(f"{SUPA_URL}/rest/v1/{q}", headers={"apikey": ANON, "Authorization": "Bearer " + ANON})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"ERRO na consulta {q.split('?')[0]}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERRO de conexão com o Supabase: {e.reason}")


def resumo(d):
    c = d["consultas"]
    precos = sum(len(i.get("precos") or []) for i in c["itens"])
    ativos = [x["nome"] for x in c["concursos"] if x.get("ativo")]
    return (f"concursos {len(c['concursos'])} (ativos: {', '.join(ativos) or '-'}) · itens {len(c['itens'])} · preços {precos}"
            f" · produtos {len(c['produtos'])} (ativos {sum(1 for p in c['produtos'] if p.get('ativo'))})"
            f" · contatos {len(c['contatos'])} · cupons ativos {len(c['cupons'])} · bizus {len(c['bizus'])}")


def main():
    ap = argparse.ArgumentParser(description="Gera dados.json (reserva do site) a partir do Supabase.")
    ap.add_argument("--saida", default=os.path.join(RAIZ, "dados.json"))
    ap.add_argument("--conferir", action="store_true", help="só consulta e mostra o resumo, sem gravar")
    a = ap.parse_args()

    d = {"versao": 1, "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "consultas": {k: consultar(q) for k, q in CONSULTAS.items()}}
    print(resumo(d))

    falta = [f"{k} ({len(d['consultas'][k])} < {m})" for k, m in MINIMOS.items() if len(d["consultas"][k]) < m]
    precos = sum(len(i.get("precos") or []) for i in d["consultas"]["itens"])
    if precos < 50:
        falta.append(f"preços ({precos} < 50)")
    if falta:
        sys.exit("NÃO gravei: dados suspeitos — " + "; ".join(falta) + ". A reserva anterior continua valendo.")
    if a.conferir:
        return

    texto = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    tmp = a.saida + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, a.saida)
    print(f"Gravado: {os.path.relpath(a.saida, RAIZ)} ({len(texto.encode('utf-8')) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
