-- Replay protection for host-key signed VM guest metadata requests: the highest
-- request timestamp (Unix milliseconds) accepted per allocation. A request is
-- accepted only when its timestamp is strictly greater, advanced by one atomic
-- conditional upsert (PostgresGuestRequestWatermarks.try_advance).
CREATE TABLE IF NOT EXISTS compute_guest_request_watermarks (
    allocation_id UUID PRIMARY KEY REFERENCES compute_leases (id) ON DELETE CASCADE,
    last_request_at BIGINT NOT NULL
);
