import { NextRequest, NextResponse } from "next/server";
import { BUNDLED_SFX, searchBundledSfx } from "@/media/bundled-catalog";

export async function GET(request: NextRequest) {
	const { searchParams } = new URL(request.url);
	const kind = searchParams.get("kind") || "sfx";
	const q = searchParams.get("q") || "";

	if (kind !== "sfx") {
		return NextResponse.json({
			kind,
			count: 0,
			results: [],
			note: "Only bundled sfx ships in this build. Music/LUT/sticker catalogs land next.",
		});
	}

	const results = searchBundledSfx(q).map((asset) => ({
		id: asset.id,
		name: asset.name,
		description: asset.description,
		url: asset.url,
		previewUrl: asset.previewUrl,
		downloadUrl: asset.url,
		duration: asset.duration,
		filesize: 0,
		type: "mp3",
		channels: 2,
		bitrate: 128000,
		bitdepth: 16,
		samplerate: 44100,
		username: asset.author,
		tags: asset.tags,
		license: asset.license,
		created: "2026-08-14",
		downloads: 0,
		rating: 5,
		ratingCount: 1,
		source: "bundled",
	}));

	return NextResponse.json({
		kind: "sfx",
		count: results.length,
		next: null,
		results,
		totalBundled: BUNDLED_SFX.length,
	});
}
