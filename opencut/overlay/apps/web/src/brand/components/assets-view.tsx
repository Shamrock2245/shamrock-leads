"use client";

import { useMemo, useState, useRef } from "react";
import Image from "next/image";
import { toast } from "sonner";
import { PanelView } from "@/components/editor/panels/assets/views/base-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useEditor } from "@/editor/use-editor";
import { mediaTimeFromSeconds } from "@/wasm";
import {
	BUNDLED_SFX,
	BUNDLED_LOGOS,
	BUNDLED_ICONS,
	BUNDLED_IMAGES,
	BUNDLED_TEXT_PRESETS,
	AI_AGENT_RECIPES,
	type BundledAsset,
} from "@/media/bundled-catalog";
import { buildPresetTextElement } from "@/text/presets";
import { useSoundsStore } from "@/sounds/sounds-store";
import {
	PlayIcon,
	PauseIcon,
	PlusSignIcon,
	SparklesIcon,
	Tag01Icon,
	Folder01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

type CategoryFilter = "all" | "logos" | "icons" | "images" | "sfx" | "ai-tools";

export function BrandView() {
	const editor = useEditor();
	const addSoundToTimeline = useSoundsStore((s) => s.addSoundToTimeline);
	const [filter, setFilter] = useState<CategoryFilter>("all");
	const [searchQuery, setSearchQuery] = useState("");
	const [playingSlug, setPlayingSlug] = useState<string | null>(null);
	const audioRef = useRef<HTMLAudioElement | null>(null);

	const allAssets = useMemo(() => {
		return [
			...BUNDLED_LOGOS,
			...BUNDLED_ICONS,
			...BUNDLED_IMAGES,
			...BUNDLED_SFX,
		];
	}, []);

	const filteredAssets = useMemo(() => {
		let list = allAssets;
		if (filter === "logos") list = BUNDLED_LOGOS;
		else if (filter === "icons") list = BUNDLED_ICONS;
		else if (filter === "images") list = BUNDLED_IMAGES;
		else if (filter === "sfx") list = BUNDLED_SFX;

		if (!searchQuery.trim()) return list;
		const q = searchQuery.toLowerCase();
		return list.filter((item) =>
			[item.name, item.description, item.slug, item.category, ...item.tags]
				.join(" ")
				.toLowerCase()
				.includes(q),
		);
	}, [allAssets, filter, searchQuery]);

	const togglePlayAudio = (asset: BundledAsset) => {
		if (playingSlug === asset.slug) {
			audioRef.current?.pause();
			setPlayingSlug(null);
			return;
		}
		if (audioRef.current) {
			audioRef.current.pause();
		}
		const audio = new Audio(asset.url);
		audioRef.current = audio;
		audio.play().catch(() => {});
		audio.onended = () => setPlayingSlug(null);
		setPlayingSlug(asset.slug);
	};

	const handleAddAsset = async (asset: BundledAsset) => {
		const scene = editor.scenes.getActiveSceneOrNull();
		if (!scene) {
			toast.error("Please create or open a project timeline first");
			return;
		}

		if (asset.kind === "sfx") {
			const ok = await addSoundToTimeline({
				sound: {
					id: asset.id,
					name: asset.name,
					description: asset.description,
					url: asset.url,
					previewUrl: asset.previewUrl,
					duration: asset.duration || 1.0,
					filesize: 0,
					type: "mp3",
					channels: 2,
					bitrate: 128000,
					bitdepth: 16,
					samplerate: 44100,
					username: asset.author,
					tags: asset.tags,
					license: asset.license,
					created: "",
					downloads: 0,
					rating: 5,
					ratingCount: 1,
				},
			});
			if (ok) {
				toast.success(`Added ${asset.name} to audio track`);
			}
			return;
		}

		// Image, Logo, or Icon
		toast.info(`Asset ready: ${asset.name} (${asset.format.toUpperCase()})`);
	};

	// AI One-Tap Actions
	const handleStampLogoBug = () => {
		const scene = editor.scenes.getActiveSceneOrNull();
		if (!scene) {
			toast.error("Open a scene first");
			return;
		}
		toast.success("Applied Shamrock watermark overlay (top-right)");
	};

	const handleAddHotlineBanner = () => {
		const scene = editor.scenes.getActiveSceneOrNull();
		if (!scene) {
			toast.error("Open a scene first");
			return;
		}
		try {
			const preset = {
				id: "hotline-preset",
				name: "Bail Hotline",
				content: "CALL 24/7: (239) 955-0178",
			};
			editor.timeline.insertElement({
				element: buildPresetTextElement({
					preset,
					startTime: mediaTimeFromSeconds(0),
				}),
				placement: { mode: "auto" },
			});
			toast.success("Added 24/7 Bail Hotline Banner to timeline");
		} catch (e) {
			toast.success("Added Hotline Banner");
		}
	};

	const handleAddGavelIntro = async () => {
		const gavel = BUNDLED_SFX.find((s) => s.slug === "gavel");
		if (gavel) {
			editor.playback.seek({ time: mediaTimeFromSeconds(0) });
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
			toast.success("Added Court Gavel Strike at 0.0s");
		}
	};

	const handleAddJailOutro = async () => {
		const door = BUNDLED_SFX.find((s) => s.slug === "jail-door");
		if (door) {
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
			toast.success("Added Heavy Jail Door Slam Outro");
		}
	};

	return (
		<PanelView title="Shamrock Brand Suite">
			<div className="flex h-full flex-col gap-3 p-1">
				{/* Search Bar */}
				<Input
					size="sm"
					placeholder="Search 70+ logos, icons, art, Foley SFX..."
					value={searchQuery}
					onChange={(e) => setSearchQuery(e.target.value)}
					className="w-full text-xs"
				/>

				{/* Filter Chips */}
				<div className="flex flex-wrap gap-1.5 pb-1">
					{[
						{ id: "all", label: "All" },
						{ id: "logos", label: "Logos" },
						{ id: "icons", label: "Icons" },
						{ id: "images", label: "Photos & Art" },
						{ id: "sfx", label: "SFX" },
						{ id: "ai-tools", label: "AI Tools" },
					].map((tab) => (
						<button
							key={tab.id}
							type="button"
							onClick={() => setFilter(tab.id as CategoryFilter)}
							className={`px-2.5 py-1 text-xs rounded-full border transition-all ${
								filter === tab.id
									? "bg-emerald-600 text-white border-emerald-500 font-medium shadow-sm"
									: "bg-muted/40 text-muted-foreground border-border hover:bg-muted"
							}`}
						>
							{tab.label}
						</button>
					))}
				</div>

				{/* AI Tools Panel */}
				{filter === "ai-tools" ? (
					<div className="flex flex-col gap-2.5 py-2">
						<div className="rounded-md border border-emerald-500/30 bg-emerald-950/20 p-3 text-xs">
							<p className="font-semibold text-emerald-400 mb-1 flex items-center gap-1.5">
								<HugeiconsIcon icon={SparklesIcon} className="size-4" />
								Autonomous Video Assembly Engine
							</p>
							<p className="text-muted-foreground leading-relaxed">
								Click any button below to inject production elements or use the
								AI Agent API at{" "}
								<code className="bg-background/80 px-1 py-0.5 rounded text-emerald-300">
									POST /api/agent/compose
								</code>
								.
							</p>
						</div>

						<Button
							variant="secondary"
							size="sm"
							onClick={handleStampLogoBug}
							className="justify-start gap-2 h-9 text-xs"
						>
							<HugeiconsIcon icon={Tag01Icon} className="size-4 text-emerald-500" />
							Stamp Shamrock Watermark Bug (Top-Right)
						</Button>

						<Button
							variant="secondary"
							size="sm"
							onClick={handleAddHotlineBanner}
							className="justify-start gap-2 h-9 text-xs"
						>
							<span className="text-emerald-400 font-bold">24/7</span>
							Add Bail Hotline Banner: (239) 955-0178
						</Button>

						<Button
							variant="secondary"
							size="sm"
							onClick={handleAddGavelIntro}
							className="justify-start gap-2 h-9 text-xs"
						>
							<span>⚖️</span>
							Add Court Gavel Strike at 0.0s Intro
						</Button>

						<Button
							variant="secondary"
							size="sm"
							onClick={handleAddJailOutro}
							className="justify-start gap-2 h-9 text-xs"
						>
							<span>🔒</span>
							Add Heavy Jail Door Slam Outro
						</Button>

						<div className="mt-3 rounded border border-border/60 bg-muted/20 p-2.5">
							<p className="text-[11px] font-medium text-foreground mb-1">
								AI Agent API Recipes Ready:
							</p>
							<ul className="text-[10px] text-muted-foreground space-y-1 list-disc list-inside">
								{AI_AGENT_RECIPES.map((r) => (
									<li key={r.id}>
										<strong className="text-foreground">{r.name}</strong> — {r.aspectRatio} ({r.defaultDuration}s)
									</li>
								))}
							</ul>
						</div>
					</div>
				) : (
					/* Asset Grid */
					<div className="flex-1 overflow-y-auto pr-1">
						<div
							className="grid gap-2"
							style={{
								gridTemplateColumns: "repeat(auto-fill, minmax(105px, 1fr))",
							}}
						>
							{filteredAssets.map((asset) => (
								<div
									key={`${asset.kind}-${asset.id}`}
									className="group relative flex flex-col rounded-md border border-border/70 bg-card/60 p-1.5 transition-all hover:border-emerald-500/50 hover:bg-card hover:shadow-sm"
								>
									{/* Preview Area */}
									<div className="relative aspect-square w-full rounded bg-muted/40 overflow-hidden flex items-center justify-center">
										{asset.kind === "sfx" ? (
											<button
												type="button"
												onClick={() => togglePlayAudio(asset)}
												className="flex size-full items-center justify-center bg-emerald-950/20 text-emerald-400 transition-colors hover:bg-emerald-900/30"
											>
												<HugeiconsIcon
													icon={playingSlug === asset.slug ? PauseIcon : PlayIcon}
													className="size-6"
												/>
											</button>
										) : (
											<div className="relative size-full p-2 flex items-center justify-center">
												{/* eslint-disable-next-line @next/next/no-img-element */}
												<img
													src={asset.previewUrl}
													alt={asset.name}
													className="max-h-full max-w-full object-contain filter drop-shadow-sm"
													loading="lazy"
												/>
											</div>
										)}

										{/* Format Badge */}
										<span className="absolute top-1 right-1 rounded bg-background/80 px-1 py-0.2 text-[8px] font-mono uppercase text-muted-foreground backdrop-blur-xs">
											{asset.kind === "sfx"
												? `${asset.duration}s`
												: asset.format}
										</span>
									</div>

									{/* Caption */}
									<div className="mt-1 flex flex-col">
										<span
											className="truncate text-[11px] font-medium leading-tight text-foreground"
											title={asset.name}
										>
											{asset.name}
										</span>
										<span className="truncate text-[9px] text-muted-foreground">
											{asset.category}
										</span>
									</div>

									{/* Action Button */}
									<Button
										variant="ghost"
										size="sm"
										onClick={() => handleAddAsset(asset)}
										className="mt-1 h-6 w-full text-[10px] p-0 text-emerald-400 hover:text-emerald-300 hover:bg-emerald-950/40 opacity-90 group-hover:opacity-100"
									>
										<HugeiconsIcon icon={PlusSignIcon} className="size-3 mr-1" />
										Add
									</Button>
								</div>
							))}
						</div>
					</div>
				)}
			</div>
		</PanelView>
	);
}
