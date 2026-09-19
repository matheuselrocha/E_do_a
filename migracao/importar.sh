#!/usr/bin/env bash
# ============================================================================
# Importa os dados da planilha (Google Sheets) para o Supabase.
# Uso:  bash migracao/importar.sh
#
# Ele lê a service_role key do arquivo migracao/.env (que NÃO vai para o git).
# Na primeira vez: copie migracao/.env.example para migracao/.env e cole a chave.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# carrega variáveis do .env local, se existir
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

if [ -z "${SUPABASE_SERVICE_ROLE:-}" ]; then
  echo "❌ Falta a SUPABASE_SERVICE_ROLE."
  echo "   Crie o arquivo migracao/.env a partir de migracao/.env.example e cole a service_role key."
  exit 1
fi

echo "▶️  Importando planilha -> Supabase…"
python3 migrate_supabase.py
echo "✅ Concluído."
