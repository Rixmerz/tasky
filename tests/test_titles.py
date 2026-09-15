import json

from tasky.titles import TitleWatcher, title_from_entry


def _append(path, *entries, newline=True):
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + ("\n" if newline else ""))


def test_rename_wins_over_generated_name(tmp_path):
    path = tmp_path / "s.jsonl"
    _append(
        path,
        {"type": "user", "message": {"content": "hi"}},
        {"type": "ai-title", "aiTitle": "Generated name"},
        {"type": "custom-title", "customTitle": "tasky main"},
        {"type": "ai-title", "aiTitle": "Later generated name"},
    )

    assert TitleWatcher().title(str(path)) == "tasky main"


def test_generated_name_used_when_never_renamed(tmp_path):
    path = tmp_path / "s.jsonl"
    _append(path, {"type": "ai-title", "aiTitle": "Generated  name\n"})

    assert TitleWatcher().title(str(path)) == "Generated name"


def test_later_rename_is_picked_up_incrementally(tmp_path):
    path = tmp_path / "s.jsonl"
    _append(path, {"type": "custom-title", "customTitle": "first"})
    watcher = TitleWatcher()
    assert watcher.title(str(path)) == "first"

    _append(path, {"type": "custom-title", "customTitle": "second"})

    assert watcher.title(str(path)) == "second"


def test_partial_last_line_waits_for_the_writer(tmp_path):
    path = tmp_path / "s.jsonl"
    watcher = TitleWatcher()
    _append(path, {"type": "custom-title", "customTitle": "half"}, newline=False)
    assert watcher.title(str(path)) is None

    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    assert watcher.title(str(path)) == "half"


def test_rewritten_file_is_rescanned(tmp_path):
    path = tmp_path / "s.jsonl"
    _append(path, {"type": "custom-title", "customTitle": "a much longer original name"})
    watcher = TitleWatcher()
    assert watcher.title(str(path)) == "a much longer original name"

    path.write_text(json.dumps({"type": "custom-title", "customTitle": "new"}) + "\n")

    assert watcher.title(str(path)) == "new"


def test_missing_bad_or_foreign_paths_have_no_title(tmp_path):
    bad = tmp_path / "s.jsonl"
    bad.write_text('{"type": "custom-title", "customTitle": \n{broken\n')
    other = tmp_path / "notes.txt"
    other.write_text(json.dumps({"type": "custom-title", "customTitle": "x"}) + "\n")
    watcher = TitleWatcher()

    assert watcher.title(None) is None
    assert watcher.title(str(tmp_path / "missing.jsonl")) is None
    assert watcher.title(str(bad)) is None
    assert watcher.title(str(other)) is None


def test_title_from_entry_ignores_non_strings():
    assert title_from_entry({"type": "custom-title", "customTitle": 5}) == (None, None)
    assert title_from_entry(["custom-title"]) == (None, None)
    assert title_from_entry({"type": "custom-title", "customTitle": "x" * 500})[0] == "x" * 200
