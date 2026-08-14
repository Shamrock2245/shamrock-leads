/**
 * Shamrock bundled media catalog.
 * License-clean, commercial-safe, ships with the editor (no Freesound required).
 */

export type BundledKind = "sfx" | "music" | "lut" | "sticker" | "preset";

export interface BundledAsset {
	id: number;
	slug: string;
	kind: BundledKind;
	name: string;
	description: string;
	url: string;
	previewUrl: string;
	duration: number;
	tags: string[];
	license: "CC0" | "CC-BY" | "Shamrock-original";
	author: string;
	sourceUrl: string;
	commercial: true;
}

export const BUNDLED_SFX: BundledAsset[] = [
	sfx(900001, "whoosh-fast", "Fast Whoosh", "Short left-to-right whoosh for hard cuts", 0.45, ["whoosh", "swipe", "transition"]),
	sfx(900002, "whoosh-long", "Long Whoosh", "Longer riser-whoosh for scene changes", 0.9, ["whoosh", "riser", "transition"]),
	sfx(900003, "hit-punch", "Punch Hit", "Tight percussive hit for text pops", 0.35, ["hit", "punch", "impact"]),
	sfx(900004, "hit-boom", "Cinematic Boom", "Low boom for logo slams", 0.8, ["hit", "boom", "cinematic"]),
	sfx(900005, "riser-short", "Short Riser", "Tension riser into a cut", 1.2, ["riser", "tension"]),
	sfx(900006, "click-ui", "UI Click", "Clean click for UI / list beats", 0.2, ["click", "ui"]),
	sfx(900007, "pop-soft", "Soft Pop", "Soft pop for captions", 0.25, ["pop", "caption"]),
	sfx(900008, "swipe", "Swipe", "Paper/air swipe", 0.4, ["swipe", "whoosh"]),
	sfx(900009, "glitch-stutter", "Glitch Stutter", "Digital stutter for glitch presets", 0.5, ["glitch", "stutter"]),
	sfx(900010, "notification", "Notification", "Short chime", 0.6, ["notification", "chime"]),
	sfx(900011, "phone-ring", "Phone Ring", "Smartphone ring — intake / after-hours ads", 2.0, ["phone", "ring", "shamrock"]),
	sfx(900012, "gavel", "Court Gavel", "Wooden gavel strike", 0.5, ["gavel", "court", "shamrock"]),
	sfx(900013, "jail-door", "Jail Door", "Heavy metal door slam", 1.1, ["jail", "door", "metal", "shamrock"]),
	sfx(900014, "heartbeat", "Heartbeat", "Low heartbeat pulse", 1.4, ["heartbeat", "tension", "shamrock"]),
	sfx(900015, "rumble", "Sub Rumble", "Low rumble bed", 2.0, ["rumble", "bass"]),
];

function sfx(
	id: number,
	slug: string,
	name: string,
	description: string,
	duration: number,
	tags: string[],
): BundledAsset {
	const url = `/sfx/${slug}.mp3`;
	return {
		id,
		slug,
		kind: "sfx",
		name,
		description,
		url,
		previewUrl: url,
		duration,
		tags,
		license: "Shamrock-original",
		author: "Shamrock Bail Bonds",
		sourceUrl: "https://edit.shamrockbailbonds.biz/sfx/",
		commercial: true,
	};
}

export function searchBundledSfx(query: string): BundledAsset[] {
	const q = query.trim().toLowerCase();
	if (!q) return BUNDLED_SFX;
	return BUNDLED_SFX.filter((asset) => {
		const hay = [asset.name, asset.description, asset.slug, ...asset.tags]
			.join(" ")
			.toLowerCase();
		return hay.includes(q);
	});
}
