-- DBeaver 제어 명령을 sqlite3.executescript용 SQL로 치환한 사용자 제공 스크립트입니다.
-- DBeaver 전용: 변수 사용 활성화/제어 접두사 @ 상태에서 전체를 Alt+X로 실행합니다.
-- 기존 Series DB에서 불필요한 컬럼만 제거하는 스크립트
-- 행을 삭제하거나 제목/링크/관계/추천 값을 재가공하지 않습니다.
-- state는 결과에 남기지 않으며, 이미 state가 없는 DB에서도 재실행 가능합니다.
-- 기존 작업을 종료하고 백업한 뒤 자동 커밋 상태에서 전체를 한 번 실행합니다.
-- 외부에서 스크립트 전체를 트랜잭션으로 감싸지 않습니다(VACUUM 실행 필요).
-- 28개 컬럼을 원문 그대로 보존하고 검색 인덱스·트리거·조회 뷰를 다시 만듭니다.
-- Series_compact_new가 이미 있으면 중단하며 기존 임시 테이블을 삭제하지 않습니다.

SAVEPOINT series_compaction;

CREATE TABLE Series_compact_new (
    "id" INTEGER PRIMARY KEY,
    "title" TEXT,
    "type" TEXT,
    "links" TEXT,
    "status" TEXT,
    "titles" TEXT,
    "artists" TEXT,
    "authors" TEXT,
    "final_volume" TEXT,
    "content_rating" TEXT,
    "cover_raw_url" TEXT,
    "native_title" TEXT,
    "romanized_title" TEXT,
    "secondary_titles_ko" TEXT,
    "secondary_titles_en" TEXT,
    "secondary_titles_ja" TEXT,
    "source_manga_updates_response_series_id" INTEGER,
    "source_manga_updates_response_recommendations" TEXT,
    "source_manga_updates_response_category_recommendations" TEXT,
    "relationships_v2" TEXT,
    "relationships_other" TEXT,
    "relationships_sequel" TEXT,
    "relationships_spin_off" TEXT,
    "relationships_main_story" TEXT,
    "relationships_alternative" TEXT,
    "relationships_adaptation" TEXT,
    "relationships_prequel" TEXT,
    "relationships_side_story" TEXT
);

INSERT INTO Series_compact_new ("id", "title", "type", "links", "status", "titles", "artists", "authors", "final_volume", "content_rating", "cover_raw_url", "native_title", "romanized_title", "secondary_titles_ko", "secondary_titles_en", "secondary_titles_ja", "source_manga_updates_response_series_id", "source_manga_updates_response_recommendations", "source_manga_updates_response_category_recommendations", "relationships_v2", "relationships_other", "relationships_sequel", "relationships_spin_off", "relationships_main_story", "relationships_alternative", "relationships_adaptation", "relationships_prequel", "relationships_side_story")
SELECT "id", "title", "type", "links", "status", "titles", "artists", "authors", "final_volume", "content_rating", "cover_raw_url", "native_title", "romanized_title", "secondary_titles_ko", "secondary_titles_en", "secondary_titles_ja", "source_manga_updates_response_series_id", "source_manga_updates_response_recommendations", "source_manga_updates_response_category_recommendations", "relationships_v2", "relationships_other", "relationships_sequel", "relationships_spin_off", "relationships_main_story", "relationships_alternative", "relationships_adaptation", "relationships_prequel", "relationships_side_story" FROM Series;

-- 복사가 끝난 뒤에만 원본 테이블과 검색 인덱스를 교체합니다.
DROP TRIGGER IF EXISTS series_fts_after_insert;
DROP TRIGGER IF EXISTS series_fts_after_delete;
DROP TRIGGER IF EXISTS series_fts_after_update;
DROP VIEW IF EXISTS series_relationship_items;
DROP VIEW IF EXISTS series_recommendation_items;
DROP TABLE IF EXISTS series_title_fts;
DROP TABLE IF EXISTS series_creator_fts;
DROP TABLE Series;
ALTER TABLE Series_compact_new RENAME TO Series;

CREATE INDEX idx_series_type_status ON Series(type,status);

CREATE INDEX idx_series_manga_updates_id ON Series("source_manga_updates_response_series_id");

CREATE VIRTUAL TABLE series_title_fts USING fts5(title, titles,content='Series',content_rowid='id',tokenize='trigram');

INSERT INTO series_title_fts(series_title_fts) VALUES('rebuild');

CREATE VIRTUAL TABLE series_creator_fts USING fts5(authors, artists,content='Series',content_rowid='id',tokenize='trigram');

INSERT INTO series_creator_fts(series_creator_fts) VALUES('rebuild');

CREATE TRIGGER series_fts_after_insert AFTER INSERT ON Series BEGIN
INSERT INTO series_title_fts(rowid,title,titles) VALUES(new.id,new.title,new.titles);
INSERT INTO series_creator_fts(rowid,authors,artists) VALUES(new.id,new.authors,new.artists);
END;


CREATE TRIGGER series_fts_after_delete AFTER DELETE ON Series BEGIN
INSERT INTO series_title_fts(series_title_fts,rowid,title,titles) VALUES('delete',old.id,old.title,old.titles);
INSERT INTO series_creator_fts(series_creator_fts,rowid,authors,artists) VALUES('delete',old.id,old.authors,old.artists);
END;


CREATE TRIGGER series_fts_after_update AFTER UPDATE OF id,title,titles,authors,artists ON Series BEGIN
INSERT INTO series_title_fts(series_title_fts,rowid,title,titles) VALUES('delete',old.id,old.title,old.titles);
INSERT INTO series_creator_fts(series_creator_fts,rowid,authors,artists) VALUES('delete',old.id,old.authors,old.artists);
INSERT INTO series_title_fts(rowid,title,titles) VALUES(new.id,new.title,new.titles);
INSERT INTO series_creator_fts(rowid,authors,artists) VALUES(new.id,new.authors,new.artists);
END;


CREATE VIEW series_relationship_items AS
SELECT s.id AS series_id,json_extract(j.value,'$.relation_type') AS relation_type,json_extract(j.value,'$.to_series_id') AS related_series_id,CAST(j.key AS INTEGER)+1 AS position,j.value AS relationship_json FROM Series s JOIN json_each(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END) j WHERE j.type='object'
UNION ALL
SELECT s.id,'other',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_other") THEN CASE WHEN json_type(s."relationships_other")='array' THEN s."relationships_other" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0
UNION ALL
SELECT s.id,'sequel',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_sequel") THEN CASE WHEN json_type(s."relationships_sequel")='array' THEN s."relationships_sequel" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0
UNION ALL
SELECT s.id,'spin_off',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_spin_off") THEN CASE WHEN json_type(s."relationships_spin_off")='array' THEN s."relationships_spin_off" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0
UNION ALL
SELECT s.id,'main_story',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_main_story") THEN CASE WHEN json_type(s."relationships_main_story")='array' THEN s."relationships_main_story" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0
UNION ALL
SELECT s.id,'alternative',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_alternative") THEN CASE WHEN json_type(s."relationships_alternative")='array' THEN s."relationships_alternative" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0
UNION ALL
SELECT s.id,'adaptation',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_adaptation") THEN CASE WHEN json_type(s."relationships_adaptation")='array' THEN s."relationships_adaptation" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0
UNION ALL
SELECT s.id,'prequel',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_prequel") THEN CASE WHEN json_type(s."relationships_prequel")='array' THEN s."relationships_prequel" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0
UNION ALL
SELECT s.id,'side_story',CAST(j.value AS INTEGER),CAST(j.key AS INTEGER)+1,NULL FROM Series s JOIN json_each(CASE WHEN json_valid(s."relationships_side_story") THEN CASE WHEN json_type(s."relationships_side_story")='array' THEN s."relationships_side_story" ELSE '[]' END ELSE '[]' END) j WHERE j.type='integer' AND json_array_length(CASE WHEN json_valid(s.relationships_v2) THEN CASE WHEN json_type(s.relationships_v2)='array' THEN s.relationships_v2 ELSE '[]' END ELSE '[]' END)=0;

CREATE VIEW series_recommendation_items AS SELECT s.id AS series_id,CAST(j.key AS INTEGER)+1 AS position,json_extract(j.value,'$.weight') AS weight,json_extract(j.value,'$.series_id') AS source_manga_updates_series_id,(SELECT CASE WHEN COUNT(*)=1 THEN MIN(t.id) END FROM Series t WHERE t."source_manga_updates_response_series_id"=json_extract(j.value,'$.series_id')) AS recommended_series_id,(SELECT COUNT(*) FROM Series t WHERE t."source_manga_updates_response_series_id"=json_extract(j.value,'$.series_id')) AS target_match_count,json_extract(j.value,'$.series_name') AS recommended_title,json_extract(j.value,'$.series_url') AS recommended_url FROM Series s JOIN json_each(CASE WHEN json_valid(s."source_manga_updates_response_recommendations") THEN CASE WHEN json_type(s."source_manga_updates_response_recommendations")='array' THEN s."source_manga_updates_response_recommendations" ELSE '[]' END ELSE '[]' END) j WHERE j.type='object';

RELEASE SAVEPOINT series_compaction;

VACUUM main;

ANALYZE main;

PRAGMA main.optimize;

-- 외부 콘텐츠 FTS는 COUNT만으로 인덱스 상태를 확인할 수 없습니다.
-- rank=1 검사는 실제 Series 내용과 인덱스 내용까지 대조합니다.
INSERT INTO series_title_fts(series_title_fts,rank) VALUES('integrity-check',1);
INSERT INTO series_creator_fts(series_creator_fts,rank) VALUES('integrity-check',1);
PRAGMA main.integrity_check;

SELECT
 (SELECT COUNT(*) FROM Series) AS series_rows,
 (SELECT COUNT(*) FROM series_title_fts) AS title_fts_rows,
 (SELECT COUNT(*) FROM series_creator_fts) AS creator_fts_rows,
 (SELECT COUNT(*) FROM series_relationship_items) AS relationship_items,
 (SELECT COUNT(*) FROM series_recommendation_items) AS recommendation_items;