import { NextRequest, NextResponse } from "next/server";
import { AI_AGENT_RECIPES } from "@/media/bundled-catalog";

export async function GET(request: NextRequest) {
	return NextResponse.json({
		count: AI_AGENT_RECIPES.length,
		recipes: AI_AGENT_RECIPES,
		usage: "Submit a recipe ID to POST /api/agent/compose with clip URLs and custom title/hotline text.",
	});
}
