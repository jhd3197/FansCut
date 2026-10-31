import pytest

from fanscut.planner import CutOptions, PlanEpisode, plan_cut, wants


def ep(n, label="canon", duration=1440.0, include=None, trims=None, file="auto"):
    return PlanEpisode(id=f"S01E{n:02d}", number=n, season=1,
                       file=f"/m/e{n}.mkv" if file == "auto" else file, duration=duration,
                       label=label, include=include, trims=trims or {}, title=f"Ep {n}")


LABELS = ["canon", "filler", "mixed", "unknown", "canon"]


def kept(preset, **kw):
    cut = plan_cut([ep(i + 1, lab) for i, lab in enumerate(LABELS)], CutOptions(preset=preset, **kw))
    return [int(e[-2:]) for e in cut.kept]


def test_presets():
    assert kept("canon_only") == [1, 4, 5]  # unknown kept by default policy
    assert kept("canon_only", unknown_policy="skip") == [1, 5]
    assert kept("canon_mixed", unknown_policy="skip") == [1, 3, 5]
    assert kept("everything", unknown_policy="skip") == [1, 2, 3, 4, 5]
    assert kept("custom") == [1, 2, 3, 4, 5]


def test_include_override_beats_preset_both_ways():
    eps = [ep(1, "filler", include=True), ep(2, "canon", include=False)]
    cut = plan_cut(eps, CutOptions(preset="canon_only"))
    assert cut.kept == ["S01E01"]
    assert cut.skipped == [{"episode_id": "S01E02", "reason": "user exclude"}]


def test_unknown_preset_rejected():
    with pytest.raises(ValueError, match="preset"):
        plan_cut([ep(1)], CutOptions(preset="nope"))


def test_whole_episodes_and_totals():
    cut = plan_cut([ep(1), ep(2, "filler"), ep(3)], CutOptions())
    assert [(s.episode_id, s.start, s.end) for s in cut.segments] == [
        ("S01E01", 0, 1440), ("S01E03", 0, 1440)]
    assert cut.segments[0].reason == "full episode"
    d = cut.to_dict()["totals"]
    assert d == {"kept": 2, "skipped": 1, "original_runtime": 4320, "final_runtime": 2880,
                 "time_saved": 1440}


def test_trims_removed_with_padding_and_clamping():
    trims = {"intro": (90.0, 180.0), "outro": (1350.0, 1440.0)}
    opts = CutOptions(pad=2.0, keep_intro_first_episode=False)
    cut = plan_cut([ep(1, trims=trims)], opts)
    assert [(s.start, s.end) for s in cut.segments] == [(0.0, 92.0), (178.0, 1352.0)]
    assert cut.segments[0].reason == "trimmed intro, outro"


def test_pad_zero_is_exact():
    cut = plan_cut([ep(1, trims={"recap": (0.0, 60.0)})], CutOptions(pad=0, min_segment=1))
    assert [(s.start, s.end) for s in cut.segments] == [(60.0, 1440.0)]


def test_keep_intro_only_on_first_kept_episode():
    eps = [ep(1, "filler", trims={"intro": (60, 150)}), ep(2, trims={"intro": (60, 150)}),
           ep(3, trims={"intro": (60, 150)})]
    cut = plan_cut(eps, CutOptions(pad=0, keep_intro_first_episode=True))
    by_ep = {}
    for s in cut.segments:
        by_ep.setdefault(s.episode_id, []).append((s.start, s.end))
    assert by_ep["S01E02"] == [(0.0, 1440.0)]  # first kept episode keeps its OP
    assert by_ep["S01E03"] == [(0.0, 60.0), (150.0, 1440.0)]


def test_close_pieces_merge_and_tiny_pieces_drop():
    # A 3 s sliver between two cuts disappears; two cuts 0.8 s apart merge into one.
    trims = {"recap": (0.0, 30.0), "intro": (33.0, 120.0), "outro": (1400.0, 1440.0)}
    cut = plan_cut([ep(1, trims=trims)],
                   CutOptions(pad=0, min_segment=5, keep_intro_first_episode=False))
    assert [(s.start, s.end) for s in cut.segments] == [(120.0, 1400.0)]
    trims = {"intro": (100.0, 200.0), "preview": (200.8, 260.0)}
    cut = plan_cut([ep(1, trims=trims)],
                   CutOptions(pad=0, merge_gap=1.0, keep_intro_first_episode=False))
    assert [(s.start, s.end) for s in cut.segments] == [(0.0, 100.0), (260.0, 1440.0)]


def test_padding_can_close_a_gap():
    # A 5 s cut with 3 s of padding each side leaves nothing to cut: the halves overlap.
    cut = plan_cut([ep(1, trims={"intro": (100.0, 105.0)})],
                   CutOptions(pad=3, keep_intro_first_episode=False))
    assert [(s.start, s.end) for s in cut.segments] == [(0.0, 1440.0)]


def test_trim_beyond_duration_is_clamped_and_overlaps_combine():
    trims = {"outro": (1400.0, 99999.0), "preview": (1390.0, 1420.0)}
    cut = plan_cut([ep(1, trims=trims)], CutOptions(pad=0))
    assert [(s.start, s.end) for s in cut.segments] == [(0.0, 1390.0)]


def test_trim_kinds_option_limits_what_is_cut():
    trims = {"intro": (60.0, 150.0), "outro": (1350.0, 1440.0)}
    cut = plan_cut([ep(1, trims=trims)],
                   CutOptions(pad=0, keep_intro_first_episode=False, trim_kinds=("outro",)))
    assert [(s.start, s.end) for s in cut.segments] == [(0.0, 1350.0)]


def test_missing_file_or_duration_is_skipped_with_reason():
    cut = plan_cut([ep(1, file=None), ep(2, duration=None), ep(3)], CutOptions())
    assert cut.kept == ["S01E03"]
    assert [s["reason"] for s in cut.skipped] == ["no file on disk"] * 2


def test_episode_trimmed_to_nothing_is_skipped():
    cut = plan_cut([ep(1, trims={"intro": (0.0, 1440.0)}), ep(2)],
                   CutOptions(keep_intro_first_episode=False))
    assert cut.kept == ["S01E02"]
    assert cut.skipped[0]["reason"] == "nothing left after trimming"


def test_empty_input():
    cut = plan_cut([], CutOptions())
    assert cut.segments == [] and cut.final_runtime == 0 and cut.time_saved == 0


def test_wants_reasons_are_readable():
    assert wants(ep(1, "filler"), CutOptions())[1] == "filler skipped by canon_only"
    assert wants(ep(1, "unknown"), CutOptions(unknown_policy="skip"))[0] is False
