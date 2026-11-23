"""PostgreSQL query for the rights calendar.

The caller resolves delegation chains first and passes only the *effective*
grants (scope clipped to every upstream grant).  This query does the window
work: merge touching/overlapping grants, expose the merged intervals together
with their source grants, and derive gaps, delay candidates and decisions.
"""

from sqlalchemy import text


ANALYZE_SCREENINGS = text("""
WITH screenings AS (
    SELECT * FROM jsonb_to_recordset(CAST(:screenings AS jsonb)) AS s(
        screening_id text, film_id text, title text, territory text, medium text,
        exhibitor text, starts_at timestamptz, ends_at timestamptz
    )
), rights AS (
    SELECT * FROM jsonb_to_recordset(CAST(:rights AS jsonb)) AS r(
        right_id text, film_id text, territory text, medium text, grantee text,
        starts_at timestamptz, ends_at timestamptz
    )
), matched AS (
    -- A screening is only covered by a grant made to the exhibitor that shows
    -- the film (chains ending at an agent do not authorize the cinema itself).
    -- An empty exhibitor or grantee keeps legacy, chain-less input working.
    SELECT s.screening_id, r.right_id, r.starts_at, r.ends_at
    FROM screenings s
    JOIN rights r ON r.film_id = s.film_id
                 AND r.territory IN (s.territory, '*')
                 AND r.medium IN (s.medium, '*')
                 AND (s.exhibitor = '' OR r.grantee = '' OR r.grantee = s.exhibitor)
                 AND r.ends_at > s.starts_at
), prior_coverage AS (
    -- An earlier, longer grant can still reach past the immediately preceding grant.
    SELECT screening_id, right_id, starts_at, ends_at,
           MAX(ends_at) OVER (
               PARTITION BY screening_id
               ORDER BY starts_at, ends_at, right_id
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
           ) AS prior_end
    FROM matched
), island_members AS (
    SELECT screening_id, right_id, starts_at, ends_at,
           SUM(CASE WHEN prior_end IS NULL OR starts_at > prior_end THEN 1 ELSE 0 END)
               OVER (PARTITION BY screening_id ORDER BY starts_at, ends_at, right_id
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS island_id
    FROM prior_coverage
), rights_islands AS (
    SELECT screening_id, island_id, MIN(starts_at) AS starts_at, MAX(ends_at) AS ends_at
    FROM island_members
    GROUP BY screening_id, island_id
), coverage AS (
    -- One row per island clipped to the screening, with every grant that
    -- actually contributes to that interval.
    SELECT m.screening_id,
           GREATEST(s.starts_at, i.starts_at) AS starts_at,
           LEAST(s.ends_at, i.ends_at) AS ends_at,
           array_agg(m.right_id ORDER BY m.starts_at, m.right_id) AS right_ids
    FROM island_members m
    JOIN rights_islands i ON i.screening_id = m.screening_id
                         AND i.island_id = m.island_id
    JOIN screenings s ON s.screening_id = m.screening_id
    WHERE i.starts_at < s.ends_at AND i.ends_at > s.starts_at
      AND m.starts_at < s.ends_at AND m.ends_at > s.starts_at
    GROUP BY m.screening_id, i.island_id, i.starts_at, i.ends_at, s.starts_at, s.ends_at
), coverage_boundaries AS (
    SELECT screening_id, starts_at, ends_at FROM coverage
    UNION ALL
    SELECT screening_id, starts_at, starts_at FROM screenings
    UNION ALL
    SELECT screening_id, ends_at, ends_at FROM screenings
), gap_candidates AS (
    -- Zero-width boundaries let the same LEAD find head, interior and tail gaps.
    SELECT screening_id, ends_at AS gap_start,
           LEAD(starts_at) OVER (
               PARTITION BY screening_id ORDER BY starts_at, ends_at
           ) AS gap_end
    FROM coverage_boundaries
), gaps AS (
    SELECT screening_id, gap_start, gap_end,
           CAST(EXTRACT(EPOCH FROM (gap_end - gap_start)) / 60 AS bigint) AS gap_minutes
    FROM gap_candidates
    WHERE gap_start < gap_end
), delay_candidates AS (
    SELECT s.screening_id, s.starts_at AS original_start, s.ends_at - s.starts_at AS duration,
           GREATEST(s.starts_at, i.starts_at) AS proposed_start, i.ends_at AS coverage_end
    FROM screenings s
    JOIN rights_islands i ON i.screening_id = s.screening_id
), proposals AS (
    SELECT screening_id, MIN(proposed_start) AS proposed_start
    FROM delay_candidates
    WHERE coverage_end - proposed_start >= duration
      AND EXTRACT(EPOCH FROM (proposed_start - original_start))
          <= CAST(:max_delay_minutes AS numeric) * 60
    GROUP BY screening_id
)
SELECT s.screening_id, s.film_id, s.title, s.territory, s.medium, s.exhibitor,
       s.starts_at, s.ends_at,
       CAST(EXTRACT(EPOCH FROM (s.ends_at - s.starts_at)) / 60 AS bigint) AS screening_minutes,
       COALESCE((
           SELECT SUM(g.gap_minutes) FROM gaps g WHERE g.screening_id = s.screening_id
       ), 0)::bigint AS unauthorized_minutes,
       COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
                      'starts_at', c.starts_at,
                      'ends_at', c.ends_at,
                      'right_ids', c.right_ids
                  ) ORDER BY c.starts_at, c.ends_at)
           FROM coverage c WHERE c.screening_id = s.screening_id
       ), '[]'::jsonb) AS coverage,
       COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
                      'starts_at', g.gap_start,
                      'ends_at', g.gap_end,
                      'minutes', g.gap_minutes
                  ) ORDER BY g.gap_start)
           FROM gaps g WHERE g.screening_id = s.screening_id
       ), '[]'::jsonb) AS gaps,
       p.proposed_start, p.proposed_start + (s.ends_at - s.starts_at) AS proposed_end
FROM screenings s
LEFT JOIN proposals p ON p.screening_id = s.screening_id
ORDER BY s.starts_at, s.screening_id
""")
