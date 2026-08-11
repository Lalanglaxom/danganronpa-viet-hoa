import json
from pathlib import Path

from dr_po_toolkit.models import POEntry, ReplacementRule
from dr_po_toolkit.rules import apply_rules_to_entry, load_rules, save_rules


def make_rule(find: str, replace: str, **kwargs) -> ReplacementRule:
    return ReplacementRule(id="test", find=find, replace=replace, **kwargs)


def test_rule_supports_ordered_semicolon_pairs():
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="", msgstr="foo bar foo")
    rule = make_rule("foo;bar", "one;two")

    text, hits = apply_rules_to_entry(entry, [rule])

    assert text == "one two one"
    assert sum(count for _rule, count, _before, _after in hits) == 3


def test_rule_can_replace_two_or_more_spaces_with_one_space():
    rule = make_rule("  ;", " ;")

    for source in ("hello  world", "hello   world", "hello    world", "hello     world"):
        text, hits = apply_rules_to_entry(POEntry(index=0, msgctxt=None, msgid="", msgstr=source), [rule])
        assert text == "hello world"
        assert sum(count for _rule, count, _before, _after in hits) >= 1


def test_rules_run_strictly_top_to_bottom_and_cascade():
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="", msgstr="A")
    first = make_rule("A", "B")
    second = make_rule("B", "C")

    text, _hits = apply_rules_to_entry(entry, [first, second])

    assert text == "C"

    reversed_text, _hits = apply_rules_to_entry(entry, [second, first])
    assert reversed_text == "B"


def test_legacy_priority_is_ignored_and_file_order_is_preserved(tmp_path: Path):
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "rules": [
                    {"id": "strong", "priority": 900, "find": "B", "replace": "C"},
                    {"id": "weak", "priority": 100, "find": "A", "replace": "B"},
                ],
            }
        ),
        encoding="utf-8",
    )

    rules = load_rules(path)
    assert [(rule.find, rule.replace) for rule in rules] == [("B", "C"), ("A", "B")]

    text, _hits = apply_rules_to_entry(POEntry(index=0, msgctxt=None, msgid="", msgstr="A"), rules)
    assert text == "B"

    save_rules(path, rules)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 3
    assert [(rule["find"], rule["replace"]) for rule in saved["rules"]] == [("B", "C"), ("A", "B")]
    assert all("id" not in rule and "priority" not in rule for rule in saved["rules"])


def test_preset_replace_ignores_wrapping_newline_for_spaced_phrase():
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="", msgstr="foo\nbar remains\nuntouched")
    rule = make_rule("foo bar", "baz")

    text, hits = apply_rules_to_entry(entry, [rule])

    assert text == "baz remains\nuntouched"
    assert sum(count for _rule, count, _before, _after in hits) == 1


def test_preset_replace_ignores_newline_between_adjacent_characters():
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="", msgstr="foo\nbar")
    rule = make_rule("foobar", "baz")

    text, hits = apply_rules_to_entry(entry, [rule])

    assert text == "baz"
    assert sum(count for _rule, count, _before, _after in hits) == 1


def test_newlines_in_preset_find_are_ignored_too():
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="", msgstr="foobar")
    rule = make_rule("foo\nbar", "baz")

    text, hits = apply_rules_to_entry(entry, [rule])

    assert text == "baz"
    assert sum(count for _rule, count, _before, _after in hits) == 1


def test_newline_only_preset_does_not_match_everywhere():
    entry = POEntry(index=0, msgctxt="0001 | MAKOTO", msgid="", msgstr="foo\nbar")
    rule = make_rule("\n", "baz")

    text, hits = apply_rules_to_entry(entry, [rule])

    assert text == "foo\nbar"
    assert hits == []
