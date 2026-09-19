-- ============================================================================
-- Enxoval do Aprovado — Etapa 2: leitura pública (anon) das tabelas de catálogo
-- Rode no SQL Editor. Reexecutável.
-- (A tabela `leads` NÃO recebe SELECT para anon — continua protegida.)
-- ============================================================================

-- privilégio de tabela para o papel anon (só leitura)
grant select on concursos, lojas, loja_concurso, itens_enxoval, produtos_online, precos to anon;

-- liga RLS e cria a policy de leitura pública em cada tabela
alter table concursos       enable row level security;
alter table lojas           enable row level security;
alter table loja_concurso   enable row level security;
alter table itens_enxoval   enable row level security;
alter table produtos_online enable row level security;
alter table precos          enable row level security;

drop policy if exists leitura_publica on concursos;
create policy leitura_publica on concursos       for select to anon using (true);
drop policy if exists leitura_publica on lojas;
create policy leitura_publica on lojas           for select to anon using (true);
drop policy if exists leitura_publica on loja_concurso;
create policy leitura_publica on loja_concurso   for select to anon using (true);
drop policy if exists leitura_publica on itens_enxoval;
create policy leitura_publica on itens_enxoval   for select to anon using (true);
drop policy if exists leitura_publica on produtos_online;
create policy leitura_publica on produtos_online for select to anon using (true);
drop policy if exists leitura_publica on precos;
create policy leitura_publica on precos          for select to anon using (true);
