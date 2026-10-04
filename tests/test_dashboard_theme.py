"""Interface themes: the Dark mode switch is presentation only (no network access)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tomllib
import unittest
from unittest.mock import patch

from guard_pdc import theme as plot_theme
from guard_pdc.config import MontandonConfig
from guard_pdc.maps import AlertAreas
from tests.test_stage6_to_8 import fixture_result

ROOT = Path(__file__).parents[1]
if str(ROOT / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "dashboard"))

import components as ui  # noqa: E402


class InterfaceThemeTests(unittest.TestCase):
    def test_themes_use_the_specified_interface_colours(self) -> None:
        light, dark = ui.INTERFACE_THEMES["light"], ui.INTERFACE_THEMES["dark"]
        self.assertEqual(
            [light[key] for key in ("page", "sidebar", "card", "border", "ink", "ink2", "accent", "select")],
            ["#F5F2ED", "#FFFEFB", "#FFFEFB", "#E2DDD6", "#2D2A28", "#79736C", "#A45A45", "#F2E3DB"],
        )
        self.assertEqual(
            [dark[key] for key in ("page", "sidebar", "card", "border", "ink", "ink2", "accent", "select")],
            ["#171819", "#1D1E20", "#252629", "#38393B", "#F1EEE8", "#B2ADA6", "#D8A078", "#34312E"],
        )
        self.assertEqual((light["on-accent"], dark["on-accent"]), ("#FFFFFF", "#1D1E20"))

    def test_figures_keep_their_canvas_and_plotting_settings(self) -> None:
        for mode in ui.INTERFACE_THEMES:
            css = ui._css(mode)
            # Figure containers keep the figures' own white canvas in both themes.
            self.assertIn(f'div[data-testid="stPlotlyChart"] {{ background:{plot_theme.SURFACE};', css)
            self.assertIn(ui.FIGURE_INHERITED, css)
        # Shared plotting settings are not interface tokens.
        self.assertEqual((plot_theme.SURFACE, plot_theme.PRIMARY, plot_theme.INK), ("#FFFFFF", "#0B7A80", "#1B2533"))
        self.assertEqual(plot_theme.SEQUENTIAL[0], "#FFF5E1")
        # Streamlit writes its theme font into Plotly text, so it must stay the original.
        config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["theme"]["font"], "sans-serif")
        self.assertNotIn("dark", config["theme"])

    def test_switching_theme_keeps_state_and_never_retrieves(self) -> None:
        from streamlit.testing.v1 import AppTest

        calls = []

        def fetch(query, **_options):
            calls.append(query)
            return replace(fixture_result(), query=query)

        app = AppTest.from_file(str(ROOT / "dashboard" / "streamlit_app.py"))
        with (
            patch("guard_pdc.service.retrieve", side_effect=fetch),
            patch("guard_pdc.config.MontandonConfig.from_env", return_value=MontandonConfig(api_token="fixture")),
            patch("guard_pdc.api.PdcApiProvider.years_with_events", return_value={2023: True, 2024: True}),
            patch("guard_pdc.maps.fetch_alert_areas", return_value=AlertAreas("missing", "stubbed in tests")),
        ):
            app.run(timeout=60)
            self.assertFalse(app.toggle(key=ui.THEME_KEY).value)  # light by default
            next(button for button in app.button if button.label == "Retrieve data").click().run(timeout=120)
            self.assertEqual(len(app.exception), 0, [item.value for item in app.exception])
            self.assertEqual(len(calls), 1)
            next(button for button in app.button if button.label == "Prepare evidence files").click().run(timeout=120)
            stored = app.session_state["pdc"]
            exports = app.session_state["exports"]
            selected = app.session_state["selected_event"]
            months = app.session_state["months"]

            for value in (True, False, True):
                app.toggle(key=ui.THEME_KEY).set_value(value).run(timeout=120)
                self.assertEqual(len(app.exception), 0, [item.value for item in app.exception])
                self.assertIs(app.toggle(key=ui.THEME_KEY).value, value)
                self.assertEqual(len(calls), 1)  # no fresh retrieval
                self.assertIs(app.session_state["pdc"], stored)  # no repeated analysis
                self.assertIs(app.session_state["exports"], exports)  # prepared downloads kept
                self.assertEqual(app.session_state["selected_event"], selected)
                self.assertEqual(app.session_state["months"], months)
            # The choice survives an unrelated rerun.
            app.run(timeout=120)
            self.assertTrue(app.toggle(key=ui.THEME_KEY).value)


if __name__ == "__main__":
    unittest.main()
