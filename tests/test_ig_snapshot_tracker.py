from supergod.tools.ig_snapshot_tracker import (
    extract_count,
    extract_post_metric,
    parse_compact_count,
)


def test_parse_compact_count_variants():
    assert parse_compact_count("1,234") == 1234
    assert parse_compact_count("2.5k") == 2500
    assert parse_compact_count("3m") == 3_000_000
    assert parse_compact_count("7.2b") == 7_200_000_000
    assert parse_compact_count("bad") is None


def test_extract_profile_counts_from_mixed_text():
    texts = [
        "some other line",
        "1,245 followers",
        "following 612",
        "37 posts",
    ]
    followers, _ = extract_count(texts, "followers")
    following, _ = extract_count(texts, "following")
    posts, _ = extract_count(texts, "posts")
    assert followers == 1245
    assert following == 612
    assert posts == 37


def test_extract_post_metrics():
    texts = [
        "Viewed by 9,876 accounts",
        "9,876 views",
        "3,201 likes",
        "124 comments",
    ]
    likes, _ = extract_post_metric(texts, "likes")
    views, _ = extract_post_metric(texts, "views")
    comments, _ = extract_post_metric(texts, "comments")
    assert likes == 3201
    assert views == 9876
    assert comments == 124

