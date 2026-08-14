import type { EffectDefinition } from "@/effects/types";

export const filmGrainEffectDefinition: EffectDefinition = {
	type: "film-grain",
	name: "Film Grain",
	keywords: ["grain", "film", "noise", "texture"],
	params: [
		{ key: "amount", label: "Amount", type: "number", default: 0.12, min: 0, max: 0.5, step: 0.01 },
		{ key: "size", label: "Size", type: "number", default: 1.5, min: 0.5, max: 6, step: 0.1 },
	],
	renderer: {
		passes: [
			{
				shader: "film-grain",
				uniforms: ({ effectParams }) => ({
					u_amount: Number(effectParams.amount) || 0,
					u_size: Number(effectParams.size) || 1,
				}),
			},
		],
	},
};
