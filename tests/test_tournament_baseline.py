import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ml.baseline import recommend_tournament_matches, tournament_popularity


NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def match(identifier, tournament, *, tour="ATP", status="upcoming", tier="atp_500", days=1, doubles=False):
    return SimpleNamespace(
        provider_id=identifier,
        tournament=tournament,
        tour=tour,
        status=status,
        tier=tier,
        starts_at=NOW + timedelta(days=days),
        is_doubles=doubles,
    )


class TournamentBaselineTest(unittest.TestCase):
    def test_subscribed_tournament_outranks_more_popular_unsubscribed_event(self):
        candidates = [
            match("wimbledon", "Wimbledon", tier="grand_slam"),
            match("madrid", "Madrid"),
        ]
        global_subscriptions = [("ATP", "Madrid")] * 20 + [("ATP", "Wimbledon")]

        recommendations = recommend_tournament_matches(
            candidates,
            [("ATP", "WIMBLEDON")],
            global_subscriptions,
            now=NOW,
        )

        self.assertEqual(recommendations[0].match.provider_id, "wimbledon")
        self.assertIn("Subscribed tournament", recommendations[0].reasons)

    def test_popularity_and_tier_provide_cold_start_order(self):
        candidates = [
            match("small", "Small Event", tier="atp_500"),
            match("major", "US Open", tier="grand_slam"),
        ]
        global_subscriptions = [("ATP", "US Open")] * 4

        recommendations = recommend_tournament_matches(
            candidates, [], global_subscriptions, now=NOW
        )

        self.assertEqual(recommendations[0].match.provider_id, "major")

    def test_unavailable_and_doubles_matches_are_excluded(self):
        candidates = [
            match("valid", "Madrid"),
            match("finished", "Madrid", status="finished"),
            match("doubles", "Madrid", doubles=True),
            match("too-late", "Madrid", days=8),
        ]

        recommendations = recommend_tournament_matches(
            candidates, [], [], now=NOW
        )

        self.assertEqual([item.match.provider_id for item in recommendations], ["valid"])

    def test_popularity_normalizes_case_and_whitespace(self):
        counts = tournament_popularity([
            ("atp", "US Open"),
            ("ATP", "  us   open "),
        ])

        self.assertEqual(counts[("ATP", "us open")], 2)


if __name__ == "__main__":
    unittest.main()
