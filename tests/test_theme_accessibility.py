"""Contrast is checked per role, against every surface a token actually meets.

The previous version checked two pairs against `surface` only, which is why
text_muted and accent_gold sat at 4.41 and 4.37 on the page background without
anything failing. surface_alt — the alternating row fill — is darker still and
was never checked at all.

Roles matter as much as coverage. `warning` is amber: it cannot reach AA as text
on a light page, and darkening it to brown would destroy the very signal it
carries. It is a fill, and what has to pass is the dark text placed on top of
it. Demanding that every colour work as body text would have quietly turned a
warning badge into mud.
"""
import unittest
from unittest import mock

from theme_tokens import COLORS


AA = 4.5


def contrast(a, b):
    def luminance(value):
        channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        channels = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]

    first, second = sorted((luminance(a), luminance(b)), reverse=True)
    return (first + .05) / (second + .05)


# Every light surface a foreground colour can land on. surface_alt is the
# strictest of the three, so it decides the palette.
LIGHT_SURFACES = ("bg", "surface", "surface_alt")

# Roles are per theme, because they genuinely differ.
#
# In the light theme the olive `primary` is dark, so it reads as text and also
# carries light text as a fill. In the dark theme it cannot do both: to be
# legible on a dark surface it would have to be light, and to carry light text
# it has to stay dark. There it is a fill only, and primary_muted is the brand
# colour when something has to be written in it.
#
# The status colours flip the same way. On a light page they are dark fills
# under light text; on a dark page they are bright fills under dark text.
TEXT_TOKENS = (
    "text", "text_secondary", "text_muted", "primary", "accent_gold", "cta", "info",
)

DARK_TEXT_TOKENS = (
    "text", "text_secondary", "text_muted", "primary_muted", "accent_gold",
    "cta", "info",
)

# Tokens used as fills, paired with the foreground that is drawn on them.
FILL_TOKENS = {
    "primary": "text_on_primary",
    "primary_hover": "text_on_primary",
    "cta": "text_on_primary",
    "cta_hover": "text_on_primary",
    "accent_gold": "text_on_primary",
    "success": "text_on_primary",
    "danger": "text_on_primary",
    "info": "text_on_primary",
    # Amber carries its meaning by being light; dark text is what goes on it.
    "warning": "text",
}

DARK_FILL_TOKENS = {
    "primary": "text_on_primary",
    "primary_hover": "text_on_primary",
    # Everything else is a bright accent on a dark page, so the page background
    # colour is what gets written on top of it.
    "accent_gold": "bg",
    "cta": "bg",
    "cta_hover": "bg",
    "success": "bg",
    "danger": "bg",
    "info": "bg",
    "warning": "bg",
}


class ThemeAccessibilityTests(unittest.TestCase):
    def test_light_tokens_match_the_landing(self):
        self.assertEqual("#F5F1E8", COLORS["bg"])
        self.assertEqual("#FFFEFA", COLORS["surface"])
        self.assertEqual("#17231B", COLORS["text"])
        self.assertEqual("#24432D", COLORS["primary"])

    def test_text_tokens_meet_aa_on_every_light_surface(self):
        failures = []
        for token in TEXT_TOKENS:
            for surface in LIGHT_SURFACES:
                ratio = contrast(COLORS[token], COLORS[surface])
                if ratio < AA:
                    failures.append(
                        "{} on {}: {:.2f}".format(token, surface, ratio)
                    )
        self.assertEqual([], failures)

    def test_fill_tokens_meet_aa_against_their_own_foreground(self):
        failures = []
        for fill, foreground in FILL_TOKENS.items():
            ratio = contrast(COLORS[foreground], COLORS[fill])
            if ratio < AA:
                failures.append(
                    "{} on {}: {:.2f}".format(foreground, fill, ratio)
                )
        self.assertEqual([], failures)

    def test_warning_is_a_fill_and_is_not_offered_as_text(self):
        """Guards the reasoning above: if someone adds warning to TEXT_TOKENS
        the suite should tell them why that cannot work, not go quietly amber."""
        self.assertNotIn("warning", TEXT_TOKENS)
        self.assertLess(contrast(COLORS["warning"], COLORS["bg"]), AA)
        self.assertGreaterEqual(contrast(COLORS["text"], COLORS["warning"]), AA)

    def test_focus_ring_tracks_the_accent(self):
        """They are the same colour by design; drifting apart is a bug."""
        self.assertEqual(COLORS["accent_gold"], COLORS["border_focus"])

    def test_registered_widgets_observe_the_new_theme_during_callback(self):
        from theme_manager_v2 import ThemeManager

        manager = ThemeManager()
        observed = []

        class Probe:
            def apply_theme(self, _name):
                observed.append(manager.current)

        class FakeApp:
            def setStyleSheet(self, _qss):
                pass

        probe = Probe()
        manager.register_widget(probe)
        with mock.patch.object(manager, "_apply_palette"), \
                mock.patch.object(manager, "_apply_matplotlib"), \
                mock.patch.object(manager, "save_persisted"):
            manager.apply(FakeApp(), "dark")
        self.assertEqual(["dark"], observed)


DARK_SURFACES = ("bg", "surface", "surface_alt")


class DarkPaletteTests(unittest.TestCase):
    """Dark mode existed in three places and in none of them as tokens.

    theme_dark.qss carried the stylesheet colours, theme_manager_v2 a twelve-key
    QPalette, and analytics_dialog and chat_panel their own hex literals. The
    three agreed by luck and hand-copying, and nothing checked the dark theme
    for contrast at all — the accessibility suite only ever saw the light one.
    """

    def test_dark_palette_covers_exactly_the_same_tokens(self):
        from theme_tokens import COLORS, DARK_COLORS

        self.assertEqual(set(COLORS), set(DARK_COLORS))

    def test_dark_text_tokens_meet_aa_on_every_dark_surface(self):
        from theme_tokens import DARK_COLORS

        failures = []
        for token in DARK_TEXT_TOKENS:
            for surface in DARK_SURFACES:
                ratio = contrast(DARK_COLORS[token], DARK_COLORS[surface])
                if ratio < AA:
                    failures.append("{} on {}: {:.2f}".format(token, surface, ratio))
        self.assertEqual([], failures)

    def test_dark_fill_tokens_meet_aa_against_their_own_foreground(self):
        from theme_tokens import DARK_COLORS

        failures = []
        for fill, foreground in DARK_FILL_TOKENS.items():
            ratio = contrast(DARK_COLORS[foreground], DARK_COLORS[fill])
            if ratio < AA:
                failures.append("{} on {}: {:.2f}".format(foreground, fill, ratio))
        self.assertEqual([], failures)

    def test_theme_selection_is_a_single_lookup(self):
        from theme_tokens import COLORS, DARK_COLORS, colors_for

        self.assertIs(COLORS, colors_for("light"))
        self.assertIs(DARK_COLORS, colors_for("dark"))
        # An unknown name must not silently return the dark palette.
        self.assertIs(COLORS, colors_for("nonsense"))

    def test_qt_palette_is_derived_from_the_tokens(self):
        """Two hand-maintained copies of the same colours drift; one of them
        already had: LIGHT_PALETTE['link'] was gold while COLORS['info'] was
        blue, so Qt links and chat links did not match."""
        import theme_manager_v2 as manager
        from theme_tokens import COLORS, DARK_COLORS

        for palette, tokens in ((manager.LIGHT_PALETTE, COLORS),
                                (manager.DARK_PALETTE, DARK_COLORS)):
            self.assertEqual(tokens["bg"], palette["window"])
            self.assertEqual(tokens["surface"], palette["base"])
            self.assertEqual(tokens["surface_alt"], palette["alt_base"])
            self.assertEqual(tokens["text"], palette["text"])
            self.assertEqual(tokens["primary"], palette["highlight"])


if __name__ == "__main__":
    unittest.main()
