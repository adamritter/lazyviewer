"""Picker match-list recomputation helpers."""

from __future__ import annotations

from ...search.fuzzy import fuzzy_match_labels

PICKER_RESULT_LIMIT = 200


class PickerMatchingMixin:
    """Match refresh methods shared by symbol and command picker modes."""

    def refresh_symbol_picker_matches(self, reset_selection: bool = False) -> None:
        """Recompute visible symbol matches from current picker query."""
        matched = fuzzy_match_labels(
            self.state.picker_query,
            self.state.picker_symbol_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        self.state.picker_matches = []
        self.state.picker_match_labels = [label for _, label, _ in matched]
        self.state.picker_match_lines = [self.state.picker_symbol_lines[idx] for idx, _, _ in matched]
        self.state.picker_match_commands = []
        if self.state.picker_match_labels:
            self.state.picker_message = ""
        elif not self.state.picker_message:
            self.state.picker_message = " no matching symbols"
        self.state.picker_selected = 0 if reset_selection else max(
            0,
            min(self.state.picker_selected, max(0, len(self.state.picker_match_labels) - 1)),
        )
        if reset_selection or not self.state.picker_match_labels:
            self.state.picker_list_start = 0

    def refresh_command_picker_matches(self, reset_selection: bool = False) -> None:
        """Recompute command palette matches from current picker query."""
        matched = fuzzy_match_labels(
            self.state.picker_query,
            self.state.picker_command_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        self.state.picker_matches = []
        self.state.picker_match_labels = [label for _, label, _ in matched]
        self.state.picker_match_lines = []
        self.state.picker_match_commands = [self.state.picker_command_ids[idx] for idx, _, _ in matched]
        if self.state.picker_match_labels:
            self.state.picker_message = ""
        elif not self.state.picker_message:
            self.state.picker_message = " no matching commands"
        self.state.picker_selected = 0 if reset_selection else max(
            0,
            min(self.state.picker_selected, max(0, len(self.state.picker_match_labels) - 1)),
        )
        if reset_selection or not self.state.picker_match_labels:
            self.state.picker_list_start = 0

    def refresh_active_picker_matches(self, reset_selection: bool = False) -> None:
        """Refresh matches for whichever picker mode is currently active."""
        if self.state.picker_mode == "commands":
            self.refresh_command_picker_matches(reset_selection=reset_selection)
            return
        self.refresh_symbol_picker_matches(reset_selection=reset_selection)
