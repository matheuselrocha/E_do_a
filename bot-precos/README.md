# Bot de atualização de preços

Entra em cada link da aba **"Compras Online"** da planilha, lê o preço na página
(Mercado Livre / Shopee / Magazine Luiza) e grava o valor numérico de volta na
coluna **Preço** da mesma linha.

- **Ler** a planilha é público; **escrever** exige uma **conta de serviço** do Google (passo único abaixo).
- Cada link roda em `try/except`: se falhar, anota o motivo e segue — nunca quebra no meio.
- Grava **número** (ex.: `10.9`). Formate a coluna F como moeda no Sheets se quiser ver "R$".

## Como usar (depois do setup)

Duplo-clique em **`Atualizar Preços.command`** no Finder. Na 1ª vez ele monta o
ambiente sozinho (venv + navegador); nas próximas, só roda.

Ou pelo terminal:

```bash
cd bot-precos
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
python3 atualizar_precos.py
```

Flags úteis para teste:

```bash
python3 atualizar_precos.py --limit 5 --headful   # 5 primeiras, navegador visível
python3 atualizar_precos.py --dry-run             # extrai mas NÃO grava
python3 atualizar_precos.py --so-mercadolivre     # só uma plataforma
```

## Setup da conta de serviço (uma vez, ~5 min)

Sem isso o bot lê mas **não consegue escrever** na planilha.

1. Acesse <https://console.cloud.google.com/> e crie um projeto (ou use um existente).
2. Em **APIs e Serviços → Biblioteca**, ative a **Google Sheets API**.
3. Em **APIs e Serviços → Credenciais → Criar credenciais → Conta de serviço**.
   Dê um nome qualquer e finalize.
4. Abra a conta de serviço criada → aba **Chaves → Adicionar chave → Criar nova
   chave → JSON**. Baixa um arquivo `.json`.
5. Renomeie esse arquivo para **`credenciais.json`** e coloque **dentro desta pasta**
   (`bot-precos/`).
6. Abra o `credenciais.json`, copie o valor de `client_email`
   (algo como `nome@projeto.iam.gserviceaccount.com`).
7. Na sua planilha do Google Sheets, clique em **Compartilhar** e adicione esse
   e-mail como **Editor**.

Pronto. O `.gitignore` já impede que o `credenciais.json` seja commitado.

## Rodar na nuvem (GitHub Actions)

O bot está embutido na ação **"Importar planilha -> Supabase"**
(`.github/workflows/importar-supabase.yml`). O fluxo é:

> bot atualiza a coluna Preço na planilha → importação leva pro Supabase → site

- **Automático (cron, de hora em hora):** roda **só a importação** (leve). Não
  raspa preços.
- **Manual (botão Run workflow):** com a opção *"Raspar preços do Mercado Livre"*
  marcada (padrão), primeiro raspa os preços do ML na planilha e **depois**
  importa pro Supabase — tudo numa ação só.

Setup único do secret da credencial do Google:

1. Copie o conteúdo da credencial para a área de transferência:

   ```bash
   pbcopy < "/Users/matheus_elr/code/enxoval-cbmdf/bot-precos/credenciais.json"
   ```

2. No GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**.
3. Nome: **`GOOGLE_CREDENTIALS_JSON`**. Valor: **cole** (Cmd+V) o JSON inteiro. Salve.

(O secret `SUPABASE_SERVICE_ROLE`, usado pela importação, já existe.)

Para rodar: aba **Actions → "Importar planilha -> Supabase" → Run workflow**.

**Ressalva honesta:** o Mercado Livre às vezes bloqueia mais os IPs de data
center do GitHub do que o IP residencial do seu Mac. Se a raspagem na nuvem
começar a falhar muito, rode localmente pelo `.command` e deixe a ação só
importando.

## Ajustes finos

No topo de `atualizar_precos.py` (ou por variável de ambiente):

- `SHEET_ID` — ID da planilha.
- `SHEET_TAB` — nome da aba (padrão `Compras Online`).
- `COL_LINK`, `COL_PRECO`, `COL_PLATAFORMA` — nomes das colunas no cabeçalho.

## Limitações honestas por plataforma

| Site | Situação |
|---|---|
| **Mercado Livre** | Confiável. Usa o bloco do preço atual + metadado como reforço. |
| **Magazine Luiza** | Funciona, mas os links `onelink.me` redirecionam; o `id` do preço tem número dinâmico, então o bot casa por **prefixo** (`price-final-label...`) e por `data-testid="price-value"`. |
| **Shopee** | O mais instável: forte anti-bot e a classe `pyzxvq pw3J3G` é **hash que muda a cada deploy** deles. O bot tenta a classe, depois metadados/JSON-LD, mas falhas são esperadas — nesses casos ele apenas registra e segue. |

Se a Shopee/Magalu começarem a falhar em massa, rode com `--headful` para ver o que
a página está mostrando (login, captcha, etc.) e me avise para reajustar os seletores.
