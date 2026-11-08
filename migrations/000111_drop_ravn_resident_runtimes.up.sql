-- The ravn resident engine was removed. A resident_runtimes row whose engine is 'ravn'
-- can no longer be loaded (the engine is not a valid ResidentEngine), which would make
-- every resident listing and reconciliation pass fail for all owners, so those rows go.
--
-- Nothing references resident_runtimes: realm_id (000079) is a foreign key from this
-- table to realms, and no other table has a foreign key to it, so no dependent rows
-- need deleting first. The residents' workloads, volumes and credentials live in their
-- backends and the credential store and are removed there.
DELETE FROM resident_runtimes
WHERE engine = 'ravn';
