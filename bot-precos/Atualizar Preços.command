#!/bin/bash
# Duplo-clique neste arquivo no Finder para atualizar os preços da planilha.
# Na 1ª vez ele monta o ambiente (venv + dependências) sozinho.

cd "$(dirname "$0")" || exit 1

echo "==============================================="
echo "  Atualizador de preços — Enxoval CBMDF"
echo "==============================================="
echo

# Cria o ambiente virtual na primeira execução
if [ ! -d ".venv" ]; then
  echo "Primeira execução: preparando o ambiente (leva alguns minutos)..."
  python3 -m venv .venv || { echo "Falha ao criar venv"; read -r; exit 1; }
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt || { echo "Falha ao instalar dependências"; read -r; exit 1; }
  ./.venv/bin/playwright install chromium || { echo "Falha ao instalar o navegador"; read -r; exit 1; }
  echo "Ambiente pronto."
  echo
fi

# Roda o bot
./.venv/bin/python atualizar_precos.py "$@"

echo
echo "Concluído. Pode fechar esta janela."
read -r
