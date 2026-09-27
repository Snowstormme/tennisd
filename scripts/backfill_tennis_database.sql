-- Set-based, idempotent backfill for the historical match archive.
-- This intentionally avoids ORM row-by-row inserts over the Neon network.

CREATE OR REPLACE FUNCTION tennisd_slug(value TEXT) RETURNS TEXT
LANGUAGE SQL IMMUTABLE STRICT AS $$
  SELECT trim(BOTH '-' FROM regexp_replace(
    translate(lower(value),
      'àáâãäåæçèéêëìíîïñòóôõöøœùúûüýÿšžčćđ',
      'aaaaaaaceeeeiiiinooooooouuuuyyszccd'),
    '[^a-z0-9]+', '-', 'g'))
$$;

INSERT INTO tournament (tour, slug, name)
SELECT DISTINCT ON (tour, tennisd_slug(tournament))
       tour, tennisd_slug(tournament), tournament
FROM "match"
WHERE tournament IS NOT NULL AND tournament <> ''
ORDER BY tour, tennisd_slug(tournament), tournament
ON CONFLICT (tour, slug) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO tournament_edition (tournament_id, season, level, surface, starts_on, status)
SELECT t.id, m.season, max(m.level), max(m.surface), min(m.week_start), 'finished'
FROM (
  SELECT tour, tournament, extract(year FROM week_start)::integer AS season,
         level, surface, week_start
  FROM "match"
  WHERE week_start IS NOT NULL
) m
JOIN tournament t ON t.tour = m.tour AND t.slug = tennisd_slug(m.tournament)
GROUP BY t.id, m.season
ON CONFLICT (tournament_id, season) DO UPDATE SET
  level = COALESCE(tournament_edition.level, EXCLUDED.level),
  surface = COALESCE(tournament_edition.surface, EXCLUDED.surface),
  starts_on = LEAST(tournament_edition.starts_on, EXCLUDED.starts_on);

UPDATE "match" m
SET edition_id = e.id,
    status = COALESCE(NULLIF(m.status, ''), 'finished')
FROM tournament t
JOIN tournament_edition e ON e.tournament_id = t.id
WHERE t.tour = m.tour
  AND t.slug = tennisd_slug(m.tournament)
  AND e.season = extract(year FROM m.week_start)::integer
  AND m.edition_id IS DISTINCT FROM e.id;

INSERT INTO match_participant (match_id, player_id, side, is_winner)
SELECT id, winner_id, 1, TRUE FROM "match" WHERE winner_id IS NOT NULL
UNION ALL
SELECT id, loser_id, 2, FALSE FROM "match" WHERE loser_id IS NOT NULL
ON CONFLICT (match_id, side) DO NOTHING;

INSERT INTO match_statistic (
  match_id, player_id, aces, double_faults, serve_points,
  first_serves_in, first_serve_points_won, break_points_saved,
  break_points_faced, source
)
SELECT id, winner_id, w_ace, w_df, w_svpt, w_first_in, w_first_won,
       w_bp_saved, w_bp_faced, 'sackmann'
FROM "match"
WHERE winner_id IS NOT NULL AND COALESCE(w_ace, w_df, w_svpt, w_first_in, w_first_won, w_bp_saved, w_bp_faced) IS NOT NULL
UNION ALL
SELECT id, loser_id, l_ace, l_df, l_svpt, l_first_in, l_first_won,
       l_bp_saved, l_bp_faced, 'sackmann'
FROM "match"
WHERE loser_id IS NOT NULL AND COALESCE(l_ace, l_df, l_svpt, l_first_in, l_first_won, l_bp_saved, l_bp_faced) IS NOT NULL
ON CONFLICT (match_id, player_id) DO NOTHING;

INSERT INTO ranking_snapshot (player_id, ranked_on, ranking_type, rank, source)
SELECT DISTINCT ON (player_id, ranked_on)
       player_id, ranked_on, 'singles', rank, 'sackmann-match'
FROM (
  SELECT winner_id AS player_id, week_start AS ranked_on, winner_rank AS rank FROM "match"
  UNION ALL
  SELECT loser_id, week_start, loser_rank FROM "match"
) rankings
WHERE player_id IS NOT NULL AND ranked_on IS NOT NULL AND rank IS NOT NULL
ORDER BY player_id, ranked_on, rank
ON CONFLICT (player_id, ranked_on, ranking_type) DO NOTHING;

INSERT INTO player_external_id (player_id, provider, external_id)
SELECT id, 'sackmann', split_part(id, '-', 2)
FROM player
WHERE id LIKE '%-%'
ON CONFLICT (provider, external_id) DO NOTHING;

DROP FUNCTION tennisd_slug(TEXT);
