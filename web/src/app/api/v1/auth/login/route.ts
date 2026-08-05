import { NextResponse } from "next/server";
import { createSupabaseServerClient, loadUser } from "@/lib/supabase/server";

export async function POST(request: Request) {
  let body: { email?: unknown; password?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: { code: "VALIDATION_FAILED", message: "请求体无效。" } },
      { status: 422 },
    );
  }

  const email = typeof body.email === "string" ? body.email.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (!email || !password) {
    return NextResponse.json(
      { error: { code: "VALIDATION_FAILED", message: "邮箱和密码不能为空。" } },
      { status: 422 },
    );
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error || !data.user) {
    return NextResponse.json(
      { error: { code: "AUTH_REQUIRED", message: "邮箱或密码错误。" } },
      { status: 401 },
    );
  }

  const user = await loadUser(supabase, data.user);
  return NextResponse.json(user);
}
