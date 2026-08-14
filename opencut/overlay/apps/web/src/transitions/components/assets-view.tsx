"use client";

import { useCallback } from "react";
import { PanelView } from "@/components/editor/panels/assets/views/base-panel";
import { DraggableItem } from "@/components/editor/panels/assets/draggable-item";
import { applyTransitionAtPlayhead } from "@/transitions/apply";
import { TRANSITION_PRESETS } from "@/transitions/catalog";

export function TransitionsView() {
	return (
		<PanelView title="Transitions">
			<p className="text-muted-foreground mb-3 text-xs">
				Park the playhead on a cut, then click or drag. Fade/slide/zoom overlap
				the two clips. Wipe/iris also land on the effects track.
			</p>
			<div
				className="grid gap-2"
				style={{ gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))" }}
			>
				{TRANSITION_PRESETS.map((preset) => (
					<TransitionItem key={preset.type} type={preset.type} name={preset.name} />
				))}
			</div>
		</PanelView>
	);
}

function TransitionItem({ type, name }: { type: string; name: string }) {
	const handleAdd = useCallback(() => {
		applyTransitionAtPlayhead({ type });
	}, [type]);

	return (
		<DraggableItem
			name={name}
			preview={
				<div className="from-primary/30 flex size-full items-center justify-center bg-linear-to-br to-transparent text-[10px] font-medium">
					{name}
				</div>
			}
			dragData={{
				id: type,
				name,
				type: "transition",
				transitionType: type,
			}}
			onAddToTimeline={handleAdd}
			aspectRatio={1}
			isRounded
			variant="card"
			containerClassName="w-full"
		/>
	);
}
