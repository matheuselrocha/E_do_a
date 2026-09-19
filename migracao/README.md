# Importação da planilha → Supabase

Este diretório importa os dados do Google Sheets ("Backend - Controle de Enxoval")
para o Supabase. **Não altera o site** — o site lê o Supabase depois de importado.

## Arquivos
- `01_schema.sql` — cria as 5 tabelas (concursos, lojas, loja_concurso, itens_enxoval, precos). Rode 1x no SQL Editor.
- `02_online.sql` — cria `produtos_online` + coluna `itens_enxoval.produto_online_id`. Rode 1x.
- `03_rls.sql` — leitura pública (anon) das tabelas de catálogo. Rode 1x.
- `migrate_supabase.py` — o importador (lê a planilha ao vivo, limpa e repopula).
- `importar.sh` — atalho para rodar o importador (carrega a chave do `.env`).
- `.env.example` — modelo do arquivo de segredo (a chave real fica em `.env`, fora do git).

## Primeira vez (setup)
1. No Supabase (SQL Editor), rode `01_schema.sql`, `02_online.sql` e `03_rls.sql` (nessa ordem).
2. Copie `migracao/.env.example` para `migracao/.env` e cole a **service_role key**
   (Supabase → Settings → API → `service_role`). O `.env` **não** é versionado.

## Rodar a importação (sempre que editar a planilha)
```bash
bash migracao/importar.sh
```
O script limpa e repopula as tabelas e imprime um relatório de conferência
(contagens por tabela, itens por concurso, divergências e descartes).

## Como a planilha vira dados
- **Enxoval Unificado** → itens do fardamento (uma coluna por loja, com preço).
- **Compras Online** → catálogo `produtos_online` (global). Colunas:
  `Categoria | Produto | Plataforma | Link do Produto | Link da Imagem | Preço | Obrigatório em: | Qtd`.
  - **`Obrigatório em:`** = nome(s) do(s) concurso(s) onde o produto é item obrigatório
    (aparece na calculadora). Vários concursos: separe por vírgula ou `;`
    (ex.: `CBMDF - 2025, CFP PMDF - 2023`). Em branco = só no catálogo Online.
  - **`Plataforma`** = a loja online (Mercado Livre, Shopee, …).
- **DF - Contatos** → lojas físicas (telefone, instagram, maps) e quais concursos atendem.

Concursos válidos hoje: `CBMDF - 2025` e `CFP PMDF - 2023` (valores fora disso são ignorados).

## Segurança
A `service_role` key ignora o RLS (acesso total). Ela fica **só** no `.env` local
(nunca no git, no site ou em commits). Se vazar, rotacione em Settings → API.
