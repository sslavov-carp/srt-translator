from __future__ import annotations

from srt_translator.fetch.models import Candidate, MediaInfo
from srt_translator.fetch.ranking import RankOptions, rank, score_candidate


def _candidate(**overrides) -> Candidate:
    values = {
        "source": "test",
        "language": "bg",
        "candidate_id": "1",
        "release": "Movie.2010.1080p.BluRay.x264-GRP",
        "download_count": 100,
        "rating": 6.0,
        "subtitle_format": "srt",
    }
    values.update(overrides)
    return Candidate(**values)


def _movie(**overrides) -> MediaInfo:
    values = {
        "title": "Movie",
        "year": 2010,
        "release_name": "Movie.2010.1080p.BluRay.x264-GRP",
    }
    values.update(overrides)
    return MediaInfo(**values)


def test_hash_match_dominates_other_signals() -> None:
    media = _movie()
    hashed = _candidate(moviehash_match=True, download_count=0, rating=0.0)
    popular = _candidate(
        candidate_id="2",
        download_count=99999,
        rating=10.0,
        from_trusted=True,
        release="Something.Else.480p.HDTV",
    )

    ranked = rank([popular, hashed], media, language="bg")

    assert ranked[0].candidate.candidate_id == "1"
    assert ranked[0].score > ranked[1].score


def test_plain_subtitles_beat_hearing_impaired() -> None:
    media = _movie()
    plain = _candidate(candidate_id="plain", hearing_impaired=False)
    hi = _candidate(
        candidate_id="hi",
        hearing_impaired=True,
        download_count=plain.download_count,
        rating=plain.rating,
    )

    ranked = rank([hi, plain], media, language="bg")

    assert ranked[0].candidate.candidate_id == "plain"


def test_multi_cd_is_rejected() -> None:
    ranked = rank([_candidate(nb_cd=2)], _movie(), language="bg")

    assert ranked[0].rejection == "multi-CD subtitle (2 files)"
    assert not ranked[0].accepted


def test_wrong_language_is_rejected() -> None:
    ranked = rank([_candidate(language="en")], _movie(), language="bg")

    assert "wrong language" in (ranked[0].rejection or "")


def test_wrong_episode_is_rejected() -> None:
    media = MediaInfo(title="Show", kind="episode", season=2, episode=5)
    ranked = rank(
        [_candidate(season=2, episode=4)],
        media,
        language="bg",
    )

    assert ranked[0].rejection == "wrong episode (4)"


def test_matching_episode_is_accepted() -> None:
    media = MediaInfo(title="Show", kind="episode", season=2, episode=5)
    ranked = rank([_candidate(season=2, episode=5)], media, language="bg")

    assert ranked[0].accepted


def test_machine_translated_is_rejected_unless_allowed() -> None:
    media = _movie()
    candidate = _candidate(machine_translated=True)

    rejected = rank([candidate], media, language="bg")
    allowed = rank(
        [candidate],
        media,
        language="bg",
        options=RankOptions(allow_machine=True, min_score=0),
    )

    assert rejected[0].rejection == "machine-translated subtitle"
    assert allowed[0].accepted


def test_non_srt_is_rejected() -> None:
    ranked = rank([_candidate(subtitle_format="ass")], _movie(), language="bg")

    assert "unsupported format" in (ranked[0].rejection or "")


def test_min_score_rejects_weak_candidates() -> None:
    media = MediaInfo(title="Unknown")
    candidate = _candidate(
        release="unknown",
        download_count=0,
        rating=0.0,
        from_trusted=False,
    )

    ranked = rank(
        [candidate],
        media,
        language="bg",
        options=RankOptions(min_score=1000),
    )

    assert ranked[0].rejection and "below minimum" in ranked[0].rejection


def test_release_match_adds_points() -> None:
    media = _movie()
    matching = _candidate()
    different = _candidate(release="Movie.2010.480p.HDTV.x264-OTHER")

    matching_score, matching_reasons = score_candidate(matching, media)
    different_score, _ = score_candidate(different, media)

    assert matching_score > different_score
    assert any("resolution" in reason for reason in matching_reasons)


def test_fps_mismatch_lowers_score() -> None:
    media = _movie(fps=23.976)
    matching = _candidate(candidate_id="a", fps=23.976)
    mismatched = _candidate(candidate_id="b", fps=25.0)

    assert score_candidate(matching, media)[0] > score_candidate(mismatched, media)[0]


def test_ranked_output_puts_rejections_last() -> None:
    media = _movie()
    good = _candidate(candidate_id="good", download_count=10)
    bad = _candidate(candidate_id="bad", nb_cd=2)

    ranked = rank([bad, good], media, language="bg")

    assert ranked[0].candidate.candidate_id == "good"
    assert ranked[-1].candidate.candidate_id == "bad"
