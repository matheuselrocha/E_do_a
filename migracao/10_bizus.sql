-- ============================================================================
-- Enxoval do Aprovado — Bizus por concurso (abas "<concurso> - Bizus para o CFP")
-- Rode no SQL Editor DEPOIS do 09_dados_completos.sql. Reexecutável.
--
-- Cada bizu é um link informativo (Drive, YouTube, PDF, Spotify...) agrupado por
-- categoria e ligado a UM concurso. Leitura pública; escrita só service_role
-- (importação) até as regras de escrita da etapa 4.
-- ============================================================================

create table if not exists bizus (
  id          uuid primary key default gen_random_uuid(),
  concurso_id uuid not null references concursos(id) on delete cascade,
  categoria   text not null,
  titulo      text not null,
  link        text not null,
  ordem       int  not null default 0,          -- ordem da linha na planilha
  ativo       boolean not null default true,
  created_at  timestamptz not null default now(),
  unique (concurso_id, categoria, titulo)
);
create index if not exists idx_bizus_concurso on bizus (concurso_id, ordem);

alter table bizus enable row level security;
revoke all on bizus from anon, authenticated;
grant select on bizus to anon, authenticated;
grant all on bizus to service_role;
drop policy if exists leitura_publica on bizus;
create policy leitura_publica on bizus for select to anon using (ativo);
drop policy if exists leitura_logada on bizus;
create policy leitura_logada on bizus for select to authenticated using (ativo or eh_admin());

drop trigger if exists trg_alteracao on bizus;
create trigger trg_alteracao after insert or update or delete on bizus
  for each row execute function registrar_alteracao();
