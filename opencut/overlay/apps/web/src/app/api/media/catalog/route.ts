import { NextRequest, NextResponse } from "next/server";
import {
	BUNDLED_SFX,
	BUNDLED_LOGOS,
	BUNDLED_ICONS,
	BUNDLED_IMAGES,
	BUNDLED_TEXT_PRESETS,
	AI_AGENT_RECIPES,
	searchBundledCatalog,
} from "@/media/bundled-catalog";

export async function GET(request: NextRequest) {
	const { searchParams } = new URL(request.url);
	const kind = searchParams.get("kind") || "all";
	const q = searchParams.get("q") || "";

	if (kind === "presets" || kind === "text") {
		return NextResponse.json({
			kind: "text",
			count: BUNDLED_TEXT_PRESETS.length,
			results: BUNDLED_TEXT_PRESETS,
		});
	}

	if (kind === "recipes") {
		return NextResponse.json({
			kind: "recipes",
			count: AI_AGENT_RECIPES.length,
			results: AI_AGENT_RECIPES,
		});
	}

	const results = searchBundledCatalog({ kind, query: q }).map((asset) => ({
		id: asset.id,
		slug: asset.slug,
		kind: asset.kind,
		name: asset.name,
		description: asset.description,
		url: asset.url,
		previewUrl: asset.previewUrl,
		downloadUrl: asset.url,
		duration: asset.duration || 0,
		format: asset.format,
		category: asset.category,
		tags: asset.tags,
		license: asset.license,
		author: asset.author,
		source: "bundled",
	}));

	return NextResponse.json({
		kind,
		count: results.length,
		query: q || null,
		results,
		breakdown: {
			sfx: BUNDLED_SFX.length,
			logos: BUNDLED_LOGOS.length,
			icons: BUNDLED_ICONS.length,
			images: BUNDLED_IMAGES.length,
			textPresets: BUNDLED_TEXT_PRESETS.length,
			recipes: AI_AGENT_RECIPES.length,
		},
	});
}
