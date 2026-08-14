import type { EffectDefinition } from "@/effects/types";

export const chromaticEffectDefinition: EffectDefinition = {
	type: "chromatic",
	name: "Chromatic",
	category: "lens",
	kind: "filter",
	keywords: ["chromatic", "rgb", "aberration", "lens"],
	params: [
		{ key: "amount", label: "Amount", type: "number", default: 0.008, min: 0, max: 0.04, step: 0.001 },
	],
	renderer: {
		passes: [
			{
				shader: "chromatic",
				uniforms: ({ effectParams }) => ({
					u_amount: Number(effectParams.amount) || 0,
				}),
			},
		],
	},
};
