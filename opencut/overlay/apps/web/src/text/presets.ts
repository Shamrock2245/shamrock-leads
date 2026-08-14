import { upsertPathKeyframe } from "@/animation";
import type { ElementAnimations } from "@/animation/types";
import { resolveAnimationTarget } from "@/timeline/animation-targets";
import { buildTextElement } from "@/timeline/element-utils";
import type { CreateTimelineElement, TimelineElement } from "@/timeline";
import {
	mediaTimeFromSeconds,
	ZERO_MEDIA_TIME,
	type MediaTime,
} from "@/wasm";

export interface TextPreset {
	id: string;
	name: string;
	content: string;
	fontSize: number;
	fontWeight: string;
	color: string;
	motion: "none" | "fade-up" | "pop" | "slide-left";
}

export const TEXT_PRESETS: TextPreset[] = [
	{
		id: "headline",
		name: "Headline",
		content: "24/7 BAIL BONDS",
		fontSize: 22,
		fontWeight: "bold",
		color: "#ffffff",
		motion: "pop",
	},
	{
		id: "fade-up",
		name: "Fade Up",
		content: "Call (239) 332-2245",
		fontSize: 16,
		fontWeight: "semibold",
		color: "#f4e27a",
		motion: "fade-up",
	},
	{
		id: "lower-third",
		name: "Lower Third",
		content: "Shamrock Bail Bonds  ·  Fort Myers",
		fontSize: 14,
		fontWeight: "medium",
		color: "#ffffff",
		motion: "slide-left",
	},
	{
		id: "plain",
		name: "Plain",
		content: "Default text",
		fontSize: 15,
		fontWeight: "normal",
		color: "#ffffff",
		motion: "none",
	},
];

function writeKeys({
	element,
	propertyPath,
	keys,
}: {
	element: TimelineElement;
	propertyPath: string;
	keys: Array<{ time: MediaTime; value: number }>;
}): TimelineElement {
	const target = resolveAnimationTarget({ element, path: propertyPath });
	if (!target) return element;
	let animations: ElementAnimations | undefined = element.animations;
	for (const key of keys) {
		animations = upsertPathKeyframe({
			animations,
			propertyPath,
			time: key.time,
			value: key.value,
			interpolation: "linear",
			channelLayout: target.channelLayout,
			coerceValue: target.coerceValue,
		});
	}
	return { ...element, animations };
}

export function buildPresetTextElement({
	preset,
	startTime,
}: {
	preset: TextPreset;
	startTime: MediaTime;
}): CreateTimelineElement {
	const created = buildTextElement({
		raw: {
			name: preset.name,
			params: {
				content: preset.content,
				fontSize: preset.fontSize,
				fontWeight: preset.fontWeight,
				color: preset.color,
				textAlign: "center",
			},
		},
		startTime,
	});

	const asElement = created as TimelineElement;
	const intro = mediaTimeFromSeconds({ seconds: 0.35 });

	if (preset.motion === "fade-up") {
		return writeKeys({
			element: writeKeys({
				element: asElement,
				propertyPath: "opacity",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: 0 },
					{ time: intro, value: 1 },
				],
			}),
			propertyPath: "transform.positionY",
			keys: [
				{ time: ZERO_MEDIA_TIME, value: 80 },
				{ time: intro, value: 0 },
			],
		}) as CreateTimelineElement;
	}

	if (preset.motion === "pop") {
		return writeKeys({
			element: writeKeys({
				element: writeKeys({
					element: asElement,
					propertyPath: "opacity",
					keys: [
						{ time: ZERO_MEDIA_TIME, value: 0 },
						{ time: intro, value: 1 },
					],
				}),
				propertyPath: "transform.scaleX",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: 0.72 },
					{ time: intro, value: 1 },
				],
			}),
			propertyPath: "transform.scaleY",
			keys: [
				{ time: ZERO_MEDIA_TIME, value: 0.72 },
				{ time: intro, value: 1 },
			],
		}) as CreateTimelineElement;
	}

	if (preset.motion === "slide-left") {
		return writeKeys({
			element: writeKeys({
				element: asElement,
				propertyPath: "opacity",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: 0 },
					{ time: intro, value: 1 },
				],
			}),
			propertyPath: "transform.positionX",
			keys: [
				{ time: ZERO_MEDIA_TIME, value: 160 },
				{ time: intro, value: 0 },
			],
		}) as CreateTimelineElement;
	}

	return created;
}
