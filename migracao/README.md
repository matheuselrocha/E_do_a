# Importação da planilha → Supabase

Este diretório importa os dados do Google Sheets ("Backend - Controle de Enxoval")
para o Supabase. **Não altera o site** — o site lê o Supabase depois de importado.

## Arquivos
- `01_schema.sql` — cria as 5 tabelas (concursos, lojas, loja_concurso, itens_enxoval, precos). Rode 1x no SQL Editor.
- `02_online.sql` — cria `produtos_online` + coluna `itens_enxoval.produto_online_id`. Rode 1x.
- `03_rls.sql` — leitura pública (anon) das tabelas de catálogo. Rode 1x.
- `04_fase0.sql` — chaves únicas (upsert com IDs estáveis), `lojas.parceira`, `precos.updated_at`
  automático e histórico de preços (`precos_historico`). Rode 1x.
- `05_painel.sql` — painel das lojas parceiras: `loja_usuarios` (conta → loja), `minha_loja()`,
  leitura do catálogo p/ usuário logado e **RLS de escrita em `precos`** (só a própria loja parceira,
  só itens dos concursos que ela atende, só a coluna `preco`). Rode 1x.
- `06_metricas.sql` — métricas por loja: tabela `eventos_loja` (sem acesso direto), `registrar_eventos()`
  (o site grava cliques de WhatsApp, carrinho, PDF etc.) e `metricas_minha_loja()` (a loja logada lê só
  os próprios números, agregados). Rode 1x.
- `07_admin.sql` — painel administrativo: tabela `admins`, `eh_admin()` e `metricas_admin()` (métricas de
  todas as lojas, somadas e por loja, com período livre). Criar admin: ver o fim do arquivo. Rode 1x.
- `08_equipe.sql` — equipe da administração (`admin.html`): papéis `proprietario` / `admin` / `leitor`,
  `meu_papel()`, `pode_editar()`, `listar_equipe()`, `alterar_papel()`. O proprietário é definido só no SQL
  (ver o fim do arquivo); convites e remoções passam pela Edge Function `supabase/functions/equipe`. Rode 1x.
- `09_dados_completos.sql` — etapa 1 do backend: o que só existia na planilha/site ganha lugar no banco —
  `concursos` (botão itens iniciais + textos do `CFG`), `lojas.estado/cidade`, `itens_enxoval.feminino`, tabelas
  `cupons`, `cupom_produtos`, `cupom_itens` e o histórico `alteracoes` (quem/quando/antes/depois, por gatilho). Rode 1x.
- `10_bizus.sql` — tabela `bizus` (links informativos por concurso: categoria, título, link, ordem). Rode 1x.
  A importação lê as abas cujo nome contém "Bizus" pela **API do Google** (os links são hiperlinks; o CSV não traz o
  endereço) — precisa de `google-auth requests` e da credencial do robô (`GOOGLE_CREDENTIALS` ou `bot-precos/credenciais.json`);
  sem isso, pula os bizus sem apagar nada. Concurso = coluna "Concurso" ou início do nome da aba ("CBMDF - Bizus para o CFP").
- `11_escrita.sql` — etapa 4: o painel grava **só por funções** `admin_salvar_*` / `admin_excluir_*` (concurso, loja,
  item, preço, produto + `admin_produto_concursos`, cupom, bizu) e lê `admin_historico`. Exigem proprietário/administrador,
  validam os campos (erro ENX02 com mensagem pronta) e respeitam a **trava** `configuracoes.fonte_dados`:
  `planilha` (padrão) = painel não grava (ENX01); `painel` = painel grava e **a importação se recusa a rodar**.
  Só o proprietário troca (`definir_fonte_dados`). Rode 1x.
- `12_trava_prazo.sql` — prazo de validade da trava (`liberado_ate`): `definir_fonte_dados('painel', 60)` libera por
  60 min (janela de teste) e volta sozinha a `planilha`; sem minutos = permanente (virada). `status_edicao()` para o painel. Rode 1x.
- `13_historico_precos.sql` — `admin_historico_precos(item, loja, limite)`: a equipe lê o histórico de preços (aba Histórico do
  painel) com nomes de item, concurso, loja e autor; `precos_historico` segue fechada. Só leitura. Rode 1x.
- `14_virada.sql` — preparação da virada: origem **robo** no histórico (`_origem()`; conta logada sempre conta como
  `painel`), `robo_preco_produto(id, preco)` só p/ `service_role` (grava produto + itens ligados; só com o painel liberado),
  e renomear produto obrigatório renomeia os itens ligados (recusa se o concurso já tiver outro item com o nome). Rode 1x.
- `gerar_reserva.py` — gera `dados.json` (reserva do site se o Supabase cair) com a chave **pública**: resultado cru das
  mesmas consultas do site (`CONSULTAS`); recusa gravar se vier pouco dado (mantém a reserva anterior). `--conferir` só mostra
  o resumo. No Mac, rode com o Python do `bot-precos/.venv` (tem os certificados).
- `backup_banco.py` — backup de **todas** as tabelas (descobertas pela API) em JSON, em `~/Backups/enxoval-do-aprovado/<data_hora>/`
  (fora do projeto; o script recusa pasta dentro do repositório). Usa a service_role (`migracao/.env`); confere a contagem de cada
  tabela com o banco. **Tem dado pessoal (leads)** — guardar em lugar seguro. Não inclui `auth.users`.
- `conferir.py` — conferência planilha × banco (só lê): abas Concursos, Contatos e Cupons campo a campo.
  `set -a; . migracao/.env; set +a; python3 migracao/conferir.py` → "✅ Planilha e banco iguais."
- `migrate_supabase.py` — o importador (lê a planilha ao vivo e **sincroniza**: atualiza pelo nome,
  mantendo os IDs, e remove só o que saiu da planilha).
- `importar.sh` — atalho para rodar o importador (carrega a chave do `.env`).
- `.env.example` — modelo do arquivo de segredo (a chave real fica em `.env`, fora do git).

## Primeira vez (setup)
1. No Supabase (SQL Editor), rode `01_schema.sql`, `02_online.sql`, `03_rls.sql`, `04_fase0.sql`, `05_painel.sql`, `06_metricas.sql`, `07_admin.sql`, `08_equipe.sql` e `09_dados_completos.sql` (nessa ordem).
2. Copie `migracao/.env.example` para `migracao/.env` e cole a **service_role key**
   (Supabase → Settings → API → `service_role`). O `.env` **não** é versionado.

## Rodar a importação (sempre que editar a planilha)
```bash
bash migracao/importar.sh
```
O script sincroniza as tabelas (upsert pela chave natural: nome da loja, nome do concurso,
concurso + nome do item, item + loja) e imprime um relatório de conferência (gravadas/removidas
por tabela, itens por concurso, divergências e descartes). **Renomear** um item/loja na planilha
= apagar o antigo e criar um novo (novo ID).

## Como a planilha vira dados
- **Enxoval Unificado** → itens do fardamento (uma coluna por loja, com preço).
- **Compras Online** → catálogo `produtos_online` (global). Colunas:
  `Categoria | Produto | Plataforma | Link do Produto | Link da Imagem | Preço | Obrigatório em: | Qtd`.
  - **`Obrigatório em:`** = nome(s) do(s) concurso(s) onde o produto é item obrigatório
    (aparece na calculadora). Vários concursos: separe por vírgula ou `;`
    (ex.: `CBMDF - 2025, CFP PMDF - 2023`). Em branco = só no catálogo Online.
  - **`Plataforma`** = a loja online (Mercado Livre, Shopee, …).
- **DF - Contatos** → lojas físicas (telefone, instagram, maps) e quais concursos atendem.
  - **`Status de Ativação`** desmarcado = loja some do site.
  - **`Parceira`** marcado = loja contratante: a importação **não cria, altera nem apaga** os preços
    dela (a fonte é o painel da loja). Loja parceira também nunca é apagada do banco.
- **Concursos** → lista de concursos importados (`Status de Ativação` → `concursos.ativo`).

Toda mudança de preço (importação ou painel) fica em `precos_historico` (só leitura interna).

## Painel das lojas (`painel.html`)
Contas são criadas **só pelo admin** (cadastro público do Supabase desligado):
1. Supabase → Authentication → Users → **Add user** (e-mail + senha, marcar *Auto Confirm User*).
2. SQL Editor — ligar a conta à loja (a loja precisa estar com **Parceira** marcada na aba Contatos):
   ```sql
   insert into loja_usuarios (user_id, loja_id)
   select u.id, l.id from auth.users u, lojas l
    where u.email = 'email@da.loja' and l.nome = 'Nome exato da loja'
   on conflict (user_id) do update set loja_id = excluded.loja_id;
   ```
3. A loja entra em `https://enxovaldoaprovado.com.br/painel.html` e edita os preços dos itens
   dos concursos que atende (Concurso (1)/(2) na aba Contatos). Toda mudança vai pro `precos_historico`
   com `origem = 'painel'`.

## Segurança
A `service_role` key ignora o RLS (acesso total). Ela fica **só** no `.env` local
(nunca no git, no site ou em commits). Se vazar, rotacione em Settings → API.
