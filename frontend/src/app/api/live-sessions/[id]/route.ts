import { headers } from "next/headers";
import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

async function proxy(method: "GET" | "POST", context: { params: Promise<{ id: string }> }) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await context.params;
  const backend = process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
  try {
    const upstream = await fetch(`${backend.replace(/\/$/, "")}/live-sessions/${encodeURIComponent(id)}${method === "POST" ? "/stop" : ""}`, {
      method, headers: buildBackendAuthHeaders(session.user.id), cache: "no-store",
    });
    return new NextResponse(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
  } catch {
    return NextResponse.json({ error: "Recorder service unavailable" }, { status: 503 });
  }
}

export const GET = (_: Request, context: { params: Promise<{ id: string }> }) => proxy("GET", context);
export const POST = (_: Request, context: { params: Promise<{ id: string }> }) => proxy("POST", context);
