from supergod.orchestrator.stuck_detector import StuckDetector


def test_stuck_detector_triggers_on_repetition():
    detector = StuckDetector(window_size=5, threshold=0.8)
    triggered = False
    for _ in range(5):
        triggered = detector.feed("s1", "same output line")
    assert triggered


def test_stuck_detector_ignores_control_lines():
    detector = StuckDetector(window_size=3, threshold=0.66)
    assert not detector.feed("s1", "[turn completed]")
    assert not detector.feed("s1", "")


def test_stuck_detector_clear():
    detector = StuckDetector(window_size=3, threshold=0.66)
    detector.feed("s1", "a")
    detector.feed("s1", "a")
    detector.clear("s1")
    # Buffer reset means no immediate trigger.
    assert not detector.feed("s1", "a")
