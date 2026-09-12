import { NextResponse } from "next/server";
import { buildAgentRequest, proxyResponse } from "@/lib/server/agent-proxy";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * BFF pass-through for /api/v1/*. Authenticates the browser session via Supabase
 * cookies, then proxies to the agent service while preserving the SSE stream.
 */
async function proxy(path: string[], request: Request): Promise<Response> {
  const supabase = await createSupabaseServerClient();
  const { data: userData, error } = await supabase.auth.getUser();
  if (error || !userData.user) {
    return NextResponse.json(
      { error: { code: "AUTH_REQUIRED", message: "请先登录。" } },
      { status: 401 },
    );
  }
  const { data: sessionData } = await supabase.auth.getSession();
  const accessToken = sessionData?.session?.access_token;
  if (!accessToken) {
    return NextResponse.json(
      { error: { code: "AUTH_REQUIRED", message: "请先登录。" } },
      { status: 401 },
    );
  }

  const agentRequest = await buildAgentRequest(path, request, accessToken);
  const upstream = await fetch(agentRequest);
  return proxyResponse(upstream);
}

export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(path, request);
}

export async function POST(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(path, request);
}

export async function PUT(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(path, request);
}

export async function DELETE(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  return proxy(path, request);
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
