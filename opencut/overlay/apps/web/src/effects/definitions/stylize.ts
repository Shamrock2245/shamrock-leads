import type { EffectDefinition } from "@/effects/types";

export const pixelateEffectDefinition: EffectDefinition = {
	type: "pixelate",
	name: "Pixelate",
	category: "stylize",
	keywords: ["pixel", "mosaic", "8bit"],
	params: [
		{ key: "size", label: "Size", type: "number", default: 12, min: 2, max: 80, step: 1 },
	],
	renderer: {
		passes: [
			{
				shader: "pixelate",
				uniforms: ({ effectParams }) => ({
					u_size: Number(effectParams.size) || 8,
				}),
			},
		],
	},
};

export const invertEffectDefinition: EffectDefinition = {
	type: "invert",
	name: "Invert",
	category: "stylize",
	keywords: ["invert", "negative"],
	params: [
		{ key: "amount", label: "Amount", type: "number", default: 1, min: 0, max: 1, step: 0.01 },
	],
	renderer: {
		passes: [
			{
				shader: "invert",
				uniforms: ({ effectParams }) => ({
					u_amount: Number(effectParams.amount) || 0,
				}),
			},
		],
	},
};

export const posterizeEffectDefinition: EffectDefinition = {
	type: "posterize",
	name: "Posterize",
	category: "stylize",
	keywords: ["poster", "bands", "comic"],
	params: [
		{ key: "levels", label: "Levels", type: "number", default: 5, min: 2, max: 16, step: 1 },
	],
	renderer: {
		passes: [
			{
				shader: "posterize",
				uniforms: ({ effectParams }) => ({
					u_levels: Number(effectParams.levels) || 5,
				}),
			},
		],
	},
};

export const mirrorEffectDefinition: EffectDefinition = {
	type: "mirror",
	name: "Mirror",
	category: "stylize",
	keywords: ["mirror", "flip", "symmetry"],
	params: [
		{ key: "mode", label: "Mode", type: "number", default: 0, min: 0, max: 2, step: 1 },
	],
	renderer: {
		passes: [
			{
				shader: "mirror",
				uniforms: ({ effectParams }) => ({
					u_mode: Number(effectParams.mode) || 0,
				}),
			},
		],
	},
};

export const sharpenEffectDefinition: EffectDefinition = {
	type: "sharpen",
	name: "Sharpen",
	category: "lens",
	keywords: ["sharpen", "crisp", "detail"],
	params: [
		{ key: "amount", label: "Amount", type: "number", default: 0.45, min: 0, max: 2, step: 0.01 },
	],
	renderer: {
		passes: [
			{
				shader: "sharpen",
				uniforms: ({ effectParams }) => ({
					u_amount: Number(effectParams.amount) || 0,
				}),
			},
		],
	},
};

export const glowEffectDefinition: EffectDefinition = {
	type: "glow",
	name: "Glow",
	category: "lens",
	keywords: ["glow", "bloom", "highlight"],
	params: [
		{ key: "amount", label: "Amount", type: "number", default: 0.45, min: 0, max: 1.5, step: 0.01 },
	],
	renderer: {
		passes: [
			{
				shader: "glow",
				uniforms: ({ effectParams }) => ({
					u_amount: Number(effectParams.amount) || 0,
				}),
			},
		],
	},
};

export const shakeEffectDefinition: EffectDefinition = {
	type: "shake",
	name: "Shake",
	category: "glitch",
	keywords: ["shake", "handheld", "impact"],
	params: [
		{ key: "amount", label: "Amount", type: "number", default: 0.012, min: 0, max: 0.08, step: 0.001 },
	],
	renderer: {
		passes: [
			{
				shader: "shake",
				uniforms: ({ effectParams }) => ({
					u_amount: Number(effectParams.amount) || 0,
					u_time: 0,
				}),
			},
		],
	},
};
