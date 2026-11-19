from src.live.ranking import rank_chat_windows, ten_second_counts


def test_counts_exclude_messages_before_stream_start():
    assert ten_second_counts([99, 100, 109.9, 110], 100) == {0: 2, 1: 1}


def test_burst_wins_over_quiet_windows_and_no_overlap():
    windows = rank_chat_windows({i: 1 for i in range(50)} | {20: 100}, max_windows=2)
    assert len(windows) == 2
    assert any(start <= 200 < end for start, end, _ in windows)
    assert windows[0][1] <= windows[1][0]


def test_no_chat_falls_back():
    assert rank_chat_windows({}) == []
