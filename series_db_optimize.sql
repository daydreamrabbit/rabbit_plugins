-- series.full.sqlite slimming script
--
-- Keeps the fields required for:
--   1. title/creator search
--   2. MangaUpdates recommendations
--   3. normalized related-series links
--
-- Back up the database before running this script.
-- Run VACUUM outside a transaction, as done below.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

-- Keep only active rows. The explicit NULL check is intentional: in SQL,
-- NULL <> 'active' is not true and would otherwise leave NULL-state rows.
DELETE FROM Series
WHERE state IS NULL OR state <> 'active';

-- Keep only MangaBaka links. Malformed/non-JSON values are left untouched so
-- a single bad row does not abort the entire migration.
UPDATE Series
SET links = (
    SELECT json_group_array(value)
    FROM json_each(Series.links)
    WHERE value LIKE 'https://mangabaka.org/%'
)
WHERE json_valid(links);

-- Keep only the title languages used by the application.
UPDATE Series
SET titles = (
    SELECT json_group_array(value)
    FROM json_each(Series.titles)
    WHERE json_extract(value, '$.language') IN ('en', 'ja', 'ko')
)
WHERE json_valid(titles);

-- Remove objects that depend on the old Series table before replacing it.
DROP TRIGGER IF EXISTS series_fts_after_insert;
DROP TRIGGER IF EXISTS series_fts_after_delete;
DROP TRIGGER IF EXISTS series_fts_after_update;
DROP VIEW IF EXISTS series_recommendation_items;
DROP VIEW IF EXISTS series_relationship_items;
DROP TABLE IF EXISTS series_title_fts;
DROP TABLE IF EXISTS series_creator_fts;
DROP TABLE IF EXISTS Series_new;

CREATE TABLE Series_new (
    id INTEGER PRIMARY KEY,
    title TEXT,
    native_title TEXT,
    secondary_titles_ko TEXT,
    source_manga_updates_id TEXT,
    type TEXT,
    links TEXT,
    status TEXT,
    titles TEXT,
    artists TEXT,
    authors TEXT,
    final_volume TEXT,
    content_rating TEXT,
    cover_raw_url TEXT,

    -- Needed to resolve recommendation JSON's external series_id back to a
    -- local Series.id. This value is not guaranteed to be unique.
    source_manga_updates_response_series_id INTEGER,

    -- MangaUpdates recommendation arrays.
    source_manga_updates_response_recommendations TEXT,

    -- Unified detailed relationship data.
    relationships_v2 TEXT,

    -- Convenient, normalized relationship arrays containing local Series.id
    -- values. These are the preferred fields for application display.
    relationships_other TEXT,
    relationships_sequel TEXT,
    relationships_spin_off TEXT,
    relationships_main_story TEXT,
    relationships_alternative TEXT,
    relationships_adaptation TEXT,
    relationships_prequel TEXT,
    relationships_side_story TEXT
);

INSERT INTO Series_new (
    id,
    title,
    native_title,
    secondary_titles_ko,
    source_manga_updates_id,
    type,
    links,
    status,
    titles,
    artists,
    authors,
    final_volume,
    content_rating,
    cover_raw_url,
    source_manga_updates_response_series_id,
    source_manga_updates_response_recommendations,
    relationships_v2,
    relationships_other,
    relationships_sequel,
    relationships_spin_off,
    relationships_main_story,
    relationships_alternative,
    relationships_adaptation,
    relationships_prequel,
    relationships_side_story
)
SELECT
    id,
    title,
    native_title,
    secondary_titles_ko,
    source_manga_updates_id,
    type,
    links,
    status,
    titles,
    artists,
    authors,
    final_volume,
    content_rating,
    cover_raw_url,
    source_manga_updates_response_series_id,
    source_manga_updates_response_recommendations,
    relationships_v2,
    relationships_other,
    relationships_sequel,
    relationships_spin_off,
    relationships_main_story,
    relationships_alternative,
    relationships_adaptation,
    relationships_prequel,
    relationships_side_story
FROM Series;

DROP TABLE Series;
ALTER TABLE Series_new RENAME TO Series;

-- Recommendation target matching uses this index. It is deliberately not
-- UNIQUE because the source database contains duplicate MangaUpdates IDs.
CREATE INDEX idx_series_manga_updates_id
ON Series(source_manga_updates_response_series_id);

CREATE INDEX idx_series_type_status
ON Series(type, status);

COMMIT;

-- Reclaim pages left by the dropped wide table.
VACUUM;


BEGIN IMMEDIATE;

CREATE VIRTUAL TABLE series_title_fts USING fts5(
    title,
    titles,
    content = 'Series',
    content_rowid = 'id',
    tokenize = 'trigram'
);

CREATE VIRTUAL TABLE series_creator_fts USING fts5(
    authors,
    artists,
    content = 'Series',
    content_rowid = 'id',
    tokenize = 'trigram'
);

INSERT INTO series_title_fts(series_title_fts) VALUES ('rebuild');
INSERT INTO series_creator_fts(series_creator_fts) VALUES ('rebuild');

CREATE TRIGGER series_fts_after_insert
AFTER INSERT ON Series
BEGIN
    INSERT INTO series_title_fts(rowid, title, titles)
    VALUES (new.id, new.title, new.titles);

    INSERT INTO series_creator_fts(rowid, authors, artists)
    VALUES (new.id, new.authors, new.artists);
END;

CREATE TRIGGER series_fts_after_delete
AFTER DELETE ON Series
BEGIN
    INSERT INTO series_title_fts(series_title_fts, rowid, title, titles)
    VALUES ('delete', old.id, old.title, old.titles);

    INSERT INTO series_creator_fts(series_creator_fts, rowid, authors, artists)
    VALUES ('delete', old.id, old.authors, old.artists);
END;

CREATE TRIGGER series_fts_after_update
AFTER UPDATE OF id, title, titles, authors, artists ON Series
BEGIN
    INSERT INTO series_title_fts(series_title_fts, rowid, title, titles)
    VALUES ('delete', old.id, old.title, old.titles);

    INSERT INTO series_creator_fts(series_creator_fts, rowid, authors, artists)
    VALUES ('delete', old.id, old.authors, old.artists);

    INSERT INTO series_title_fts(rowid, title, titles)
    VALUES (new.id, new.title, new.titles);

    INSERT INTO series_creator_fts(rowid, authors, artists)
    VALUES (new.id, new.authors, new.artists);
END;

-- Flatten the direct recommendation array at query time without duplicating
-- its data on disk. recommended_series_id resolves to the smallest matching local
-- ID because MangaUpdates IDs are not unique in the source database.
CREATE VIEW series_recommendation_items AS
SELECT
    s.id AS series_id,
    'recommendation' AS recommendation_type,
    CAST(j.key AS INTEGER) + 1 AS position,
    CAST(json_extract(j.value, '$.weight') AS INTEGER) AS weight,
    CAST(json_extract(j.value, '$.series_id') AS INTEGER)
        AS source_manga_updates_series_id,
    (
        SELECT MIN(target.id)
        FROM Series AS target
        WHERE target.source_manga_updates_response_series_id =
              CAST(json_extract(j.value, '$.series_id') AS INTEGER)
    ) AS recommended_series_id,
    json_extract(j.value, '$.series_name') AS recommended_title,
    json_extract(j.value, '$.series_url') AS recommended_url
FROM Series AS s
JOIN json_each(
    CASE
        WHEN json_valid(s.source_manga_updates_response_recommendations)
        THEN s.source_manga_updates_response_recommendations
        ELSE '[]'
    END
) AS j;

-- Flatten the eight application-facing relationship arrays. related_series_id
-- is already a local Series.id, so no external-ID conversion is required.
CREATE VIEW series_relationship_items AS
SELECT s.id AS series_id, 'sequel' AS relation_type,
       CAST(j.value AS INTEGER) AS related_series_id,
       CAST(j.key AS INTEGER) + 1 AS position
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_sequel)
                    THEN s.relationships_sequel ELSE '[]' END) AS j
UNION ALL
SELECT s.id, 'prequel', CAST(j.value AS INTEGER), CAST(j.key AS INTEGER) + 1
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_prequel)
                    THEN s.relationships_prequel ELSE '[]' END) AS j
UNION ALL
SELECT s.id, 'spin_off', CAST(j.value AS INTEGER), CAST(j.key AS INTEGER) + 1
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_spin_off)
                    THEN s.relationships_spin_off ELSE '[]' END) AS j
UNION ALL
SELECT s.id, 'main_story', CAST(j.value AS INTEGER), CAST(j.key AS INTEGER) + 1
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_main_story)
                    THEN s.relationships_main_story ELSE '[]' END) AS j
UNION ALL
SELECT s.id, 'alternative', CAST(j.value AS INTEGER), CAST(j.key AS INTEGER) + 1
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_alternative)
                    THEN s.relationships_alternative ELSE '[]' END) AS j
UNION ALL
SELECT s.id, 'adaptation', CAST(j.value AS INTEGER), CAST(j.key AS INTEGER) + 1
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_adaptation)
                    THEN s.relationships_adaptation ELSE '[]' END) AS j
UNION ALL
SELECT s.id, 'side_story', CAST(j.value AS INTEGER), CAST(j.key AS INTEGER) + 1
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_side_story)
                    THEN s.relationships_side_story ELSE '[]' END) AS j
UNION ALL
SELECT s.id, 'other', CAST(j.value AS INTEGER), CAST(j.key AS INTEGER) + 1
FROM Series AS s
JOIN json_each(CASE WHEN json_valid(s.relationships_other)
                    THEN s.relationships_other ELSE '[]' END) AS j;

COMMIT;
