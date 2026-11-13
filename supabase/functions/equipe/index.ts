// Enxoval do Aprovado — Edge Function `equipe`
// Convida e remove membros do painel administrativo. Só o PROPRIETÁRIO (admins.papel)
// pode chamar. Usa a service_role (variável de ambiente do Supabase, nunca no navegador).
//
//   POST { acao: "convidar", email, papel: "admin" | "leitor" }
//   POST { acao: "remover",  user_id }
//
// Deploy: supabase functions deploy equipe   (verify_jwt ligado)
import { createClient } from "npm:@supabase/supabase-js@2";

const ORIGENS = ["https://enxovaldoaprovado.com.br", "http://localhost:8765", "http://127.0.0.1:8765"];
const PAPEIS = ["admin", "leitor"];

Deno.serve(async (req) => {
  const origem = req.headers.get("origin") || "";
  const cors = {
    "Access-Control-Allow-Origin": ORIGENS.includes(origem) ? origem : ORIGENS[0],
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
  const resp = (status: number, corpo: Record<string, unknown>) =>
    new Response(JSON.stringify(corpo), { status, headers: { ...cors, "Content-Type": "application/json" } });
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return resp(405, { erro: "Método não permitido." });

  const adm = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  // quem está chamando?
  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  const { data: quem, error: eQuem } = await adm.auth.getUser(token);
  if (eQuem || !quem?.user) return resp(401, { erro: "Sessão inválida. Entre de novo." });
  const eu = quem.user.id;
  const { data: meu } = await adm.from("admins").select("papel").eq("user_id", eu).maybeSingle();
  if (meu?.papel !== "proprietario") return resp(403, { erro: "Só o proprietário gerencia a equipe." });

  let corpo: Record<string, unknown>;
  try { corpo = await req.json(); } catch { return resp(400, { erro: "Pedido inválido." }); }

  // ---------------- convidar ----------------
  if (corpo.acao === "convidar") {
    const email = String(corpo.email || "").trim().toLowerCase();
    const papel = String(corpo.papel || "");
    if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return resp(400, { erro: "E-mail inválido." });
    if (!PAPEIS.includes(papel)) return resp(400, { erro: "Papel inválido." });

    const { data: existenteId, error: eBusca } = await adm.rpc("_usuario_por_email", { p_email: email });
    if (eBusca) return resp(500, { erro: "Não foi possível conferir o e-mail." });
    let uid = existenteId as string | null;
    const existente = !!uid;

    if (uid) {
      const { data: ja } = await adm.from("admins").select("user_id").eq("user_id", uid).maybeSingle();
      if (ja) return resp(409, { erro: "Esse e-mail já faz parte da equipe." });
    } else {
      // conta nova: o Supabase manda o e-mail de convite; o link abre o admin.html para criar a senha
      const redirectTo = (ORIGENS.includes(origem) ? origem : ORIGENS[0]) + "/admin.html";
      const { data, error } = await adm.auth.admin.inviteUserByEmail(email, { redirectTo });
      if (error || !data?.user) {
        const limite = error?.status === 429 || /rate limit/i.test(error?.message || "");
        return resp(limite ? 429 : 500, {
          erro: limite ? "Limite de e-mails por hora atingido. Tente mais tarde." : "Não foi possível enviar o convite.",
        });
      }
      uid = data.user.id;
    }

    const { error: eIns } = await adm.from("admins").insert({ user_id: uid, papel, convidado_por: eu });
    if (eIns) {
      if (!existente) await adm.auth.admin.deleteUser(uid!);
      return resp(500, { erro: "Não foi possível adicionar à equipe." });
    }
    return resp(200, { ok: true, existente });
  }

  // ---------------- remover ----------------
  if (corpo.acao === "remover") {
    const alvo = String(corpo.user_id || "");
    if (!alvo) return resp(400, { erro: "Membro não informado." });
    if (alvo === eu) return resp(400, { erro: "Você não pode remover a si mesmo." });
    const { data: m } = await adm.from("admins").select("papel").eq("user_id", alvo).maybeSingle();
    if (!m) return resp(404, { erro: "Membro não encontrado." });
    if (m.papel === "proprietario") return resp(400, { erro: "O proprietário não pode ser removido." });

    const { error: eDel } = await adm.from("admins").delete().eq("user_id", alvo);
    if (eDel) return resp(500, { erro: "Não foi possível remover." });

    // conta criada só pelo convite e nunca usada (e sem loja) → apaga, para poder convidar de novo
    const { data: info } = await adm.auth.admin.getUserById(alvo);
    const { count } = await adm.from("loja_usuarios").select("user_id", { count: "exact", head: true }).eq("user_id", alvo);
    if (info?.user && !info.user.last_sign_in_at && !count) await adm.auth.admin.deleteUser(alvo);
    return resp(200, { ok: true });
  }

  return resp(400, { erro: "Ação desconhecida." });
});
