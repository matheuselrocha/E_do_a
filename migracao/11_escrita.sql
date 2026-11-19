-- ============================================================================
-- Enxoval do Aprovado — Etapa 4 do backend: regras de escrita pelo painel
-- Rode no SQL Editor DEPOIS do 10_bizus.sql. Reexecutável.
--
-- Modelo:
--  * O painel grava SÓ por estas funções (security definer). Nenhuma tabela ganha
--    grant/policy de escrita nova para `authenticated` — as lojas parceiras
--    continuam com as mesmas regras do 05 (só o próprio preço).
--  * Cada função exige pode_editar() (proprietário ou administrador; leitor não)
--    e valida os campos; erro de validação = SQLSTATE ENX02 com mensagem pronta
--    para mostrar na tela.
--  * TRAVA da fonte de dados (configuracoes.fonte_dados):
--      'planilha' (agora) → o painel NÃO grava (ENX01): a importação horária
--                           desfaria a edição;
--      'painel'  (virada) → o painel grava e a importação se recusa a rodar.
--    Só o proprietário troca (definir_fonte_dados).
--  * Excluir algo com vínculos desativa em vez de apagar (concurso com itens,
--    loja com preços/métricas/conta). A função devolve o que fez.
--  * Toda gravação cai no histórico `alteracoes` (09) com alterado_por.
-- ============================================================================

-- ---------------------------------------------------------------- trava
create table if not exists configuracoes (
  id          boolean primary key default true check (id),     -- linha única
  fonte_dados text not null default 'planilha' check (fonte_dados in ('planilha','painel')),
  alterado_em timestamptz not null default now(),
  alterado_por uuid
);
insert into configuracoes (id) values (true) on conflict do nothing;
alter table configuracoes enable row level security;            -- sem policy: lido pelas funções
revoke all on configuracoes from anon, authenticated;
grant all on configuracoes to service_role;                     -- o importador consulta a trava

create or replace function fonte_dados() returns text
language sql stable security definer set search_path = public as $$
  select fonte_dados from configuracoes where id
$$;
revoke execute on function fonte_dados() from public, anon;
grant execute on function fonte_dados() to authenticated;

create or replace function definir_fonte_dados(p text) returns text
language plpgsql security definer set search_path = public as $$
begin
  if (select papel from admins where user_id = auth.uid()) is distinct from 'proprietario' then
    raise exception 'acesso negado' using errcode = '42501';
  end if;
  if p not in ('planilha','painel') then raise exception 'Fonte inválida.' using errcode = 'ENX02'; end if;
  update configuracoes set fonte_dados = p, alterado_em = now(), alterado_por = auth.uid() where id;
  return p;
end $$;
revoke execute on function definir_fonte_dados(text) from public, anon;
grant execute on function definir_fonte_dados(text) to authenticated;

-- ---------------------------------------------------------------- utilitários internos
create or replace function _exige_edicao() returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not pode_editar() then raise exception 'acesso negado' using errcode = '42501'; end if;
  if (select fonte_dados from configuracoes where id) <> 'painel' then
    raise exception 'Os dados ainda vêm da planilha: faça a mudança por lá. O painel passa a editar na virada.'
      using errcode = 'ENX01';
  end if;
end $$;

create or replace function _erro(msg text) returns void
language plpgsql volatile as $$ begin raise exception '%', msg using errcode = 'ENX02'; end $$;

-- texto aparado; '' vira null
create or replace function _txt(p jsonb, k text) returns text
language sql immutable as $$ select nullif(btrim(p ->> k), '') $$;

create or replace function _url(u text, campo text) returns text
language plpgsql volatile as $$
begin
  if u is null then return null; end if;
  if u !~* '^https?://[^\s]+$' then perform _erro(campo || ': informe um link completo (começando com https://).'); end if;
  return u;
end $$;

create or replace function _bool(p jsonb, k text, padrao boolean) returns boolean
language sql immutable as $$ select case when p ? k then coalesce((p ->> k)::boolean, padrao) else padrao end $$;

revoke execute on function _exige_edicao(), _erro(text), _txt(jsonb, text), _url(text, text), _bool(jsonb, text, boolean)
  from public, anon, authenticated;

-- ---------------------------------------------------------------- concursos
create or replace function admin_salvar_concurso(p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid := nullif(p ->> 'id', '')::uuid;
  v_nome text := _txt(p, 'nome');
  v_ordem int := nullif(p ->> 'ordem', '')::int;
begin
  perform _exige_edicao();
  if v_nome is null then perform _erro('Informe o nome do concurso (ex.: CBMDF - 2025).'); end if;
  if length(v_nome) > 80 then perform _erro('Nome do concurso muito longo (máx. 80).'); end if;
  if v_id is null then
    insert into concursos (nome, estado, ativo, itens_iniciais, rotulo, ano, selo, msg_whatsapp, descricao, ordem)
    values (v_nome, coalesce(_txt(p, 'estado'), 'Distrito Federal'), _bool(p, 'ativo', false), _bool(p, 'itens_iniciais', false),
            _txt(p, 'rotulo'), _txt(p, 'ano'), _txt(p, 'selo'), _txt(p, 'msg_whatsapp'), _txt(p, 'descricao'), v_ordem)
    returning id into v_id;
  else
    update concursos set
      nome = v_nome,
      estado = case when p ? 'estado' then coalesce(_txt(p, 'estado'), estado) else estado end,
      ativo = _bool(p, 'ativo', ativo), itens_iniciais = _bool(p, 'itens_iniciais', itens_iniciais),
      rotulo = case when p ? 'rotulo' then _txt(p, 'rotulo') else rotulo end,
      ano = case when p ? 'ano' then _txt(p, 'ano') else ano end,
      selo = case when p ? 'selo' then _txt(p, 'selo') else selo end,
      msg_whatsapp = case when p ? 'msg_whatsapp' then _txt(p, 'msg_whatsapp') else msg_whatsapp end,
      descricao = case when p ? 'descricao' then _txt(p, 'descricao') else descricao end,
      ordem = case when p ? 'ordem' then v_ordem else ordem end
    where id = v_id;
    if not found then perform _erro('Concurso não encontrado.'); end if;
  end if;
  return v_id;
exception when unique_violation then perform _erro('Já existe um concurso com esse nome.'); return null;
end $$;

create or replace function admin_excluir_concurso(p_id uuid) returns text
language plpgsql security definer set search_path = public as $$
begin
  perform _exige_edicao();
  if not exists (select 1 from concursos where id = p_id) then perform _erro('Concurso não encontrado.'); end if;
  if exists (select 1 from itens_enxoval where concurso_id = p_id) or exists (select 1 from bizus where concurso_id = p_id)
     or exists (select 1 from loja_concurso where concurso_id = p_id) then
    update concursos set ativo = false where id = p_id;
    return 'desativado';                                   -- tem itens/bizus/lojas: some do site, nada se perde
  end if;
  delete from concursos where id = p_id;
  return 'removido';
end $$;

-- ---------------------------------------------------------------- lojas
create or replace function admin_salvar_loja(p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid := nullif(p ->> 'id', '')::uuid;
  v_nome text := _txt(p, 'nome');
  v_tipo text := coalesce(_txt(p, 'tipo'), 'fisica');
  v_tel text := nullif(regexp_replace(coalesce(p ->> 'telefone', ''), '\D', '', 'g'), '');
  v_ig text := _txt(p, 'instagram');
  v_concs uuid[];
begin
  perform _exige_edicao();
  if v_nome is null then perform _erro('Informe o nome da loja.'); end if;
  if v_tipo not in ('fisica','online') then perform _erro('Tipo de loja inválido (física ou online).'); end if;
  if v_tel is not null and length(v_tel) not between 10 and 13 then perform _erro('Telefone: use DDD + número (ex.: 61 98765-4321).'); end if;
  if v_ig like '@%' then v_ig := 'https://www.instagram.com/' || substr(v_ig, 2); end if;   -- aceita @perfil
  v_ig := _url(v_ig, 'Instagram');
  if p ? 'concursos' then
    select coalesce(array_agg(x::uuid), '{}') into v_concs from jsonb_array_elements_text(p -> 'concursos') x;
    if exists (select 1 from unnest(v_concs) c where not exists (select 1 from concursos where id = c)) then
      perform _erro('Concurso inválido na lista da loja.');
    end if;
  end if;
  if v_id is null then
    insert into lojas (nome, tipo, telefone, instagram, link_maps, ativo, parceira, estado, cidade)
    values (v_nome, v_tipo, v_tel, v_ig, _url(_txt(p, 'link_maps'), 'Mapa'), _bool(p, 'ativo', true), _bool(p, 'parceira', false),
            _txt(p, 'estado'), _txt(p, 'cidade'))
    returning id into v_id;
  else
    update lojas set
      nome = v_nome, tipo = v_tipo,
      telefone = case when p ? 'telefone' then v_tel else telefone end,
      instagram = case when p ? 'instagram' then v_ig else instagram end,
      link_maps = case when p ? 'link_maps' then _url(_txt(p, 'link_maps'), 'Mapa') else link_maps end,
      ativo = _bool(p, 'ativo', ativo), parceira = _bool(p, 'parceira', parceira),
      estado = case when p ? 'estado' then _txt(p, 'estado') else estado end,
      cidade = case when p ? 'cidade' then _txt(p, 'cidade') else cidade end
    where id = v_id;
    if not found then perform _erro('Loja não encontrada.'); end if;
  end if;
  if v_concs is not null then                              -- concursos atendidos: a lista enviada vira a verdade
    delete from loja_concurso where loja_id = v_id and concurso_id <> all (v_concs);
    insert into loja_concurso (loja_id, concurso_id) select v_id, c from unnest(v_concs) c on conflict do nothing;
  end if;
  return v_id;
exception when unique_violation then perform _erro('Já existe uma loja com esse nome.'); return null;
end $$;

create or replace function admin_excluir_loja(p_id uuid) returns text
language plpgsql security definer set search_path = public as $$
begin
  perform _exige_edicao();
  if not exists (select 1 from lojas where id = p_id) then perform _erro('Loja não encontrada.'); end if;
  if exists (select 1 from lojas where id = p_id and parceira) or exists (select 1 from precos where loja_id = p_id)
     or exists (select 1 from eventos_loja where loja_id = p_id) or exists (select 1 from loja_usuarios where loja_id = p_id)
     or exists (select 1 from produtos_online where loja_id = p_id) or exists (select 1 from cupons where loja_id = p_id) then
    update lojas set ativo = false where id = p_id;
    return 'desativada';                                   -- preços, métricas e conta ficam guardados
  end if;
  delete from lojas where id = p_id;
  return 'removida';
end $$;

-- ---------------------------------------------------------------- itens do enxoval
create or replace function admin_salvar_item(p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid := nullif(p ->> 'id', '')::uuid;
  v_conc uuid := nullif(p ->> 'concurso_id', '')::uuid;
  v_nome text := _txt(p, 'nome_padronizado');
  v_cat text := _txt(p, 'categoria');
  v_qtd int := nullif(p ->> 'qtd_sugerida', '')::int;
  v_prod uuid := nullif(p ->> 'produto_online_id', '')::uuid;
begin
  perform _exige_edicao();
  if v_nome is null then perform _erro('Informe o nome do item.'); end if;
  if v_cat is null then perform _erro('Informe a categoria do item.'); end if;
  if v_qtd is not null and v_qtd not between 0 and 99 then perform _erro('Quantidade sugerida entre 0 e 99 (0 = a critério do candidato).'); end if;
  if v_prod is not null and not exists (select 1 from produtos_online where id = v_prod) then perform _erro('Produto do catálogo não encontrado.'); end if;
  if v_id is null then
    if v_conc is null or not exists (select 1 from concursos where id = v_conc) then perform _erro('Escolha o concurso do item.'); end if;
    insert into itens_enxoval (concurso_id, categoria, nome_padronizado, qtd_sugerida, link_foto, item_inicial, feminino, produto_online_id)
    values (v_conc, v_cat, v_nome, coalesce(v_qtd, 1), _url(_txt(p, 'link_foto'), 'Foto'), _bool(p, 'item_inicial', false),
            _bool(p, 'feminino', false), v_prod)
    returning id into v_id;
  else
    update itens_enxoval set                               -- renomear mantém o id (carrinhos não perdem o item)
      categoria = v_cat, nome_padronizado = v_nome,
      qtd_sugerida = case when p ? 'qtd_sugerida' then v_qtd else qtd_sugerida end,
      link_foto = case when p ? 'link_foto' then _url(_txt(p, 'link_foto'), 'Foto') else link_foto end,
      item_inicial = _bool(p, 'item_inicial', item_inicial), feminino = _bool(p, 'feminino', feminino),
      produto_online_id = case when p ? 'produto_online_id' then v_prod else produto_online_id end
    where id = v_id;                                       -- concurso do item não muda (cria-se outro)
    if not found then perform _erro('Item não encontrado.'); end if;
  end if;
  return v_id;
exception when unique_violation then perform _erro('Esse concurso já tem um item com esse nome.'); return null;
end $$;

create or replace function admin_excluir_item(p_id uuid) returns text
language plpgsql security definer set search_path = public as $$
begin
  perform _exige_edicao();
  delete from itens_enxoval where id = p_id;               -- preços e vínculo de cupom vão junto (cascade)
  if not found then perform _erro('Item não encontrado.'); end if;
  return 'removido';
end $$;

-- ---------------------------------------------------------------- preços (item × loja)
-- p_preco null = a loja deixa de vender o item (apaga o preço).
create or replace function admin_salvar_preco(p_item uuid, p_loja uuid, p_preco numeric, p_link text default null) returns text
language plpgsql security definer set search_path = public as $$
begin
  perform _exige_edicao();
  if not exists (select 1 from itens_enxoval where id = p_item) then perform _erro('Item não encontrado.'); end if;
  if not exists (select 1 from lojas where id = p_loja) then perform _erro('Loja não encontrada.'); end if;
  if p_preco is null then
    delete from precos where item_id = p_item and loja_id = p_loja;
    return 'removido';
  end if;
  if p_preco <= 0 or p_preco >= 100000 then perform _erro('Preço deve ser maior que zero e menor que R$ 100.000.'); end if;
  insert into precos (item_id, loja_id, preco, link_produto)
  values (p_item, p_loja, round(p_preco, 2), _url(nullif(btrim(p_link), ''), 'Link do produto'))
  on conflict (item_id, loja_id) do update
    set preco = excluded.preco, link_produto = coalesce(excluded.link_produto, precos.link_produto);
  return 'salvo';
end $$;

-- ---------------------------------------------------------------- catálogo online
-- Mudar o preço/loja do produto atualiza o preço dos itens ligados a ele (como a importação faz).
create or replace function admin_salvar_produto(p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid := nullif(p ->> 'id', '')::uuid;
  v_nome text := _txt(p, 'nome');
  v_preco numeric := nullif(p ->> 'preco', '')::numeric;
  v_loja uuid := nullif(p ->> 'loja_id', '')::uuid;
begin
  perform _exige_edicao();
  if v_nome is null then perform _erro('Informe o nome do produto.'); end if;
  if v_preco is not null and (v_preco <= 0 or v_preco >= 100000) then perform _erro('Preço deve ser maior que zero e menor que R$ 100.000.'); end if;
  if v_loja is not null and not exists (select 1 from lojas where id = v_loja) then perform _erro('Loja não encontrada.'); end if;
  if v_id is null then
    insert into produtos_online (categoria, nome, link_produto, link_imagem, preco, loja_id, ativo)
    values (_txt(p, 'categoria'), v_nome, _url(_txt(p, 'link_produto'), 'Link do produto'), _url(_txt(p, 'link_imagem'), 'Imagem'),
            round(v_preco, 2), v_loja, _bool(p, 'ativo', true))
    returning id into v_id;
  else
    update produtos_online set
      nome = v_nome,
      categoria = case when p ? 'categoria' then _txt(p, 'categoria') else categoria end,
      link_produto = case when p ? 'link_produto' then _url(_txt(p, 'link_produto'), 'Link do produto') else link_produto end,
      link_imagem = case when p ? 'link_imagem' then _url(_txt(p, 'link_imagem'), 'Imagem') else link_imagem end,
      preco = case when p ? 'preco' then round(v_preco, 2) else preco end,
      loja_id = case when p ? 'loja_id' then v_loja else loja_id end,
      ativo = _bool(p, 'ativo', ativo)
    where id = v_id;
    if not found then perform _erro('Produto não encontrado.'); end if;
  end if;
  -- itens "obrigatórios" ligados a este produto: preço na loja do produto acompanha
  select preco, loja_id into v_preco, v_loja from produtos_online where id = v_id;
  if v_loja is not null and v_preco is not null then
    insert into precos (item_id, loja_id, preco, link_produto)
    select i.id, v_loja, v_preco, po.link_produto from itens_enxoval i join produtos_online po on po.id = i.produto_online_id
     where i.produto_online_id = v_id
    on conflict (item_id, loja_id) do update set preco = excluded.preco, link_produto = excluded.link_produto;
  end if;
  return v_id;
exception when unique_violation then perform _erro('Já existe um produto com esse nome no catálogo.'); return null;
end $$;

-- "Obrigatório em": cria/remove o item ligado ao produto em cada concurso (a lista enviada vira a verdade).
create or replace function admin_produto_concursos(p_id uuid, p_concursos uuid[]) returns int
language plpgsql security definer set search_path = public as $$
declare pr produtos_online; n int;
begin
  perform _exige_edicao();
  select * into pr from produtos_online where id = p_id;
  if not found then perform _erro('Produto não encontrado.'); end if;
  if exists (select 1 from unnest(p_concursos) c where not exists (select 1 from concursos where id = c)) then
    perform _erro('Concurso inválido.');
  end if;
  delete from itens_enxoval where produto_online_id = p_id and concurso_id <> all (p_concursos);
  insert into itens_enxoval (concurso_id, categoria, nome_padronizado, qtd_sugerida, link_foto, produto_online_id)
  select c, coalesce(pr.categoria, 'Outros'), pr.nome, 1, pr.link_imagem, p_id from unnest(p_concursos) c
  on conflict (concurso_id, nome_padronizado) do update set produto_online_id = excluded.produto_online_id;
  if pr.loja_id is not null and pr.preco is not null then
    insert into precos (item_id, loja_id, preco, link_produto)
    select id, pr.loja_id, pr.preco, pr.link_produto from itens_enxoval where produto_online_id = p_id
    on conflict (item_id, loja_id) do update set preco = excluded.preco;
  end if;
  select count(*) into n from itens_enxoval where produto_online_id = p_id;
  return n;
end $$;

create or replace function admin_excluir_produto(p_id uuid) returns text
language plpgsql security definer set search_path = public as $$
begin
  perform _exige_edicao();
  if exists (select 1 from itens_enxoval where produto_online_id = p_id) then
    perform _erro('Este produto é obrigatório em algum concurso: tire-o dos concursos antes de excluir.');
  end if;
  delete from produtos_online where id = p_id;
  if not found then perform _erro('Produto não encontrado.'); end if;
  return 'removido';
end $$;

-- ---------------------------------------------------------------- cupons
create or replace function admin_salvar_cupom(p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid := nullif(p ->> 'id', '')::uuid;
  v_cod text := upper(regexp_replace(coalesce(p ->> 'codigo', ''), '\s', '', 'g'));
  v_loja uuid := nullif(p ->> 'loja_id', '')::uuid;
  v_prods uuid[]; v_itens uuid[];
begin
  perform _exige_edicao();
  if v_cod !~ '^[A-Z0-9_-]{3,30}$' then perform _erro('Código do cupom: 3 a 30 letras, números, _ ou -.'); end if;
  if v_loja is not null and not exists (select 1 from lojas where id = v_loja) then perform _erro('Loja não encontrada.'); end if;
  if p ? 'produtos' then select coalesce(array_agg(x::uuid), '{}') into v_prods from jsonb_array_elements_text(p -> 'produtos') x; end if;
  if p ? 'itens'    then select coalesce(array_agg(x::uuid), '{}') into v_itens from jsonb_array_elements_text(p -> 'itens') x; end if;
  if v_id is null then
    insert into cupons (codigo, texto, loja_id, ativo) values (v_cod, _txt(p, 'texto'), v_loja, _bool(p, 'ativo', true))
    returning id into v_id;
  else
    update cupons set codigo = v_cod,
      texto = case when p ? 'texto' then _txt(p, 'texto') else texto end,
      loja_id = case when p ? 'loja_id' then v_loja else loja_id end,
      ativo = _bool(p, 'ativo', ativo)
    where id = v_id;
    if not found then perform _erro('Cupom não encontrado.'); end if;
  end if;
  if v_prods is not null then
    delete from cupom_produtos where cupom_id = v_id and produto_online_id <> all (v_prods);
    insert into cupom_produtos (cupom_id, produto_online_id) select v_id, x from unnest(v_prods) x
      where exists (select 1 from produtos_online where id = x) on conflict do nothing;
  end if;
  if v_itens is not null then
    delete from cupom_itens where cupom_id = v_id and item_id <> all (v_itens);
    insert into cupom_itens (cupom_id, item_id) select v_id, x from unnest(v_itens) x
      where exists (select 1 from itens_enxoval where id = x) on conflict do nothing;
  end if;
  return v_id;
exception when unique_violation then perform _erro('Já existe um cupom com esse código.'); return null;
end $$;

create or replace function admin_excluir_cupom(p_id uuid) returns text
language plpgsql security definer set search_path = public as $$
begin
  perform _exige_edicao();
  delete from cupons where id = p_id;                      -- vínculos vão junto (cascade)
  if not found then perform _erro('Cupom não encontrado.'); end if;
  return 'removido';
end $$;

-- ---------------------------------------------------------------- bizus
create or replace function admin_salvar_bizu(p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid := nullif(p ->> 'id', '')::uuid;
  v_conc uuid := nullif(p ->> 'concurso_id', '')::uuid;
  v_cat text := _txt(p, 'categoria');
  v_tit text := _txt(p, 'titulo');
  v_link text := _url(_txt(p, 'link'), 'Link');
  v_ordem int := nullif(p ->> 'ordem', '')::int;
begin
  perform _exige_edicao();
  if v_cat is null then perform _erro('Informe a categoria do bizu.'); end if;
  if v_tit is null then perform _erro('Informe o título do bizu.'); end if;
  if v_link is null then perform _erro('Informe o link do bizu.'); end if;
  if v_id is null then
    if v_conc is null or not exists (select 1 from concursos where id = v_conc) then perform _erro('Escolha o concurso do bizu.'); end if;
    insert into bizus (concurso_id, categoria, titulo, link, ordem, ativo)
    values (v_conc, v_cat, v_tit, v_link,
            coalesce(v_ordem, (select coalesce(max(ordem), 0) + 1 from bizus where concurso_id = v_conc)), _bool(p, 'ativo', true))
    returning id into v_id;
  else
    update bizus set categoria = v_cat, titulo = v_tit, link = v_link,
      ordem = case when p ? 'ordem' then coalesce(v_ordem, ordem) else ordem end,
      ativo = _bool(p, 'ativo', ativo)
    where id = v_id;
    if not found then perform _erro('Bizu não encontrado.'); end if;
  end if;
  return v_id;
exception when unique_violation then perform _erro('Já existe um bizu com esse título nessa categoria.'); return null;
end $$;

create or replace function admin_excluir_bizu(p_id uuid) returns text
language plpgsql security definer set search_path = public as $$
begin
  perform _exige_edicao();
  delete from bizus where id = p_id;
  if not found then perform _erro('Bizu não encontrado.'); end if;
  return 'removido';
end $$;

-- ---------------------------------------------------------------- histórico (leitura pela equipe)
drop function if exists admin_historico(text, uuid, int);
create function admin_historico(p_tabela text default null, p_registro uuid default null, p_limite int default 50)
returns table (criado_em timestamptz, tabela text, registro_id uuid, acao text, antes jsonb, depois jsonb, origem text, autor text)
language plpgsql stable security definer set search_path = public as $$
begin
  if not eh_admin() then raise exception 'acesso negado' using errcode = '42501'; end if;
  return query
    select a.criado_em, a.tabela, a.registro_id, a.acao, a.antes, a.depois, a.origem, u.email::text
      from alteracoes a left join auth.users u on u.id = a.alterado_por
     where (p_tabela is null or a.tabela = p_tabela) and (p_registro is null or a.registro_id = p_registro)
     order by a.criado_em desc, a.id desc
     limit least(greatest(coalesce(p_limite, 50), 1), 500);
end $$;

-- ---------------------------------------------------------------- permissões das funções públicas
do $$
declare f text;
begin
  foreach f in array array[
    'admin_salvar_concurso(jsonb)', 'admin_excluir_concurso(uuid)',
    'admin_salvar_loja(jsonb)', 'admin_excluir_loja(uuid)',
    'admin_salvar_item(jsonb)', 'admin_excluir_item(uuid)',
    'admin_salvar_preco(uuid, uuid, numeric, text)',
    'admin_salvar_produto(jsonb)', 'admin_produto_concursos(uuid, uuid[])', 'admin_excluir_produto(uuid)',
    'admin_salvar_cupom(jsonb)', 'admin_excluir_cupom(uuid)',
    'admin_salvar_bizu(jsonb)', 'admin_excluir_bizu(uuid)',
    'admin_historico(text, uuid, int)'] loop
    execute format('revoke execute on function %s from public, anon', f);
    execute format('grant execute on function %s to authenticated', f);
  end loop;
end $$;
