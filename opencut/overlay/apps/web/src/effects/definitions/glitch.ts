import type { EffectDefinition } from "@/effects/types";

export const glitchEffectDefinition: EffectDefinition = {
	type: "glitch-rgb",
	name: "Glitch",
	category: "glitch",
	kind: "filter",
	keywords: ["glitch", "rgb", "stutter", "digital"],
	params: [
		{ key: "amount", label: "Amount", type: "number", default: 0.02, min: 0, max: 0.12, step: 0.001 },
	],
	renderer: {
		passes: [
			{
				shader: "glitch-rgb",
				uniforms: ({ effectParams }) => ({
					u_amount: Number(effectParams.amount) || 0,
					u_time: 0,
				}),
			},
		],
	},
};
