# CONTEXT.md — Enxoval do Aprovado

> Documento de contexto para retomar o projeto sem reler todo o código.
> Ao atualizar: preserve o que ainda é verdade, corrija o que mudou, remova o que já foi concluído.
> Última atualização: 2026-09-21.

## 1. Visão geral

**Enxoval do Aprovado** é um PWA mobile-first (arquivo único `index.html`) que ajuda aprovados em concursos militares a comparar preços, achar lojas e organizar a compra do enxoval obrigatório do Curso de Formação. Domínio: **enxovaldoaprovado.com.br**. Hospedado no **GitHub Pages** a partir do repositório **matheuselrocha/enxoval_do_aprovado** (branch `main` — todo push publica). Concursos ativos hoje: **CBMDF 2025** e **CFP PMDF 2023**.

## 2. Arquitetura atual

### Arquivos
- **`index.html`** — o app inteiro (CSS em `<style>`, JS em `<script>`, dados de fallback embutidos em `SNAPSHOT`). É o único artefato servido ao usuário.
- **`migracao/`** — importação Sheets → Supabase:
  - `migrate_supabase.py` — lê a planilha ao vivo (export CSV) e regrava o Supabase via `curl` + `service_role` (usa `curl` por causa de SSL em Python sem CA bundle).
  - `01_schema.sql`, `02_online.sql`, `03_rls.sql` — schema, catálogo online e RLS/grants. **Fonte de verdade do schema — não repetir aqui.**
  - `importar.sh` (runner, carrega `migracao/.env`), `.env.example`, `README.md`.
- **`bot-precos/`** — raspador de preços do Mercado Livre (Playwright) que grava preços na planilha via service account do Google. `atualizar_precos.py`, `requirements.txt`, `README.md`.
- **`.github/workflows/importar-supabase.yml`** — automação (ver §4).
- **`CNAME`**, **`manifest.webmanifest`**, `logo.png`/`icon-*`/`apple-touch-icon.png`/`og.png`, `README.md`.

### Fonte de dados HOJE
O site lê do **Supabase** (`CFG.fonte = "supabase"`). Ordem de fallback automática em runtime:
1. **Supabase** (PostgREST + anon key) — padrão.
2. **Google Sheets** via `export?format=csv&gid=N` (usa `export`, não `gviz`, porque `gviz` respeita filtros ativos e chegou a esconder linhas). GIDs em `CFG.gid`.
3. **`SNAPSHOT`** embutido no HTML (offline / se tudo falhar).

Override manual por URL: `?fonte=sheets` ou `?fonte=supabase`. A planilha continua sendo o **ponto de edição de dados** (o Supabase é repovoado a partir dela pela importação).

### Tabelas no Supabase
Schema detalhado em `migracao/*.sql`. Resumo:
- **`concursos`** — cada concurso/curso (ex.: CBMDF - 2025). Referenciada pelas demais.
- **`lojas`** — lojas (físicas e online), com contato/mapa/instagram.
- **`loja_concurso`** — N:N entre `lojas` e `concursos` (quais lojas atendem cada concurso).
- **`itens_enxoval`** — itens do enxoval; um item vira obrigatório de um concurso via vínculo ("Obrigatório em"). Pode referenciar `produtos_online`.
- **`precos`** — preço de um item numa loja (item × loja). De-dup global por (item, loja).
- **`produtos_online`** — catálogo de produtos online (link + imagem) da aba "Compras Online".
- **`leads`** — captura de e-mail/WhatsApp. **NÃO está nos `.sql` versionados** (criada direto no painel do Supabase); **protegida**: sem policy de SELECT para `anon` (só INSERT anônimo).

### Credenciais
- **anon key**: hardcoded em `index.html` (`SUPA_KEY`) — **pública por design** (role `anon`, só SELECT nas 6 tabelas de catálogo + INSERT em `leads`).
- **service_role key**: **nunca versionada**. Fica em `migracao/.env` (gitignored) localmente e no GitHub Secret `SUPABASE_SERVICE_ROLE`.
- **Google service account** (bot de preços): GitHub Secret `GOOGLE_CREDENTIALS_JSON`; gera `bot-precos/credenciais.json` (gitignored) em runtime e apaga depois.
- **PostHog**: `posthogKey` no HTML é Project API Key (pública por design).
- `.gitignore` cobre `.env`, `migracao/.env`, `bot-precos/credenciais.json`, `.venv/`, `__pycache__/`, `.DS_Store`.

## 3. Funcionalidades já implementadas (em produção)

- **Seleção de concurso** (tela inicial com identidade/louros) + persistência em localStorage + navegação com histórico (pushState/popstate); troca de concurso pelo selo do topo.
- **Captura de lead** pós-seleção: pop-up com Estado/Concurso **pré-preenchidos**; grava em `leads` no Supabase; e-mail duplicado = sucesso silencioso. Opt-in de "aviso de promoção" **desmarcado por padrão**.
- **Calculadora**: quantidade por item, escolha de loja por item, "lojas mais baratas", "selecionar tudo", "limpar", busca; fotos (links do Drive convertidos para thumbnail via `fotoURL()`).
- **Carrinho** agrupado pela loja escolhida, com checklist **"já comprei nesta loja"**, e **mensagem de WhatsApp por loja** com atribuição da plataforma; lojas online oferecem "Consultar ofertas" (salta pro catálogo Online).
- **Orçamento em PDF** (jsPDF + autotable, carregados sob demanda): cabeçalho com emblema/marca, uma tabela por loja, total, rodapé; itens de loja online recebem link clicável "ver produto online".
- **Catálogo Online** (aba) e **Bizus** (aba de materiais extras).
- **Analytics** PostHog (pageview, add_ao_carrinho, whatsapp_click, consultar_ofertas, selecao_concurso, baixar_pdf).
- **PWA** (manifest + ícones), domínio próprio, OG card.
- **Multi-concurso** no modelo de dados (pronto para adicionar novos concursos/cursos).

## 4. Em andamento / parcial

- **Automação de importação** (`importar-supabase.yml`): **rodando**.
  - `cron "17 * * * *"` (horário) → só **importa** Sheets → Supabase (leve).
  - `workflow_dispatch` manual → opção `atualizar_precos` (default true) que **raspa** preços do ML (bot-precos) e grava na planilha **antes** de importar.
- **Consentimento para alerta de preço**: o opt-in já é **coletado** no lead (`aceite_promocao`), mas **o disparo do alerta ainda não existe** (sem job/rotina de notificação). É o próximo diferencial planejado.

## 5. Decisões de arquitetura (e o porquê, em uma linha)

- **App em arquivo único (`index.html`)** — deploy trivial no GitHub Pages, sem build.
- **Supabase como fonte, mas Sheets como editor + fallback** — dados normalizados/escaláveis sem tirar a facilidade de edição na planilha; site nunca cai por falha de uma fonte.
- **`export?format=csv` em vez de `gviz`** — `gviz` respeita filtros ativos da planilha e escondia linhas.
- **Importação via `curl` (não urllib/requests)** — Python do sistema sem CA bundle dava `CERTIFICATE_VERIFY_FAILED`; `curl` tem bundle próprio e mantém verificação TLS.
- **anon key pública / `leads` sem SELECT anônimo** — leitura de catálogo é pública por design; leads são protegidos (só INSERT).
- **Lead com baixa fricção** (pop-up simples, e-mail dup = sucesso, opt-in opcional) — captar audiência sem espantar o usuário na reta final da compra.
- **Opt-in desmarcado por padrão** — conformidade LGPD (consentimento ativo).
- **Sem RLS de escrita / sem login ainda** — evitar backend pesado antes de validar o modelo comercial (loja pagante).
- **jsPDF carregado sob demanda** — não pesar o carregamento inicial no mobile para uma feature usada por poucos.
- **`bot-precos` só no dispatch manual** — raspagem é cara/instável; o cron horário fica leve.

## 6. Próximos passos previstos (ordem discutida)

Roteiro competitivo (vs. concorrente tfmrqs.github.io/enxoval-pmdf):
1. ✅ **PDF de orçamento** (feito).
2. **Múltiplas ofertas/marcas + cupom por item** na calculadora (aproveitando `produtos_online`).
3. **Preview de mapa embutido + "como chegar"** na aba de Lojas.
4. **Alerta de preço** (usa `aceite_promocao` + bot de preços) — diferencial que o concorrente não replica sem backend.
5. **Login/"minha conta"** (Supabase Auth) — lista sincronizada entre dispositivos (Etapa 3).
6. **Painel B2B de loja** (edição de preço por loja + métricas) — monetização; exige RLS de escrita.
7. **Expandir concursos/cursos** (operacionais, outras corporações/estados).

## 7. Pontos de atenção / riscos

- **RLS de escrita por loja (futuro)**: uma policy mal escrita pode deixar uma loja editar preço de outra. Escopar por identidade da loja e testar antes de liberar.
- **Nunca versionar a `service_role`** nem a `credenciais.json` do Google — repo é **público**.
- **`leads` deve continuar sem SELECT para `anon`** — não adicionar policy de leitura pública por engano.
- **Importação apaga e repovoa o Supabase** a partir da planilha: dado que só existir no Supabase (não na planilha) é perdido na próxima importação. A planilha é a fonte de verdade editável.
- **Correspondência item↔produto online** (link no PDF / "Consultar ofertas") é por **match fuzzy** de palavras-chave (`categoriaOnline`); pode errar em nomes ambíguos.
- **Cache do navegador/PWA**: mudanças no site podem exigir refresh forçado / aba anônima para aparecer.
- **`SNAPSHOT` embutido** pode ficar desatualizado vs. a fonte ao vivo; é só rede de segurança, não fonte primária.
