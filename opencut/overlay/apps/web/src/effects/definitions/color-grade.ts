import type { EffectDefinition } from "@/effects/types";

export const colorGradeEffectDefinition: EffectDefinition = {
	type: "color-grade",
	name: "Color Grade",
	keywords: ["color", "grade", "contrast", "saturation", "temperature", "look"],
	params: [
		{ key: "brightness", label: "Brightness", type: "number", default: 0, min: -0.5, max: 0.5, step: 0.01 },
		{ key: "contrast", label: "Contrast", type: "number", default: 0.1, min: -0.8, max: 1.2, step: 0.01 },
		{ key: "saturation", label: "Saturation", type: "number", default: 0.15, min: -1, max: 1.5, step: 0.01 },
		{ key: "temperature", label: "Temperature", type: "number", default: 0.05, min: -1, max: 1, step: 0.01 },
	],
	renderer: {
		passes: [
			{
				shader: "color-grade",
				uniforms: ({ effectParams }) => ({
					u_brightness: Number(effectParams.brightness) || 0,
					u_contrast: Number(effectParams.contrast) || 0,
					u_saturation: Number(effectParams.saturation) || 0,
					u_temperature: Number(effectParams.temperature) || 0,
				}),
			},
		],
	},
};
