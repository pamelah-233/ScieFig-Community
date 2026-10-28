from __future__ import annotations

from dataclasses import dataclass, field

from core.models.panel import BasePanel


@dataclass
class PanelRegistry:
    panels: dict[str, BasePanel] = field(default_factory=dict)

    @classmethod
    def from_panels(cls, panels: list[BasePanel]) -> "PanelRegistry":
        registry = cls()
        for panel in panels:
            registry.register(panel)
        return registry

    def register(self, panel: BasePanel) -> None:
        if not panel.panel_uuid:
            raise ValueError("Panel Object must have panel_uuid before registration.")
        if panel.panel_uuid in self.panels:
            raise ValueError(f"Duplicate panel_uuid in PanelRegistry: {panel.panel_uuid}")
        self.panels[panel.panel_uuid] = panel

    def unregister(self, panel_uuid: str) -> BasePanel:
        try:
            return self.panels.pop(panel_uuid)
        except KeyError as exc:
            raise KeyError(f"Panel not found: {panel_uuid}") from exc

    def get(self, panel_uuid: str) -> BasePanel | None:
        return self.panels.get(panel_uuid)

    def to_index(self) -> list[dict[str, str]]:
        return [
            {
                "panel_uuid": panel.panel_uuid,
                "panel_id": panel.panel_id,
                "panel_type": panel.panel_type,
                "source_module": panel.source_module,
            }
            for panel in self.panels.values()
        ]
