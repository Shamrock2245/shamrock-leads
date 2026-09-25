"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { PanelView } from "@/components/editor/panels/assets/views/base-panel";
import { useEditor } from "@/editor/use-editor";
import { useAssetsPanelStore } from "@/components/editor/panels/assets/assets-panel-store";
import {
	applyTransitionToEveryCut,
	listCuts,
} from "@/transitions/apply";
import { buildEffectElement } from "@/timeline/element-utils";
import { mediaTimeFromSeconds, ZERO_MEDIA_TIME } from "@/wasm";
import { BUNDLED_SFX } from "@/media/bundled-catalog";
import { useSoundsStore } from "@/sounds/sounds-store";
import { toast } from "sonner";
import { TEXT_PRESETS, buildPresetTextElement } from "@/text/presets";

export function AutoAssistView() {
	const editor = useEditor();
	const setActiveTab = useAssetsPanelStore((s) => s.setActiveTab);
	const addSoundToTimeline = useSoundsStore((s) => s.addSoundToTimeline);
	const [busy, setBusy] = useState<string | null>(null);

	const run = async (label: string, work: () => Promise<void> | void) => {
		setBusy(label);
		try {
			await work();
		} catch (error) {
			toast.error(error instanceof Error ? error.message : label);
		} finally {
			setBusy(null);
		}
	};

	return (
		<PanelView title="Auto / AI">
			<div className="flex flex-col gap-3 text-sm">
				<p className="text-muted-foreground text-xs">
					CapCut-style one-taps. These edit your timeline — undo works.
				</p>

				<Button
					disabled={!!busy}
					onClick={() =>
						run("fades", () => {
							const n = applyTransitionToEveryCut({ type: "fade" });
							toast.success(
								n ? `Faded ${n} cut${n === 1 ? "" : "s"}` : "Need at least two clips",
							);
						})
					}
				>
					{busy === "fades" ? "Working…" : "Fade every cut"}
				</Button>

				<Button
					variant="secondary"
					disabled={!!busy}
					onClick={() =>
						run("whoosh", async () => {
							const scene = editor.scenes.getActiveSceneOrNull();
							if (!scene) return;
							const cuts = listCuts({ tracks: scene.tracks });
							const whoosh = BUNDLED_SFX.find((s) => s.slug === "whoosh-fast");
							if (!whoosh) return;
							let added = 0;
							for (const cut of cuts) {
								editor.playback.seek({ time: cut });
								const ok = await addSoundToTimeline({
									sound: {
										id: whoosh.id,
										name: whoosh.name,
										description: whoosh.description,
										url: whoosh.url,
										previewUrl: whoosh.previewUrl,
										duration: whoosh.duration,
										filesize: 0,
										type: "mp3",
										channels: 2,
										bitrate: 128000,
										bitdepth: 16,
										samplerate: 44100,
										username: whoosh.author,
										tags: whoosh.tags,
										license: whoosh.license,
										created: "",
										downloads: 0,
										rating: 5,
										ratingCount: 1,
									},
								});
								if (ok) added += 1;
							}
							toast.success(
								added ? `Whoosh on ${added} cut${added === 1 ? "" : "s"}` : "No cuts found",
							);
						})
					}
				>
					{busy === "whoosh" ? "Working…" : "Whoosh on every cut"}
				</Button>

				<Button
					variant="secondary"
					disabled={!!busy}
					onClick={() =>
						run("look", () => {
							const t = editor.playback.getCurrentTime();
							const dur = mediaTimeFromSeconds({ seconds: 8 });
							for (const type of ["color-grade", "vignette", "film-grain"] as const) {
								editor.timeline.insertElement({
									placement: { mode: "auto", trackType: "effect" },
									element: buildEffectElement({
										effectType: type,
										startTime: t,
										duration: dur,
									}),
								});
							}
							toast.success("Film look on the effects track");
						})
					}
				>
					{busy === "look" ? "Working…" : "Film look (grade + grain)"}
				</Button>

				<Button
					variant="secondary"
					disabled={!!busy}
					onClick={() =>
						run("cta", () => {
							const preset = TEXT_PRESETS.find((p) => p.id === "headline");
							if (!preset) return;
							editor.timeline.insertElement({
								element: buildPresetTextElement({
									preset,
									startTime: editor.playback.getCurrentTime() ?? ZERO_MEDIA_TIME,
								}),
								placement: { mode: "auto" },
							});
							toast.success("Headline added");
						})
					}
				>
					{busy === "cta" ? "Working…" : "Add Shamrock headline"}
				</Button>

				<Button
					variant="secondary"
					disabled={!!busy}
					onClick={() =>
						run("gavel", async () => {
							const gavel = BUNDLED_SFX.find((s) => s.slug === "gavel");
							if (!gavel) return;
							editor.playback.seek({ time: ZERO_MEDIA_TIME });
							await addSoundToTimeline({
								sound: {
									id: gavel.id,
									name: gavel.name,
									description: gavel.description,
									url: gavel.url,
									previewUrl: gavel.previewUrl,
									duration: gavel.duration || 0.5,
									filesize: 0,
									type: "mp3",
									channels: 2,
									bitrate: 128000,
									bitdepth: 16,
									samplerate: 44100,
									username: gavel.author,
									tags: gavel.tags,
									license: gavel.license,
									created: "",
									downloads: 0,
									rating: 5,
									ratingCount: 1,
								},
							});
							toast.success("Court Gavel Strike added at 0.0s");
						})
					}
				>
					{busy === "gavel" ? "Working…" : "⚖️ Add Court Gavel Intro (0.0s)"}
				</Button>

				<Button
					variant="secondary"
					disabled={!!busy}
					onClick={() =>
						run("jail", async () => {
							const door = BUNDLED_SFX.find((s) => s.slug === "jail-door");
							if (!door) return;
							await addSoundToTimeline({
								sound: {
									id: door.id,
									name: door.name,
									description: door.description,
									url: door.url,
									previewUrl: door.previewUrl,
									duration: door.duration || 1.1,
									filesize: 0,
									type: "mp3",
									channels: 2,
									bitrate: 128000,
									bitdepth: 16,
									samplerate: 44100,
									username: door.author,
									tags: door.tags,
									license: door.license,
									created: "",
									downloads: 0,
									rating: 5,
									ratingCount: 1,
								},
							});
							toast.success("Jail Door Slam Outro added");
						})
					}
				>
					{busy === "jail" ? "Working…" : "🔒 Add Heavy Jail Slam Outro"}
				</Button>

				<Button
					variant="secondary"
					disabled={!!busy}
					onClick={() =>
						run("hotline", () => {
							const hotlinePreset = {
								id: "hotline-lowerthird",
								name: "24/7 Hotline",
								content: "CALL 24/7: (239) 955-0178",
							};
							editor.timeline.insertElement({
								element: buildPresetTextElement({
									preset: hotlinePreset,
									startTime: editor.playback.getCurrentTime() ?? ZERO_MEDIA_TIME,
								}),
								placement: { mode: "auto" },
							});
							toast.success("24/7 Hotline banner added: (239) 955-0178");
						})
					}
				>
					{busy === "hotline" ? "Working…" : "📞 Add 24/7 Bail Hotline Banner"}
				</Button>

				<Button
					variant="text"
					onClick={() => setActiveTab("brand")}
				>
					Browse Full Shamrock Brand Suite →
				</Button>

				<Button
					variant="text"
					onClick={() => setActiveTab("captions")}
				>
					Open captions / auto-transcribe
				</Button>
			</div>
		</PanelView>
	);
}
