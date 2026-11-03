ALTER TABLE sessions DROP COLUMN IF EXISTS description_updated_at;
ALTER TABLE sessions DROP COLUMN IF EXISTS short_description_updated_at;
ALTER TABLE sessions DROP COLUMN IF EXISTS description;
ALTER TABLE sessions DROP COLUMN IF EXISTS short_description;
