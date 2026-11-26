-- Slip propagation over depends_on edges, evaluated at :as_of. No LLM, no randomness.
--
-- An edge (src -> dst, kind 'depends_on') means "src depends on dst", so delay flows from dst (upstream) to
-- src (downstream).
--  1. own_slip: how many days an unfinished item is past its due date at :as_of.
--  2. From every slipping item, walk downstream. Each hop absorbs slack: the downstream item only needs the
--     upstream one finished by its latest start (its due date minus its estimate). If the upstream item finishes
--     at due + slip, the downstream delay is slip - (latest_start_down - due_up). Unknown dates absorb nothing.
--     Delay that reaches zero stops; done/cancelled items are never delayed.
--  3. path[] prevents revisiting a node (cycles); depth is capped as a second guard. A diamond (D depends on B
--     and C, both depend on A) reaches D twice; the result keeps the worst slip.
-- Output, one row per delayed item: its worst projected slip in days, the slipping root items responsible, and
-- (for roots) how many other items they delay downstream.
WITH RECURSIVE base AS (
    SELECT id,
           due_at,
           coalesce(estimate_days, 0) AS est,
           status NOT IN ('done', 'cancelled') AS active,
           CASE WHEN status NOT IN ('done', 'cancelled') AND due_at < CAST(:as_of AS timestamptz)
                THEN extract(epoch FROM CAST(:as_of AS timestamptz) - due_at) / 86400.0
                ELSE 0 END AS own_slip
    FROM work_items
),
walk (root_id, item_id, slip, depth, path) AS (
    SELECT id, id, own_slip, 0, ARRAY[id] FROM base WHERE own_slip > 0
    UNION ALL
    SELECT w.root_id, e.src_id, hop.slip, w.depth + 1, w.path || e.src_id
    FROM walk w
    JOIN edges e ON e.dst_id = w.item_id AND e.kind = 'depends_on'
    JOIN base up ON up.id = w.item_id
    JOIN base down ON down.id = e.src_id
    CROSS JOIN LATERAL (
        SELECT w.slip - CASE
            WHEN down.due_at IS NULL OR up.due_at IS NULL THEN 0
            ELSE greatest(0, extract(epoch FROM (down.due_at - down.est * interval '1 day') - up.due_at) / 86400.0)
        END AS slip
    ) hop
    WHERE down.active AND hop.slip > 0 AND NOT e.src_id = ANY (w.path) AND w.depth < 50
)
SELECT w.item_id,
       max(w.slip) AS projected_slip_days,
       array_agg(DISTINCT w.root_id) AS root_ids,
       (SELECT count(DISTINCT w2.item_id) - 1 FROM walk w2 WHERE w2.root_id = w.item_id) AS downstream_impact
FROM walk w
GROUP BY w.item_id
