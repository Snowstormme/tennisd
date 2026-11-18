"""Capture a bounded, read-only PostgreSQL plan for the public player page."""

import json
import os
import re
from pathlib import Path

import psycopg


QUERY = r'''
WITH appearances AS (
    SELECT sides.player_id
    FROM "match" AS m
    CROSS JOIN LATERAL (
        SELECT m.winner_id AS player_id
        UNION ALL
        SELECT m.loser_id AS player_id
    ) AS sides
    WHERE m.winner_id <> m.loser_id
      AND m.status = 'finished'
      AND m.round <> 'R128'
      AND EXISTS (
          SELECT 1 FROM player AS winner
          WHERE winner.id = m.winner_id AND winner.tour = m.tour
      )
      AND EXISTS (
          SELECT 1 FROM player AS loser
          WHERE loser.id = m.loser_id AND loser.tour = m.tour
      )
), match_counts AS (
    SELECT player_id, count(*) AS match_count
    FROM appearances
    GROUP BY player_id
), follower_counts AS (
    SELECT player_id, count(*) AS follower_count
    FROM followed_player
    GROUP BY player_id
)
SELECT p.id,
       coalesce(match_counts.match_count, 0) AS match_count,
       coalesce(follower_counts.follower_count, 0) AS follower_count
FROM player AS p
LEFT JOIN match_counts ON match_counts.player_id = p.id
LEFT JOIN follower_counts ON follower_counts.player_id = p.id
WHERE lower(p.name) NOT IN ('unknown player', 'unknown', 'tbd', 'tba')
ORDER BY coalesce(follower_counts.follower_count, 0) DESC,
         coalesce(match_counts.match_count, 0) DESC,
         p.wikidata_id IS NULL,
         p.name,
         p.id
LIMIT 30 OFFSET 0
'''


def main():
    database_url = os.environ["DATABASE_URL"]
    database_url = re.sub(r"^postgresql\+[^:]+://", "postgresql://", database_url)
    plan_path = Path(os.environ.get("PLAN_OUTPUT", "/tmp/player-directory-plan.json"))
    with psycopg.connect(database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '15000ms'")
        connection.execute("SET LOCAL lock_timeout = '2000ms'")
        result = connection.execute(
            "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) " + QUERY
        ).fetchone()[0]
    if isinstance(result, str):
        result = json.loads(result)
    plan_path.write_text(json.dumps({"schema_version": 1, "plan": result}, indent=2) + "\n")
    print("Read-only player directory query plan captured; details are in the workflow artifact.")


if __name__ == "__main__":
    main()
