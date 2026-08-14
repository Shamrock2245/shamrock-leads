export type TransitionMotion =
	| "crossfade"
	| "slide-left"
	| "slide-right"
	| "slide-up"
	| "slide-down"
	| "zoom"
	| "shader";

export interface TransitionPreset {
	type: string;
	name: string;
	description: string;
	durationSeconds: number;
	motion: TransitionMotion;
	shader?: string;
}

export const TRANSITION_PRESETS: TransitionPreset[] = [
	{
		type: "fade",
		name: "Fade",
		description: "Crossfade between clips",
		durationSeconds: 0.6,
		motion: "crossfade",
	},
	{
		type: "dissolve",
		name: "Dissolve",
		description: "Soft dissolve",
		durationSeconds: 0.8,
		motion: "crossfade",
	},
	{
		type: "slide-left",
		name: "Slide Left",
		description: "Incoming clip slides in from the right",
		durationSeconds: 0.5,
		motion: "slide-left",
	},
	{
		type: "slide-right",
		name: "Slide Right",
		description: "Incoming clip slides in from the left",
		durationSeconds: 0.5,
		motion: "slide-right",
	},
	{
		type: "slide-up",
		name: "Slide Up",
		description: "Incoming clip slides up",
		durationSeconds: 0.5,
		motion: "slide-up",
	},
	{
		type: "slide-down",
		name: "Slide Down",
		description: "Incoming clip slides down",
		durationSeconds: 0.5,
		motion: "slide-down",
	},
	{
		type: "zoom",
		name: "Zoom",
		description: "Incoming clip punches in",
		durationSeconds: 0.45,
		motion: "zoom",
	},
	{
		type: "fade-black",
		name: "Fade Black",
		description: "Dip to black",
		durationSeconds: 0.7,
		motion: "shader",
		shader: "fade-black",
	},
	{
		type: "fade-white",
		name: "Fade White",
		description: "Flash through white",
		durationSeconds: 0.55,
		motion: "shader",
		shader: "fade-white",
	},
	{
		type: "wipe-right",
		name: "Wipe Right",
		description: "Hard wipe left to right",
		durationSeconds: 0.5,
		motion: "shader",
		shader: "wipe-right",
	},
	{
		type: "wipe-left",
		name: "Wipe Left",
		description: "Hard wipe right to left",
		durationSeconds: 0.5,
		motion: "shader",
		shader: "wipe-left",
	},
	{
		type: "wipe-up",
		name: "Wipe Up",
		description: "Wipe upward",
		durationSeconds: 0.5,
		motion: "shader",
		shader: "wipe-up",
	},
	{
		type: "wipe-down",
		name: "Wipe Down",
		description: "Wipe downward",
		durationSeconds: 0.5,
		motion: "shader",
		shader: "wipe-down",
	},
	{
		type: "iris",
		name: "Iris",
		description: "Circle close",
		durationSeconds: 0.6,
		motion: "shader",
		shader: "iris",
	},
	{
		type: "pixelize-out",
		name: "Pixelize",
		description: "Pixelate through the cut",
		durationSeconds: 0.55,
		motion: "shader",
		shader: "pixelize-out",
	},
	{
		type: "zoom-blur-out",
		name: "Zoom Blur",
		description: "Radial zoom through the cut",
		durationSeconds: 0.5,
		motion: "shader",
		shader: "zoom-blur-out",
	},
];

export function getTransitionPreset(type: string): TransitionPreset | undefined {
	return TRANSITION_PRESETS.find((item) => item.type === type);
}
