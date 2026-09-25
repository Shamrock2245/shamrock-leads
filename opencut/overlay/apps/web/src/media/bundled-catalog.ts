/**
 * Shamrock bundled media catalog for OpenCut (edit.shamrockbailbonds.biz).
 * License-clean, commercial-safe, ships with the editor.
 * Contains:
 *  - Bundled SFX (17 production sound effects)
 *  - Bundled Logos (25 vector SVGs and high-res brand emblems)
 *  - Bundled Icons (21 UI & marketing vector SVG icons)
 *  - Bundled Images (18 bail bond photos, Florida Man ads, and scene assets)
 *  - Bundled Text Presets (Bail hotline, web callout, lower-thirds)
 *  - AI Agent Recipes (Automated timeline recipes for AI agents)
 */

export type BundledKind = "sfx" | "logos" | "icons" | "images" | "text" | "recipes";

export interface BundledAsset {
	id: number;
	slug: string;
	kind: BundledKind;
	name: string;
	description: string;
	url: string;
	previewUrl: string;
	duration?: number;
	width?: number;
	height?: number;
	format: "mp3" | "svg" | "png" | "jpg" | "webp" | "json";
	tags: string[];
	license: "Shamrock-original" | "CC0" | "Commercial-clean";
	author: string;
	category: string;
}

export const BUNDLED_SFX: BundledAsset[] = [
	sfx(900001, "whoosh-fast", "Fast Whoosh", "Short left-to-right whoosh for hard cuts", 0.45, ["whoosh", "swipe", "transition"], "Transitions"),
	sfx(900002, "whoosh-long", "Long Whoosh", "Longer riser-whoosh for scene changes", 0.9, ["whoosh", "riser", "transition"], "Transitions"),
	sfx(900003, "hit-punch", "Punch Hit", "Tight percussive hit for text pops", 0.35, ["hit", "punch", "impact"], "Impacts"),
	sfx(900004, "hit-boom", "Cinematic Boom", "Low boom for logo slams", 0.8, ["hit", "boom", "cinematic"], "Impacts"),
	sfx(900005, "riser-short", "Short Riser", "Tension riser into a cut", 1.2, ["riser", "tension"], "Transitions"),
	sfx(900006, "click-ui", "UI Click", "Clean click for UI / list beats", 0.2, ["click", "ui"], "UI"),
	sfx(900007, "pop-soft", "Soft Pop", "Soft pop for captions and sticker pops", 0.25, ["pop", "caption"], "UI"),
	sfx(900008, "swipe", "Swipe", "Paper/air swipe for wipe transitions", 0.4, ["swipe", "whoosh"], "Transitions"),
	sfx(900009, "glitch-stutter", "Glitch Stutter", "Digital stutter for glitch presets", 0.5, ["glitch", "stutter"], "Transitions"),
	sfx(900010, "notification", "Notification", "Short chime for intake alert", 0.6, ["notification", "chime"], "UI"),
	sfx(900011, "phone-ring", "Phone Ring", "Smartphone ring — intake / 24/7 hotline ads", 2.0, ["phone", "ring", "hotline", "shamrock"], "Bail"),
	sfx(900012, "gavel", "Court Gavel", "Wooden gavel strike for judge/court scene", 0.5, ["gavel", "court", "judge", "shamrock"], "Bail"),
	sfx(900013, "jail-door", "Jail Door Slam", "Heavy metal cell door slam outro", 1.1, ["jail", "door", "metal", "lockup", "shamrock"], "Bail"),
	sfx(900014, "heartbeat", "Heartbeat Pulse", "Low tension heartbeat pulse for arrest drama", 1.4, ["heartbeat", "tension", "shamrock"], "Tension"),
	sfx(900015, "rumble", "Sub Rumble Bed", "Low bass rumble bed", 2.0, ["rumble", "bass", "impact"], "Impacts"),
	sfx(900016, "handcuffs-click", "Handcuffs Lock", "Metallic handcuffs double ratchet click", 0.4, ["handcuffs", "arrest", "cuffs", "shamrock"], "Bail"),
	sfx(900017, "cash-register", "Bond Posted Ding", "Bright cash register bell for posted bond", 0.6, ["cash", "money", "premium", "posted"], "UI"),
];

export const BUNDLED_LOGOS: BundledAsset[] = [
	// Vector SVGs
	logo(910001, "shamrock-logo-bw", "Shamrock Logo B&W (Vector)", "Official vector Shamrock Bail Bonds logo in high-contrast black & white", "/brand/logos/shamrock-logo-bw.svg", "svg", ["vector", "shamrock", "official", "bw"], "Vector Logos"),
	logo(910002, "shamrock-site-logo", "Shamrock Web Portal Logo (Vector)", "Clean modern web header SVG logo from shamrockbailbonds.biz", "/brand/logos/shamrock-site-logo.svg", "svg", ["vector", "web", "header", "shamrock"], "Vector Logos"),
	logo(910003, "shamrock-vector-badge", "Shamrock Circular Vector Badge", "Circular vector brand badge with clover emblem", "/brand/logos/shamrock-vector-badge.svg", "svg", ["vector", "badge", "circular", "clover"], "Vector Logos"),
	logo(910004, "shamrock-bw-emblem", "Shamrock Legal Paperwork Crest (Vector)", "Official surety paperwork and bond header emblem", "/brand/logos/shamrock-bw-emblem.svg", "svg", ["vector", "paperwork", "legal", "surety"], "Vector Logos"),
	logo(910005, "shamrock-telegram-logo", "Shamrock Telegram Bot Vector Logo", "Telegram @ShamrockBail_bot official vector brand mark", "/brand/logos/shamrock-telegram-logo.svg", "svg", ["vector", "telegram", "bot", "mobile"], "Vector Logos"),
	logo(910006, "007-cuffs", "007 Bail Cuffs Vector", "007 Handcuffs vector logo", "/brand/logos/007-cuffs.svg", "svg", ["vector", "007", "handcuffs"], "007 Bail"),
	logo(910007, "007-horizontal-logo", "007 Bail Horizontal Logo (Vector)", "007 Bail Bonds wide horizontal vector header", "/brand/logos/007-horizontal-logo.svg", "svg", ["vector", "007", "horizontal"], "007 Bail"),
	logo(910008, "007-barrel-logo", "007 Bail Gun Barrel Logo (Vector)", "007 Gun barrel vector logo icon", "/brand/logos/007-barrel-logo.svg", "svg", ["vector", "007", "barrel"], "007 Bail"),

	// Transparent PNGs and High-Res Badges
	logo(910009, "shamrock-pub-transparent", "Shamrock Three-Leaf Pub Crest", "Transparent 3-leaf Irish pub style circular crest — flagship brand mark", "/brand/logos/shamrock-pub-transparent.png", "png", ["transparent", "crest", "pub", "flagship"], "Transparent Badges"),
	logo(910010, "shamrock-logo-850", "Shamrock 850x850 Master Logo", "High-res square master logo for overlays and bug watermarks", "/brand/logos/shamrock-logo-850.png", "png", ["master", "square", "overlay", "high-res"], "Master Badges"),
	logo(910011, "shamrock-official-transparent", "Shamrock Official Transparent Logo", "Clean cutout transparent logo with golden clover typography", "/brand/logos/shamrock-official-transparent.png", "png", ["transparent", "gold", "official"], "Transparent Badges"),
	logo(910012, "shamrock-tiktok-transparent", "Shamrock TikTok Transparent Logo", "Transparent logo tailored for vertical Reels & TikTok videos", "/brand/logos/shamrock-tiktok-transparent.png", "png", ["tiktok", "vertical", "reels", "transparent"], "Social Badges"),
	logo(910013, "shamrock-bnw-720", "Shamrock B&W 720 Badge", "Sleek monochrome badge for gritty legal and arrest clips", "/brand/logos/shamrock-bnw-720.png", "png", ["monochrome", "badge", "noir", "legal"], "Master Badges"),
	logo(910014, "shamrock-sticker-etsy", "Shamrock Die-Cut Sticker", "Die-cut sticker style emblem with white border", "/brand/logos/shamrock-sticker-etsy.png", "png", ["sticker", "border", "merch"], "Stickers"),
	logo(910015, "bail-butler-logo", "Bail Butler AI Logo", "Official brand mark for Bail Butler automated intake assistant", "/brand/logos/bail-butler-logo.png", "png", ["butler", "ai", "intake"], "Bail Butler"),
	logo(910016, "bail-butler-large", "Bail Butler Large Emblem", "Large format Bail Butler crest for intros and outros", "/brand/logos/bail-butler-large.png", "png", ["butler", "large", "crest"], "Bail Butler"),
	logo(910017, "007-cuffs-transparent", "007 Cuffs Transparent Cutout", "007 Handcuffs transparent cutout for video watermarks", "/brand/logos/007-cuffs-transparent.png", "png", ["007", "cuffs", "transparent"], "007 Bail"),
	logo(910018, "007-logo-updated", "007 Bail Bonds Updated Crest", "Full color modern 007 Bail Bonds emblem", "/brand/logos/007-logo-updated.png", "png", ["007", "emblem", "color"], "007 Bail"),
	logo(910019, "shamrock-dashboard-badge", "Shamrock Lead CRM Badge", "Official ShamrockLeads Auto-CRM application badge", "/brand/logos/shamrock-dashboard-badge.png", "png", ["crm", "leads", "badge"], "Master Badges"),
];

export const BUNDLED_ICONS: BundledAsset[] = [
	icon(920001, "gavel", "Court Gavel Icon", "Judge gavel for court appearance and docket videos", "/brand/icons/gavel-alt.svg", ["gavel", "court", "judge", "legal"]),
	icon(920002, "credit-card", "Credit Card / Payment Icon", "SwipeSimple payment and financing icon", "/brand/icons/credit-card.svg", ["card", "payment", "finance"]),
	icon(920003, "currency-circle-dollar", "Dollar / Bond Premium Icon", "Bond amount and premium fee icon", "/brand/icons/currency-circle-dollar.svg", ["dollar", "money", "premium"]),
	icon(920004, "currency-btc", "Bitcoin Crypto Icon", "Cryptocurrency collateral accepted icon", "/brand/icons/currency-btc.svg", ["crypto", "btc", "bitcoin"]),
	icon(920005, "currency-eth", "Ethereum Crypto Icon", "Ethereum accepted icon", "/brand/icons/currency-eth.svg", ["crypto", "eth", "ethereum"]),
	icon(920006, "gps-fix", "GPS Tracking Icon", "GPS check-in and client tracking icon", "/brand/icons/gps-fix.svg", ["gps", "location", "tracking"]),
	icon(920007, "folder-user", "Defendant Case File Icon", "Active bonded case file and client record icon", "/brand/icons/folder-user.svg", ["case", "folder", "client"]),
	icon(920008, "file-pdf", "Bond Paperwork PDF Icon", "Surety appearance bond PDF packet icon", "/brand/icons/file-pdf.svg", ["pdf", "paperwork", "docuseal"]),
	icon(920009, "file-video", "Video Reel Asset Icon", "Video clip and footage marker icon", "/brand/icons/file-video.svg", ["video", "clip", "reel"]),
	icon(920010, "flag-pennant", "County Priority Flag Icon", "County jail roster alert and hotspot flag", "/brand/icons/flag-pennant.svg", ["flag", "county", "alert"]),
	icon(920011, "telegram-logo", "Telegram Bot Icon", "24/7 Telegram intake channel icon", "/brand/icons/telegram-logo.svg", ["telegram", "bot", "chat"]),
	icon(920012, "instagram-logo", "Instagram Reels Icon", "Instagram social outreach icon", "/brand/icons/instagram-logo-alt.svg", ["instagram", "reels", "social"]),
	icon(920013, "device-rotate", "Rotate Phone Icon", "Mobile vertical screen orientation prompt", "/brand/icons/device-rotate.svg", ["mobile", "rotate", "phone"]),
	icon(920014, "desktop", "Operations Command Icon", "Desktop CRM and bond desk workstation icon", "/brand/icons/desktop.svg", ["desktop", "crm", "ops"]),
	icon(920015, "download-simple", "Fast Download Icon", "Quick release paperwork download icon", "/brand/icons/download-simple.svg", ["download", "arrow", "save"]),
	icon(920016, "files", "Multi-Doc Packet Icon", "14-document surety signing packet icon", "/brand/icons/files.svg", ["documents", "packet", "surety"]),
	icon(920017, "folders", "Archive Folders Icon", "County case archives and discharge records", "/brand/icons/folders.svg", ["folders", "archive", "court"]),
	icon(920018, "cloud-arrow-down", "Cloud Ingest Icon", "Roster scraper data sync icon", "/brand/icons/cloud-arrow-down.svg", ["cloud", "sync", "scraper"]),
	icon(920019, "image", "Mugshot / Photo Icon", "Booking photo and ID verification icon", "/brand/icons/image.svg", ["photo", "mugshot", "id"]),
];

export const BUNDLED_IMAGES: BundledAsset[] = [
	image(930001, "florida-man-alligator-wrestle", "Florida Man Gator Wrestle Ad", "Vibrant pseudoretrofuturistic ad featuring bondsman wrestling alligator in front of Shamrock office", "/brand/images/florida-man-alligator-wrestle.webp", "webp", ["florida-man", "alligator", "wrestle", "ad", "retro"], "Florida Man Ads"),
	image(930002, "florida-man-gator-ride", "Florida Man Gator Night Ride", "GoPro suburban night shot of Florida man riding alligator", "/brand/images/florida-man-gator-ride.png", "png", ["florida-man", "alligator", "night", "gopro"], "Florida Man Ads"),
	image(930003, "hide-seek-champs-emblem", "Hide & Seek Champs Est. 2012", "Iconic circular retro seal: 'Hide & Seek Champs Est. 2012'", "/brand/images/hide-seek-champs-emblem.webp", "webp", ["hide-and-seek", "champs", "est-2012", "retro"], "Brand Art"),
	image(930004, "from-cuffs-to-comfort", "From Cuffs to Comfort", "Signature branding graphic: transition from jail handcuffs to freedom", "/brand/images/from-cuffs-to-comfort.png", "png", ["cuffs", "comfort", "freedom", "release"], "Brand Art"),
	image(930005, "shamrock-youtube-endscreen", "Shamrock YouTube End Screen", "Professional branded YouTube & video outro end screen with hotline call-to-action", "/brand/images/shamrock-youtube-endscreen.png", "png", ["endscreen", "youtube", "outro", "cta"], "Outros & Intros"),
	image(930006, "bail-intro-cover", "Bail Intro Video Cover", "Cinematic thumbnail and cover frame for bail bond guides", "/brand/images/bail-intro-cover.jpg", "jpg", ["intro", "cover", "cinematic", "guide"], "Outros & Intros"),
	image(930007, "shamrock-club-stage", "Shamrock Rap Star Club Stage", "Wide-angle music video club stage with Florida bail bondsman flair", "/brand/images/shamrock-club-stage.webp", "webp", ["club", "stage", "music-video", "nightlife"], "Social Creative"),
	image(930008, "bail-office-handcuffs-scene-1", "Bail Office Intake Scene 1", "Real-world bail office scene completing release paperwork", "/brand/images/bail-office-handcuffs-scene-1.png", "png", ["office", "intake", "handcuffs", "paperwork"], "Office & Intake"),
	image(930009, "bail-office-cuffs-free-scene", "Bail Office Released Free Scene", "Released defendant in office finishing bond paperwork free of handcuffs", "/brand/images/bail-office-cuffs-free-scene.png", "png", ["office", "freedom", "unhandcuffed", "release"], "Office & Intake"),
	image(930010, "florida-man-party-arrest-1", "Florida Man Nightlife Arrest 1", "High-energy SWFL party nightlife scene with arrest drama", "/brand/images/florida-man-party-arrest-1.png", "png", ["party", "nightlife", "arrest", "swfl"], "Florida Man Series"),
	image(930011, "florida-man-party-arrest-2", "Florida Man Nightlife Arrest 2", "Country nightlife Florida man arrest scene", "/brand/images/florida-man-party-arrest-2.png", "png", ["country", "nightlife", "arrest", "florida-man"], "Florida Man Series"),
	image(930012, "florida-man-party-arrest-3", "Florida Man Urban Arrest 3", "Downtown urban Southwest Florida arrest scene", "/brand/images/florida-man-party-arrest-3.png", "png", ["urban", "downtown", "swfl", "arrest"], "Florida Man Series"),
	image(930013, "florida-man-party-arrest-4", "Florida Man Arrest Scene 4", "Wild Florida Man arrest scene with bold nightlife color", "/brand/images/florida-man-party-arrest-4.png", "png", ["wild", "nightlife", "arrest", "neon"], "Florida Man Series"),
	image(930014, "florida-man-party-arrest-5", "Florida Man Arrest Scene 5", "Dramatic Florida Man party encounter with law enforcement", "/brand/images/florida-man-party-arrest-5.png", "png", ["drama", "police", "arrest", "florida-man"], "Florida Man Series"),
	image(930015, "florida-man-party-arrest-6", "Florida Man Arrest Scene 6", "Classic Florida Man trope in vibrant tropical setting", "/brand/images/florida-man-party-arrest-6.png", "png", ["tropical", "trope", "florida-man", "arrest"], "Florida Man Series"),
];

export const BUNDLED_TEXT_PRESETS = [
	{
		id: "shamrock-hotline-badge",
		name: "24/7 Bail Hotline Banner",
		text: "CALL 24/7: (239) 955-0178",
		style: "emerald-glow",
		fontSize: 48,
		color: "#10b981",
		backgroundColor: "rgba(6, 78, 59, 0.85)",
		position: "bottom-center",
	},
	{
		id: "shamrock-website-callout",
		name: "Official Website Banner",
		text: "shamrockbailbonds.biz",
		style: "white-clean",
		fontSize: 36,
		color: "#ffffff",
		backgroundColor: "rgba(0, 0, 0, 0.75)",
		position: "top-center",
	},
	{
		id: "florida-man-headline",
		name: "Florida Man Breaking Alert",
		text: "🚨 BREAKING: FLORIDA MAN ARRESTED",
		style: "gold-alert",
		fontSize: 52,
		color: "#f59e0b",
		backgroundColor: "rgba(17, 24, 39, 0.9)",
		position: "top-center",
	},
	{
		id: "bond-posted-status",
		name: "Bond Posted Status Bug",
		text: "✅ BOND POSTED • RELEASE IN PROGRESS",
		style: "green-status",
		fontSize: 40,
		color: "#34d399",
		backgroundColor: "rgba(6, 78, 59, 0.9)",
		position: "bottom-left",
	},
	{
		id: "court-date-reminder",
		name: "Court Appearance Reminder",
		text: "⚖️ MANDATORY COURT APPEARANCE",
		style: "crimson-legal",
		fontSize: 42,
		color: "#f87171",
		backgroundColor: "rgba(127, 29, 29, 0.85)",
		position: "bottom-center",
	},
];

export const AI_AGENT_RECIPES = [
	{
		id: "recipe-hot-lead-reel",
		name: "Hot Lead Social Reel (9:16)",
		description: "High-energy 15s viral reel for TikTok / Instagram. Adds gavel sound at 0s, Florida Man headline, fast whooshes on every cut, 24/7 hotline banner, and heavy jail door slam outro.",
		aspectRatio: "9:16",
		defaultDuration: 15,
		introSfx: "gavel",
		outroSfx: "jail-door",
		overlayLogo: "shamrock-tiktok-transparent",
		watermarkPosition: "top-right",
		textPreset: "shamrock-hotline-badge",
		transitionType: "fade",
	},
	{
		id: "recipe-after-hours-call",
		name: "After-Hours Bail Prompt (1:1 / 4:5)",
		description: "Reassuring 30s intake explainer video. Phone ring sound effect at intro, From Cuffs to Comfort badge, lower-third phone number, and smooth cross-dissolve transitions.",
		aspectRatio: "1:1",
		defaultDuration: 30,
		introSfx: "phone-ring",
		outroSfx: "notification",
		overlayLogo: "shamrock-pub-transparent",
		watermarkPosition: "bottom-right",
		textPreset: "shamrock-hotline-badge",
		transitionType: "dissolve",
	},
	{
		id: "recipe-court-date-short",
		name: "Court Date Reminder Short (9:16)",
		description: "Urgent 10s court reminder short with dramatic heartbeat audio bed, legal docket text overlay, and Shamrock compliance badge.",
		aspectRatio: "9:16",
		defaultDuration: 10,
		introSfx: "heartbeat",
		outroSfx: "gavel",
		overlayLogo: "shamrock-logo-850",
		watermarkPosition: "top-right",
		textPreset: "court-date-reminder",
		transitionType: "fade",
	},
];

/* Helper constructors */
function sfx(id: number, slug: string, name: string, description: string, duration: number, tags: string[], category: string): BundledAsset {
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
		format: "mp3",
		tags,
		license: "Shamrock-original",
		author: "Shamrock Bail Bonds",
		category,
	};
}

function logo(id: number, slug: string, name: string, description: string, url: string, format: "svg" | "png" | "jpg" | "webp", tags: string[], category: string): BundledAsset {
	return {
		id,
		slug,
		kind: "logos",
		name,
		description,
		url,
		previewUrl: url,
		format,
		tags,
		license: "Shamrock-original",
		author: "Shamrock Bail Bonds",
		category,
	};
}

function icon(id: number, slug: string, name: string, description: string, url: string, tags: string[]): BundledAsset {
	return {
		id,
		slug,
		kind: "icons",
		name,
		description,
		url,
		previewUrl: url,
		format: "svg",
		tags,
		license: "Shamrock-original",
		author: "Shamrock Bail Bonds",
		category: "Vector Icons",
	};
}

function image(id: number, slug: string, name: string, description: string, url: string, format: "png" | "jpg" | "webp", tags: string[], category: string): BundledAsset {
	return {
		id,
		slug,
		kind: "images",
		name,
		description,
		url,
		previewUrl: url,
		format,
		tags,
		license: "Commercial-clean",
		author: "Shamrock Creative",
		category,
	};
}

export function searchBundledCatalog({
	kind,
	query,
}: {
	kind?: string;
	query?: string;
}): BundledAsset[] {
	let list: BundledAsset[] = [];
	if (!kind || kind === "all") {
		list = [...BUNDLED_SFX, ...BUNDLED_LOGOS, ...BUNDLED_ICONS, ...BUNDLED_IMAGES];
	} else if (kind === "sfx") {
		list = BUNDLED_SFX;
	} else if (kind === "logos") {
		list = BUNDLED_LOGOS;
	} else if (kind === "icons") {
		list = BUNDLED_ICONS;
	} else if (kind === "images") {
		list = BUNDLED_IMAGES;
	}

	const q = (query || "").trim().toLowerCase();
	if (!q) return list;

	return list.filter((asset) => {
		const hay = [asset.name, asset.description, asset.slug, asset.category, ...asset.tags]
			.join(" ")
			.toLowerCase();
		return hay.includes(q);
	});
}

export function searchBundledSfx(query: string): BundledAsset[] {
	return searchBundledCatalog({ kind: "sfx", query });
}
