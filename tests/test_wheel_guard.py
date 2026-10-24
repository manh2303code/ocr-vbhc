"""Scrolling over a value control must not change it (Độ khẩn / Độ mật)."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication, QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from editor_widgets import WheelGuard, install_wheel_guard


def _wheel(widget, delta=-120):
    event = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0),
                        QPoint(0, delta), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event


class WheelGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.guard = install_wheel_guard(cls.app)

    def setUp(self):
        self.area = QScrollArea()
        body = QWidget()
        layout = QVBoxLayout(body)
        self.combo = QComboBox()
        self.combo.addItems(["Không", "Khẩn", "Thượng khẩn", "Hỏa tốc"])
        self.spin = QSpinBox()
        layout.addWidget(self.combo)
        layout.addWidget(self.spin)
        layout.addSpacing(2000)
        self.area.setWidget(body)
        self.area.resize(300, 200)
        self.area.show()
        QApplication.processEvents()
        # A shown window focuses its first focusable child, the combo; the
        # case under test is the pointer passing over an unfocused one.
        self.area.setFocusPolicy(Qt.StrongFocus)
        self.area.setFocus()
        self.combo.clearFocus()
        self.spin.clearFocus()
        QApplication.processEvents()

    def tearDown(self):
        self.area.close()
        self.area.deleteLater()
        QApplication.processEvents()

    def test_install_is_idempotent(self):
        self.assertIs(self.guard, install_wheel_guard(self.app))
        self.assertIsInstance(self.guard, WheelGuard)

    def test_controls_take_focus_by_click_or_tab_not_by_wheel(self):
        self.assertEqual(Qt.StrongFocus, self.combo.focusPolicy())
        self.assertEqual(Qt.StrongFocus, self.spin.focusPolicy())

    def test_wheel_over_an_unfocused_combo_scrolls_instead_of_changing_it(self):
        # An ignored wheel event is what Qt passes on to the parent (the
        # form's scroll area) for a real mouse wheel; a synthetic event is
        # not propagated, so the test checks that it was left unaccepted.
        for control in (self.combo, self.spin):
            event = _wheel(control)
            self.assertFalse(event.isAccepted(), type(control).__name__)
        self.assertEqual(0, self.combo.currentIndex())
        self.assertEqual(0, self.spin.value())

    def test_a_focused_combo_still_takes_the_wheel(self):
        # Focus is not reliable on the offscreen platform under pytest, so
        # the guard's decision is checked directly.
        event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        self.combo.hasFocus = lambda: True
        self.assertFalse(self.guard.eventFilter(self.combo, event))
        self.combo.hasFocus = lambda: False
        self.assertTrue(self.guard.eventFilter(self.combo, event))
        self.assertFalse(event.isAccepted())


if __name__ == "__main__":
    unittest.main()
