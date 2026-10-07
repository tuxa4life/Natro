from natro_pc.hotkey import PressAndHold


def test_hold_to_talk_and_tap_for_hands_free():
    key = PressAndHold(tap_seconds=0.4)
    assert key.press(10.0) == "start"
    assert key.press(10.1) is None  # key repeat while held
    assert key.release(12.0) == "send"  # held 2 s: send on release
    assert key.press(20.0) == "start"
    assert key.release(20.2) == "hands_free"  # a tap: recording goes on hands-free
    assert key.press(25.0) == "stop"  # tapping again stops it
    assert key.release(25.1) is None
    assert key.press(30.0) == "start"


def test_hands_free_ends_by_itself():
    key = PressAndHold()
    key.press(0.0)
    assert key.release(0.1) == "hands_free"
    key.finished()  # silence ended the recording
    assert key.press(5.0) == "start"
