import { EditorCore } from "@/core";
import { Command } from "@/commands/base-command";
import { upsertPathKeyframe } from "@/animation";
import type { ElementAnimations } from "@/animation/types";
import { resolveAnimationTarget } from "@/timeline/animation-targets";
import {
	isVisualElement,
	updateElementInSceneTracks,
} from "@/timeline";
import type {
	SceneTracks,
	TimelineElement,
	TimelineTrack,
	VisualElement,
} from "@/timeline";
import { buildEffectElement } from "@/timeline/element-utils";
import { buildEmptyTrack } from "@/timeline/placement/track-factory";
import { generateUUID } from "@/utils/id";
import {
	addMediaTime,
	maxMediaTime,
	mediaTimeFromSeconds,
	subMediaTime,
	ZERO_MEDIA_TIME,
	type MediaTime,
} from "@/wasm";
import { getTransitionPreset, type TransitionPreset } from "./catalog";
import { toast } from "sonner";

type ClipRef = {
	trackId: string;
	element: VisualElement;
};

function elementEnd({ element }: { element: TimelineElement }): MediaTime {
	return addMediaTime({ a: element.startTime, b: element.duration });
}

function writeKeys({
	element,
	propertyPath,
	keys,
}: {
	element: TimelineElement;
	propertyPath: string;
	keys: Array<{ time: MediaTime; value: number }>;
}): TimelineElement {
	const target = resolveAnimationTarget({ element, path: propertyPath });
	if (!target) return element;
	let animations: ElementAnimations | undefined = element.animations;
	for (const key of keys) {
		animations = upsertPathKeyframe({
			animations,
			propertyPath,
			time: key.time,
			value: key.value,
			interpolation: "linear",
			channelLayout: target.channelLayout,
			coerceValue: target.coerceValue,
		});
	}
	return { ...element, animations };
}

export function findAdjacentVisualPair({
	tracks,
	playhead,
}: {
	tracks: SceneTracks;
	playhead: MediaTime;
}): { outgoing: ClipRef; incoming: ClipRef } | null {
	let best:
		| { outgoing: ClipRef; incoming: ClipRef; distance: number }
		| null = null;

	const allTracks: TimelineTrack[] = [
		tracks.main,
		...tracks.overlay.filter((track) => track.type === "video"),
	];

	for (const track of allTracks) {
		const visuals = track.elements
			.filter(isVisualElement)
			.slice()
			.sort((a, b) => a.startTime - b.startTime);
		for (let i = 0; i < visuals.length - 1; i++) {
			const outgoing = visuals[i];
			const incoming = visuals[i + 1];
			const join = elementEnd({ element: outgoing });
			const distance = Math.abs(join - playhead);
			if (!best || distance < best.distance) {
				best = {
					outgoing: { trackId: track.id, element: outgoing },
					incoming: { trackId: track.id, element: incoming },
					distance,
				};
			}
		}
	}

	return best
		? { outgoing: best.outgoing, incoming: best.incoming }
		: null;
}

export class ApplyTransitionCommand extends Command {
	private savedState: SceneTracks | null = null;
	private readonly preset: TransitionPreset;
	private readonly playhead: MediaTime;
	private readonly canvasWidth: number;
	private readonly canvasHeight: number;

	constructor({
		preset,
		playhead,
		canvasWidth,
		canvasHeight,
	}: {
		preset: TransitionPreset;
		playhead: MediaTime;
		canvasWidth: number;
		canvasHeight: number;
	}) {
		super();
		this.preset = preset;
		this.playhead = playhead;
		this.canvasWidth = canvasWidth;
		this.canvasHeight = canvasHeight;
	}

	execute() {
		const editor = EditorCore.getInstance();
		this.savedState = editor.scenes.getActiveScene().tracks;
		const overlap = mediaTimeFromSeconds({
			seconds: this.preset.durationSeconds,
		});
		const pair = findAdjacentVisualPair({
			tracks: this.savedState,
			playhead: this.playhead,
		});

		let tracks = this.savedState;

		if (pair) {
			const outgoingEnd = elementEnd({ element: pair.outgoing.element });
			const incomingStart = maxMediaTime({
				a: ZERO_MEDIA_TIME,
				b: subMediaTime({
					a: outgoingEnd,
					b: overlap,
				}),
			});

			tracks = updateElementInSceneTracks({
				tracks,
				trackId: pair.incoming.trackId,
				elementId: pair.incoming.element.id,
				update: (element) => ({
					...element,
					startTime: incomingStart,
				}),
			});

			const outgoingOverlapStart = subMediaTime({
				a: pair.outgoing.element.duration,
				b: overlap,
			});

			tracks = updateElementInSceneTracks({
				tracks,
				trackId: pair.outgoing.trackId,
				elementId: pair.outgoing.element.id,
				elementPredicate: isVisualElement,
				update: (element) =>
					this.decorateOutgoing({
						element,
						overlapStart: maxMediaTime({
							a: ZERO_MEDIA_TIME,
							b: outgoingOverlapStart,
						}),
						overlap,
					}),
			});

			tracks = updateElementInSceneTracks({
				tracks,
				trackId: pair.incoming.trackId,
				elementId: pair.incoming.element.id,
				elementPredicate: isVisualElement,
				update: (element) =>
					this.decorateIncoming({
						element,
						overlap,
					}),
			});
		}

		if (this.preset.shader) {
			const start = pair
				? subMediaTime({
						a: elementEnd({ element: pair.outgoing.element }),
						b: overlap,
					})
				: this.playhead;
			const created = buildEffectElement({
				effectType: this.preset.shader,
				startTime: maxMediaTime({ a: ZERO_MEDIA_TIME, b: start }),
				duration: overlap,
			});
			const effectElement = { ...created, id: generateUUID() };
			const existing = tracks.overlay.find((track) => track.type === "effect");
			if (existing) {
				tracks = {
					...tracks,
					overlay: tracks.overlay.map((track) =>
						track.id === existing.id && track.type === "effect"
							? { ...track, elements: [...track.elements, effectElement] }
							: track,
					),
				};
			} else {
				const effectTrack = {
					...buildEmptyTrack({
						id: generateUUID(),
						type: "effect",
						name: "Effects",
					}),
					elements: [effectElement],
				};
				tracks = {
					...tracks,
					overlay: [...tracks.overlay, effectTrack],
				};
			}
		}

		editor.timeline.updateTracks(tracks);
		return undefined;
	}

	undo(): void {
		if (!this.savedState) return;
		EditorCore.getInstance().timeline.updateTracks(this.savedState);
	}

	private decorateOutgoing({
		element,
		overlapStart,
		overlap,
	}: {
		element: TimelineElement;
		overlapStart: MediaTime;
		overlap: MediaTime;
	}): TimelineElement {
		let next = writeKeys({
			element,
			propertyPath: "opacity",
			keys: [
				{ time: overlapStart, value: 1 },
				{ time: addMediaTime({ a: overlapStart, b: overlap }), value: 0 },
			],
		});
		return next;
	}

	private decorateIncoming({
		element,
		overlap,
	}: {
		element: TimelineElement;
		overlap: MediaTime;
	}): TimelineElement {
		let next = writeKeys({
			element,
			propertyPath: "opacity",
			keys: [
				{ time: ZERO_MEDIA_TIME, value: 0 },
				{ time: overlap, value: 1 },
			],
		});

		const w = this.canvasWidth;
		const h = this.canvasHeight;
		if (this.preset.motion === "slide-left") {
			next = writeKeys({
				element: next,
				propertyPath: "transform.positionX",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: w },
					{ time: overlap, value: 0 },
				],
			});
		} else if (this.preset.motion === "slide-right") {
			next = writeKeys({
				element: next,
				propertyPath: "transform.positionX",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: -w },
					{ time: overlap, value: 0 },
				],
			});
		} else if (this.preset.motion === "slide-up") {
			next = writeKeys({
				element: next,
				propertyPath: "transform.positionY",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: h },
					{ time: overlap, value: 0 },
				],
			});
		} else if (this.preset.motion === "slide-down") {
			next = writeKeys({
				element: next,
				propertyPath: "transform.positionY",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: -h },
					{ time: overlap, value: 0 },
				],
			});
		} else if (this.preset.motion === "zoom") {
			next = writeKeys({
				element: next,
				propertyPath: "transform.scaleX",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: 1.28 },
					{ time: overlap, value: 1 },
				],
			});
			next = writeKeys({
				element: next,
				propertyPath: "transform.scaleY",
				keys: [
					{ time: ZERO_MEDIA_TIME, value: 1.28 },
					{ time: overlap, value: 1 },
				],
			});
		}
		return next;
	}
}

export function applyTransitionAtPlayhead({
	type,
}: {
	type: string;
}): boolean {
	const preset = getTransitionPreset(type);
	if (!preset) {
		toast.error("Unknown transition");
		return false;
	}
	const editor = EditorCore.getInstance();
	const scene = editor.scenes.getActiveSceneOrNull();
	if (!scene) {
		toast.error("Open a project first");
		return false;
	}
	const playhead = editor.playback.getCurrentTime();
	const pair = findAdjacentVisualPair({
		tracks: scene.tracks,
		playhead,
	});
	if (!pair && !preset.shader) {
		toast.error("Need two clips on a video track. Park the playhead near the cut.");
		return false;
	}

	const canvas = editor.project.getActiveOrNull()?.settings.canvasSize ?? {
		width: 1920,
		height: 1080,
	};
	editor.command.execute({
		command: new ApplyTransitionCommand({
			preset,
			playhead,
			canvasWidth: canvas.width,
			canvasHeight: canvas.height,
		}),
	});
	toast.success(
		pair
			? `Applied ${preset.name} (${preset.durationSeconds}s)`
			: `Added ${preset.name} on the effects track`,
	);
	return true;
}

export function applyTransitionToEveryCut({
	type,
}: {
	type: string;
}): number {
	const preset = getTransitionPreset(type);
	if (!preset) return 0;
	const editor = EditorCore.getInstance();
	const scene = editor.scenes.getActiveSceneOrNull();
	if (!scene) return 0;

	const joins: MediaTime[] = [];
	const tracks: TimelineTrack[] = [
		scene.tracks.main,
		...scene.tracks.overlay.filter((track) => track.type === "video"),
	];
	for (const track of tracks) {
		const visuals = track.elements
			.filter(isVisualElement)
			.slice()
			.sort((a, b) => a.startTime - b.startTime);
		for (let i = 0; i < visuals.length - 1; i++) {
			joins.push(elementEnd({ element: visuals[i] }));
		}
	}

	let applied = 0;
	const canvas = editor.project.getActiveOrNull()?.settings.canvasSize ?? {
		width: 1920,
		height: 1080,
	};
	for (const join of joins) {
		editor.command.execute({
			command: new ApplyTransitionCommand({
				preset,
				playhead: join,
				canvasWidth: canvas.width,
				canvasHeight: canvas.height,
			}),
		});
		applied += 1;
	}
	return applied;
}

export function listCuts({ tracks }: { tracks: SceneTracks }): MediaTime[] {
	const joins: MediaTime[] = [];
	const allTracks: TimelineTrack[] = [
		tracks.main,
		...tracks.overlay.filter((track) => track.type === "video"),
	];
	for (const track of allTracks) {
		const visuals = track.elements
			.filter(isVisualElement)
			.slice()
			.sort((a, b) => a.startTime - b.startTime);
		for (let i = 0; i < visuals.length - 1; i++) {
			joins.push(elementEnd({ element: visuals[i] }));
		}
	}
	return joins;
}
