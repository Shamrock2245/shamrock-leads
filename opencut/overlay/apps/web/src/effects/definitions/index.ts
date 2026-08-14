import { effectsRegistry } from "../registry";
import { blurEffectDefinition } from "./blur";
import { colorGradeEffectDefinition } from "./color-grade";
import { vignetteEffectDefinition } from "./vignette";
import { filmGrainEffectDefinition } from "./film-grain";
import { chromaticEffectDefinition } from "./chromatic";
import { glitchEffectDefinition } from "./glitch";

const defaultEffects = [
	blurEffectDefinition,
	colorGradeEffectDefinition,
	vignetteEffectDefinition,
	filmGrainEffectDefinition,
	chromaticEffectDefinition,
	glitchEffectDefinition,
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
