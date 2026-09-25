import { NextRequest, NextResponse } from "next/server";
import {
	BUNDLED_SFX,
	BUNDLED_LOGOS,
	BUNDLED_TEXT_PRESETS,
	AI_AGENT_RECIPES,
} from "@/media/bundled-catalog";

interface ComposeRequest {
	recipeId?: string;
	title?: string;
	hotline?: string;
	clips?: string[];
	aspectRatio?: "9:16" | "16:9" | "1:1" | "4:5";
	duration?: number;
	watermarkLogo?: string;
	addWhooshes?: boolean;
}

/**
 * Programmatic Video Timeline Composer for AI Agents.
 * Enables Shannon (Voice), The Publicist (Postiz), The Closer, and Manus Brain
 * to assemble complete multi-track video timelines with voiceover, visual clips,
 * brand watermark, lower-third overlays, and Foley audio cues.
 */
export async function POST(request: NextRequest) {
	try {
		const body: ComposeRequest = await request.json();
		const recipe =
			AI_AGENT_RECIPES.find((r) => r.id === body.recipeId) ||
			AI_AGENT_RECIPES[0];

		const aspectRatio = body.aspectRatio || recipe.aspectRatio;
		const totalDuration = body.duration || recipe.defaultDuration;
		const title = body.title || "24/7 Fast Bail Bonds";
		const hotline = body.hotline || "(239) 955-0178";
		const watermarkSlug = body.watermarkLogo || recipe.overlayLogo;
		const logoAsset =
			BUNDLED_LOGOS.find((l) => l.slug === watermarkSlug) || BUNDLED_LOGOS[0];
		const introSfxAsset = BUNDLED_SFX.find((s) => s.slug === recipe.introSfx);
		const outroSfxAsset = BUNDLED_SFX.find((s) => s.slug === recipe.outroSfx);

		// Build structured timeline scene
		const timeline = {
			version: "1.0",
			aspectRatio,
			duration: totalDuration,
			dimensions:
				aspectRatio === "9:16"
					? { width: 1080, height: 1920 }
					: aspectRatio === "16:9"
						? { width: 1920, height: 1080 }
						: { width: 1080, height: 1080 },
			tracks: [
				{
					id: "track-video-primary",
					type: "video",
					name: "Primary Video & Clips",
					elements: (body.clips || []).map((url, i, arr) => {
						const clipDur = totalDuration / Math.max(arr.length, 1);
						return {
							id: `clip-${i + 1}`,
							type: "video",
							src: url,
							startTime: i * clipDur,
							duration: clipDur,
							transition: {
								type: recipe.transitionType,
								duration: 0.5,
							},
						};
					}),
				},
				{
					id: "track-watermark-overlay",
					type: "overlay",
					name: "Brand Logo Bug",
					elements: [
						{
							id: "elem-logo-bug",
							type: "image",
							src: logoAsset.url,
							name: logoAsset.name,
							startTime: 0,
							duration: totalDuration,
							position: recipe.watermarkPosition,
							scale: 0.18,
							opacity: 0.85,
						},
					],
				},
				{
					id: "track-text-lowerthird",
					type: "text",
					name: "Hotline & Title Banner",
					elements: [
						{
							id: "elem-headline",
							type: "text",
							text: title,
							startTime: 0.5,
							duration: 4.5,
							style: "gold-alert",
							fontSize: 48,
							position: "top-center",
						},
						{
							id: "elem-hotline-banner",
							type: "text",
							text: `CALL 24/7: ${hotline}`,
							startTime: 2.0,
							duration: totalDuration - 2.0,
							style: "emerald-glow",
							fontSize: 44,
							position: "bottom-center",
						},
					],
				},
				{
					id: "track-audio-sfx",
					type: "audio",
					name: "Foley & Sound Design",
					elements: [
						...(introSfxAsset
							? [
									{
										id: "sfx-intro",
										type: "audio",
										src: introSfxAsset.url,
										name: introSfxAsset.name,
										startTime: 0.0,
										duration: introSfxAsset.duration,
										volume: 1.0,
									},
								]
							: []),
						...(outroSfxAsset
							? [
									{
										id: "sfx-outro",
										type: "audio",
										src: outroSfxAsset.url,
										name: outroSfxAsset.name,
										startTime: Math.max(0, totalDuration - (outroSfxAsset.duration || 1.0)),
										duration: outroSfxAsset.duration,
										volume: 1.0,
									},
								]
							: []),
					],
				},
			],
			metadata: {
				generatedBy: "ShamrockLeads Autonomous AI Video Composer",
				recipeUsed: recipe.name,
				timestamp: new Date().toISOString(),
				editUrl: `https://edit.shamrockbailbonds.biz/editor?aspectRatio=${aspectRatio}`,
			},
		};

		return NextResponse.json({
			success: true,
			recipeId: recipe.id,
			recipeName: recipe.name,
			timeline,
		});
	} catch (error) {
		return NextResponse.json(
			{
				success: false,
				error: error instanceof Error ? error.message : "Composition error",
			},
			{ status: 400 },
		);
	}
}
