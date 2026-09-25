import { buildStickerId, parseStickerId } from "../sticker-id";
import type {
	StickerBrowseResult,
	StickerItem,
	StickerProvider,
	StickerSearchResult,
} from "../types";
import { BUNDLED_LOGOS, BUNDLED_ICONS } from "@/media/bundled-catalog";

const LOGOS_PROVIDER_ID = "logos";

function toStickerItem(asset: { slug: string; name: string; url: string; previewUrl: string }): StickerItem {
	return {
		id: buildStickerId({
			providerId: LOGOS_PROVIDER_ID,
			providerValue: asset.slug,
		}),
		provider: LOGOS_PROVIDER_ID,
		name: asset.name,
		previewUrl: asset.previewUrl,
		metadata: { slug: asset.slug },
	};
}

export const logosProvider: StickerProvider = {
	id: LOGOS_PROVIDER_ID,
	async search({ query }: { query: string }): Promise<StickerSearchResult> {
		const q = query.trim().toLowerCase();
		const all = [...BUNDLED_LOGOS, ...BUNDLED_ICONS];
		const filtered = q
			? all.filter((item) =>
					[item.name, item.slug, ...item.tags].join(" ").toLowerCase().includes(q),
				)
			: all;

		return {
			items: filtered.map(toStickerItem),
			total: filtered.length,
			hasMore: false,
		};
	},
	async browse(): Promise<StickerBrowseResult> {
		return {
			sections: [
				{
					id: "shamrock-logos",
					title: "Shamrock Bail Bonds Logos",
					items: BUNDLED_LOGOS.filter((l) => l.category.includes("Vector") || l.category.includes("Master")).map(toStickerItem),
					layout: "grid",
				},
				{
					id: "shamrock-crests",
					title: "Transparent Brand Crests",
					items: BUNDLED_LOGOS.filter((l) => l.category.includes("Transparent") || l.category.includes("007")).map(toStickerItem),
					layout: "grid",
				},
				{
					id: "vector-icons",
					title: "Legal & Action Icons",
					items: BUNDLED_ICONS.map(toStickerItem),
					layout: "grid",
				},
			],
		};
	},
	resolveUrl({ stickerId }: { stickerId: string }): string {
		try {
			const { providerValue } = parseStickerId({ stickerId });
			const found =
				BUNDLED_LOGOS.find((l) => l.slug === providerValue) ||
				BUNDLED_ICONS.find((i) => i.slug === providerValue);
			return found ? found.url : `/brand/logos/${providerValue}.svg`;
		} catch {
			return stickerId;
		}
	},
};
