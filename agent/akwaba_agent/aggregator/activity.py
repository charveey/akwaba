"""Seuil d'activité : N flux récents avec réponse, sur K fenêtres consécutives. Pur, sans horloge."""
from __future__ import annotations

from typing import Dict


class ActivityTracker:
    def __init__(self, min_replied: int = 3, required_windows: int = 2) -> None:
        if min_replied < 1 or required_windows < 1:
            raise ValueError("min_replied et required_windows doivent être >= 1")
        self.min_replied = min_replied
        self.required_windows = required_windows
        self._streak: Dict[str, int] = {}

    def update(self, replied_by_src: Dict[str, int]) -> Dict[str, int]:
        """Une fenêtre de plus. Retourne la série en cours des seules sources actives sur cette fenêtre."""
        current: Dict[str, int] = {}
        for src, replied in replied_by_src.items():
            if replied >= self.min_replied:
                current[src] = self._streak.get(src, 0) + 1
        self._streak = current           # une source sous le seuil, ou absente, repart de zéro
        return dict(current)

    def reset(self) -> None:
        self._streak = {}
