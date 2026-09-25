DROP INDEX IF EXISTS idx_niuu_instances_node_kind;
DROP INDEX IF EXISTS idx_niuu_instances_node_id;
ALTER TABLE niuu_instances DROP COLUMN IF EXISTS node_id;

DROP INDEX IF EXISTS idx_niuu_nodes_tenant_name;
DROP INDEX IF EXISTS idx_niuu_nodes_tenant;
DROP INDEX IF EXISTS idx_niuu_nodes_public_key;
DROP TABLE IF EXISTS niuu_nodes;

DROP INDEX IF EXISTS idx_niuu_pairing_codes_expires;
DROP TABLE IF EXISTS niuu_pairing_codes;
