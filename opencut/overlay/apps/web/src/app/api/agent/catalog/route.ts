import { NextRequest, NextResponse } from "next/server";
import {
	BUNDLED_SFX,
	BUNDLED_LOGOS,
	BUNDLED_ICONS,
	BUNDLED_IMAGES,
	BUNDLED_TEXT_PRESETS,
	AI_AGENT_RECIPES,
} from "@/media/bundled-catalog";

/**
 * AI Agent Catalog Manifest.
 * Machine-readable endpoint for digital workforce employees (Shannon, The Publicist, Manus, etc.)
 * to inspect all video production assets, tools, sound cues, and text templates.
 */
export async function GET(request: NextRequest) {
	return NextResponse.json({
		platform: "OpenCut Video Studio",
		host: "edit.shamrockbailbonds.biz",
		organization: "Shamrock Bail Bonds LLC",
		version: "2026.1-production",
		description:
			"Autonomous AI video editing suite with bundled legal assets, Foley SFX, vector brand crests, Florida Man creative art, and timeline composition API.",
		agentCapabilities: {
			programmaticComposition: true,
			aspectRatios: ["9:16", "16:9", "1:1", "4:5"],
			supportedImportFormats: ["mp4", "webm", "mov", "mp3", "wav", "png", "jpg", "svg", "webp"],
			effectsEngine: "WebGL Shader Pipeline (glitch, blur, chromatic, film-grain, stylize, vignette)",
			transitionsEngine: "Canvas WebGL (fade, wipe, slide, dissolve, zoom)",
			audioFoley: "Shamrock Bundled Sound Effects Catalog",
			compositionEndpoint: "POST /api/agent/compose",
			recipesEndpoint: "GET /api/agent/recipes",
		},
		catalog: {
			sfxCount: BUNDLED_SFX.length,
			logosCount: BUNDLED_LOGOS.length,
			iconsCount: BUNDLED_ICONS.length,
			imagesCount: BUNDLED_IMAGES.length,
			textPresetsCount: BUNDLED_TEXT_PRESETS.length,
			recipesCount: AI_AGENT_RECIPES.length,
		},
		assets: {
			sfx: BUNDLED_SFX,
			logos: BUNDLED_LOGOS,
			icons: BUNDLED_ICONS,
			images: BUNDLED_IMAGES,
			textPresets: BUNDLED_TEXT_PRESETS,
			recipes: AI_AGENT_RECIPES,
		},
	});
}
