import {
	applyEffectPasses,
	applyMaskFeather as applyMaskFeatherWasm,
	initializeGpu,
} from "opencut-wasm";
import type { EffectPass, EffectUniformValue } from "@/effects/types";
import {
	applyWebglEffectPasses,
	needsWebglFallback,
} from "./webgl-effects";

let gpuAvailable = false;
let initPromise: Promise<void> | null = null;

export function initializeGpuRenderer(): Promise<void> {
	if (!initPromise) {
		initPromise = initializeGpu()
			.then(() => {
				gpuAvailable = true;
			})
			.catch((error: unknown) => {
				gpuAvailable = false;
				const message = error instanceof Error ? error.message : String(error);
				console.warn(`GPU renderer unavailable: ${message}`);
			});
	}
	return initPromise;
}

export function isGpuAvailable(): boolean {
	return gpuAvailable;
}

export const gpuRenderer = {
	applyEffect({
		source,
		width,
		height,
		passes,
	}: {
		source: OffscreenCanvas;
		width: number;
		height: number;
		passes: EffectPass[];
	}): OffscreenCanvas {
		if (passes.length === 0) {
			return source;
		}

		if (needsWebglFallback(passes)) {
			return applyWebglEffectPasses({ source, width, height, passes });
		}

		if (!gpuAvailable) {
			return applyWebglEffectPasses({ source, width, height, passes });
		}

		try {
			return applyEffectPasses({
				source,
				width,
				height,
				passes: serializeEffectPasses(passes),
			});
		} catch (error) {
			console.warn("WASM effect failed, using WebGL fallback", error);
			return applyWebglEffectPasses({ source, width, height, passes });
		}
	},

	applyMaskFeather({
		maskCanvas,
		width,
		height,
		feather,
	}: {
		maskCanvas: OffscreenCanvas;
		width: number;
		height: number;
		feather: number;
	}): OffscreenCanvas {
		if (!gpuAvailable) {
			return maskCanvas;
		}

		return applyMaskFeatherWasm({
			mask: maskCanvas,
			width,
			height,
			feather,
		});
	},
};

function serializeEffectPasses(passes: EffectPass[]) {
	return passes.map((pass) => ({
		shader: pass.shader,
		uniforms: Object.entries(pass.uniforms).map(([name, value]) => ({
			name,
			value: normalizeUniformValue(value),
		})),
	}));
}

function normalizeUniformValue(value: EffectUniformValue): number[] {
	return typeof value === "number" ? [value] : value;
}
