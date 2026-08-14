import { DraggableItem } from "@/components/editor/panels/assets/draggable-item";
import { PanelView } from "@/components/editor/panels/assets/views/base-panel";
import { useEditor } from "@/editor/use-editor";
import { DEFAULTS } from "@/timeline/defaults";
import { TEXT_PRESETS, buildPresetTextElement } from "@/text/presets";
import type { MediaTime } from "@/wasm";

export function TextView() {
	const editor = useEditor();

	const handleAdd = ({
		presetId,
		currentTime,
	}: {
		presetId: string;
		currentTime: MediaTime;
	}) => {
		if (!editor.scenes.getActiveScene()) return;
		const preset =
			TEXT_PRESETS.find((item) => item.id === presetId) ?? TEXT_PRESETS[3];
		editor.timeline.insertElement({
			element: buildPresetTextElement({ preset, startTime: currentTime }),
			placement: { mode: "auto" },
		});
	};

	return (
		<PanelView title="Text">
			<div
				className="grid gap-2"
				style={{ gridTemplateColumns: "repeat(auto-fill, minmax(110px, 1fr))" }}
			>
				{TEXT_PRESETS.map((preset) => (
					<DraggableItem
						key={preset.id}
						name={preset.name}
						preview={
							<div className="bg-accent flex size-full items-center justify-center rounded px-1">
								<span className="line-clamp-3 text-center text-[10px] select-none">
									{preset.content}
								</span>
							</div>
						}
						dragData={{
							id: preset.id,
							type: DEFAULTS.text.element.type,
							name: preset.name,
							content: preset.content,
						}}
						aspectRatio={1}
						onAddToTimeline={({ currentTime }) =>
							handleAdd({ presetId: preset.id, currentTime })
						}
					/>
				))}
			</div>
		</PanelView>
	);
}
