import type { EffectDefinition } from "@/effects/types";

export const vignetteEffectDefinition: EffectDefinition = {
	type: "vignette",
	name: "Vignette",
	category: "lens",
	kind: "filter",
	keywords: ["vignette", "dark", "edges", "lens"],
	params: [
		{ key: "intensity", label: "Intensity", type: "number", default: 0.75, min: 0, max: 1.5, step: 0.01 },
		{ key: "softness", label: "Softness", type: "number", default: 1.15, min: 0.2, max: 2, step: 0.01 },
	],
	renderer: {
		passes: [
			{
				shader: "vignette",
				uniforms: ({ effectParams }) => ({
					u_intensity: Number(effectParams.intensity) || 0,
					u_softness: Number(effectParams.softness) || 1,
				}),
			},
		],
	},
};
