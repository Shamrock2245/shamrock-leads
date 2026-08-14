import { effectsRegistry } from "../registry";
import { blurEffectDefinition } from "./blur";
import { colorGradeEffectDefinition } from "./color-grade";
import { vignetteEffectDefinition } from "./vignette";
import { filmGrainEffectDefinition } from "./film-grain";
import { chromaticEffectDefinition } from "./chromatic";
import { glitchEffectDefinition } from "./glitch";
import {
	pixelateEffectDefinition,
	invertEffectDefinition,
	posterizeEffectDefinition,
	mirrorEffectDefinition,
	sharpenEffectDefinition,
	glowEffectDefinition,
	shakeEffectDefinition,
} from "./stylize";
import { TRANSITION_EFFECT_DEFINITIONS } from "./transitions";

const defaultEffects = [
	blurEffectDefinition,
	colorGradeEffectDefinition,
	vignetteEffectDefinition,
	filmGrainEffectDefinition,
	chromaticEffectDefinition,
	sharpenEffectDefinition,
	glowEffectDefinition,
	pixelateEffectDefinition,
	invertEffectDefinition,
	posterizeEffectDefinition,
	mirrorEffectDefinition,
	glitchEffectDefinition,
	shakeEffectDefinition,
	...TRANSITION_EFFECT_DEFINITIONS,
];

export function registerDefaultEffects(): void {
	for (const definition of defaultEffects) {
		if (effectsRegistry.has(definition.type)) {
			continue;
		}
		effectsRegistry.register({
			key: definition.type,
			definition,
		});
	}
}
