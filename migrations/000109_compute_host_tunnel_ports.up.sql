-- A reverse-SSH host's tunnel port is on the connect endpoint's loopback, which every
-- reverse-SSH host of the Forge shares, so no two hosts may hold the same one. The
-- connector picks a free port; this index refuses the second of two joins that raced.
CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_hosts_tunnel_port
    ON compute_hosts ((data->'connection'->>'tunnel_port'))
    WHERE data->'connection' ? 'tunnel_port';
