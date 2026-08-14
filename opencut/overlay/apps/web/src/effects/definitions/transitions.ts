import type { EffectDefinition } from "@/effects/types";

function transitionDef({
	type,
	name,
	keywords,
	shader,
}: {
	type: string;
	name: string;
	keywords: string[];
	shader: string;
}): EffectDefinition {
	return {
		type,
		name,
		category: "transition",
		kind: "transition",
		keywords,
		params: [
			{
				key: "progress",
				label: "Progress",
				type: "number",
				default: 0,
				min: 0,
				max: 1,
				step: 0.01,
			},
		],
		renderer: {
			passes: [
				{
					shader,
					uniforms: ({ effectParams }) => ({
						u_progress: Number(effectParams.progress) || 0,
					}),
				},
			],
		},
	};
}

export const TRANSITION_EFFECT_DEFINITIONS: EffectDefinition[] = [
	transitionDef({
		type: "fade-black",
		name: "Fade Black",
		keywords: ["fade", "black", "transition"],
		shader: "fade-black",
	}),
	transitionDef({
		type: "fade-white",
		name: "Fade White",
		keywords: ["fade", "white", "flash", "transition"],
		shader: "fade-white",
	}),
	transitionDef({
		type: "wipe-right",
		name: "Wipe Right",
		keywords: ["wipe", "right", "transition"],
		shader: "wipe-right",
	}),
	transitionDef({
		type: "wipe-left",
		name: "Wipe Left",
		keywords: ["wipe", "left", "transition"],
		shader: "wipe-left",
	}),
	transitionDef({
		type: "wipe-up",
		name: "Wipe Up",
		keywords: ["wipe", "up", "transition"],
		shader: "wipe-up",
	}),
	transitionDef({
		type: "wipe-down",
		name: "Wipe Down",
		keywords: ["wipe", "down", "transition"],
		shader: "wipe-down",
	}),
	transitionDef({
		type: "iris",
		name: "Iris",
		keywords: ["iris", "circle", "transition"],
		shader: "iris",
	}),
	transitionDef({
		type: "pixelize-out",
		name: "Pixelize",
		keywords: ["pixel", "mosaic", "transition"],
		shader: "pixelize-out",
	}),
	transitionDef({
		type: "zoom-blur-out",
		name: "Zoom Blur",
		keywords: ["zoom", "blur", "transition"],
		shader: "zoom-blur-out",
	}),
];
