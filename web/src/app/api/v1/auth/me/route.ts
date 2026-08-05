import { NextResponse } from "next/server";
import { createSupabaseServerClient, loadUser } from "@/lib/supabase/server";

export async function GET() {
  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.getUser();
  if (error || !data.user) {
    return NextResponse.json(
      { error: { code: "AUTH_REQUIRED", message: "请先登录。" } },
      { status: 401 },
    );
  }

  const user = await loadUser(supabase, data.user);
  return NextResponse.json(user);
}
