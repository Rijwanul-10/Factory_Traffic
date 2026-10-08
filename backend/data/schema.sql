-- Factory Traffic Management System Schema
-- SQLite schema for state persistence, recovery, and audit logs.

PRAGMA foreign_keys = ON;

-- 1. Junctions configuration
CREATE TABLE IF NOT EXISTS junctions (
    junction_id TEXT PRIMARY KEY,
    name TEXT,
    directions TEXT NOT NULL,          -- JSON array e.g. ["NORTH", "SOUTH", "EAST", "WEST"]
    phases TEXT NOT NULL,              -- JSON array e.g. ["NORTH_SOUTH", "EAST_WEST"]
    green_duration REAL NOT NULL,      -- nominal seconds
    min_green REAL NOT NULL,
    max_green REAL NOT NULL,
    yellow_duration REAL NOT NULL,
    all_red_duration REAL NOT NULL,
    time_scale REAL NOT NULL,
    manual_hold REAL NOT NULL,
    emergency_stale_timeout REAL NOT NULL,
    max_wait REAL NOT NULL,
    grace_empty REAL NOT NULL,
    ack_timeout REAL NOT NULL,
    ack_retries INTEGER NOT NULL,
    created_at REAL NOT NULL
);

-- 2. Junction State snapshot
CREATE TABLE IF NOT EXISTS junction_state (
    junction_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,                -- AUTOMATIC, MANUAL, EMERGENCY, FAILURE
    phase TEXT NOT NULL,               -- NORTH_SOUTH, EAST_WEST
    desired_signals TEXT NOT NULL,     -- JSON object e.g. {"NORTH":"GREEN", ...}
    actual_signals TEXT NOT NULL,      -- JSON object
    controller_status TEXT NOT NULL,   -- ONLINE, OFFLINE, DEGRADED
    in_transition INTEGER NOT NULL,    -- 0 or 1
    current_step TEXT,                 -- YELLOW, ALL_RED, GREEN or NULL
    green_start_time REAL NOT NULL,
    step_start_time REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

-- 3. Waiting Vehicles (Derived Queue)
CREATE TABLE IF NOT EXISTS waiting_vehicles (
    vehicle_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    direction TEXT NOT NULL,           -- NORTH, SOUTH, EAST, WEST
    vehicle_type TEXT NOT NULL,        -- FORKLIFT, TRUCK, EMPLOYEE_VEHICLE, EMERGENCY
    arrived_at REAL NOT NULL,          -- server unix timestamp
    sequence_no INTEGER NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_waiting_vehicles_junction_dir 
ON waiting_vehicles(junction_id, direction);

-- 4. Processed Sensor Events (Idempotency)
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    processed_at REAL NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_processed_events_junction 
ON processed_events(junction_id);

-- 5. Commands (Pending / Acked / Timeout)
CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    desired_signals TEXT NOT NULL,     -- JSON object
    status TEXT NOT NULL,              -- PENDING, ACKED, TIMEOUT, RETRIED, SUPERSEDED
    retries INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 2,
    ack_timeout REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_commands_junction_status 
ON commands(junction_id, status);

-- 6. Emergency Queue
CREATE TABLE IF NOT EXISTS emergency_queue (
    vehicle_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    phase TEXT NOT NULL,
    arrived_at REAL NOT NULL,
    stale_timeout REAL NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_emergency_queue_junction 
ON emergency_queue(junction_id, arrived_at);

-- 7. Manual Queue
CREATE TABLE IF NOT EXISTS manual_queue (
    command_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,              -- QUEUED, ACTIVE, COMPLETED, CANCELLED, REJECTED
    created_at REAL NOT NULL,
    ttl REAL NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_manual_queue_junction 
ON manual_queue(junction_id, created_at);

-- 8. Device Status (Controllers & Sensors)
CREATE TABLE IF NOT EXISTS device_status (
    device_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    device_type TEXT NOT NULL,         -- SIGNAL_CONTROLLER, SENSOR
    direction TEXT,                    -- NORTH, SOUTH, EAST, WEST or NULL
    status TEXT NOT NULL,              -- ONLINE, OFFLINE, DEGRADED
    updated_at REAL NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_status_junction 
ON device_status(junction_id);

-- 9. Audit Log
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    junction_id TEXT NOT NULL,
    direction TEXT,
    previous_state TEXT,
    new_state TEXT,
    command_id TEXT,
    reason TEXT,
    vehicle_id TEXT,
    event_id TEXT,
    timestamp REAL NOT NULL,
    FOREIGN KEY (junction_id) REFERENCES junctions(junction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_log_junction_time 
ON audit_log(junction_id, timestamp DESC);
