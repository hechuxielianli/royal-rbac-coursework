PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sys_user (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    title TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sys_role (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sys_permission (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('MENU','PAGE','BUTTON','ADMIN')),
    description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sys_user_role (
    user_id INTEGER NOT NULL REFERENCES sys_user(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
    PRIMARY KEY(user_id, role_id)
);
CREATE TABLE IF NOT EXISTS sys_role_permission (
    role_id INTEGER NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES sys_permission(id) ON DELETE CASCADE,
    PRIMARY KEY(role_id, permission_id)
);
CREATE TABLE IF NOT EXISTS sys_role_hierarchy (
    senior_id INTEGER NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
    junior_id INTEGER NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
    CHECK(senior_id <> junior_id),
    PRIMARY KEY(senior_id, junior_id)
);
CREATE TABLE IF NOT EXISTS sys_role_mutex (
    role_a_id INTEGER NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
    role_b_id INTEGER NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    CHECK(role_a_id < role_b_id),
    PRIMARY KEY(role_a_id, role_b_id)
);
CREATE TABLE IF NOT EXISTS sys_permission_mutex (
    permission_a_id INTEGER NOT NULL REFERENCES sys_permission(id),
    permission_b_id INTEGER NOT NULL REFERENCES sys_permission(id),
    reason TEXT NOT NULL,
    CHECK(permission_a_id < permission_b_id),
    PRIMARY KEY(permission_a_id, permission_b_id)
);
CREATE TABLE IF NOT EXISTS kingdom_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    swords INTEGER NOT NULL DEFAULT 24 CHECK(swords >= 0),
    cavalry_deployments INTEGER NOT NULL DEFAULT 0 CHECK(cavalry_deployments >= 0),
    gold INTEGER NOT NULL DEFAULT 3200 CHECK(gold >= 0),
    audits INTEGER NOT NULL DEFAULT 0 CHECK(audits >= 0)
);
CREATE TABLE IF NOT EXISTS kingdom_document (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    records_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS business_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    actor_id INTEGER NOT NULL REFERENCES sys_user(id),
    action TEXT NOT NULL,
    amount INTEGER NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_event (
    id TEXT PRIMARY KEY,
    actor_id INTEGER REFERENCES sys_user(id),
    event_type TEXT NOT NULL,
    target TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK(allowed IN (0,1)),
    message TEXT NOT NULL,
    logs_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_actor_time ON audit_event(actor_id, created_at DESC);
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS policy_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    revision INTEGER NOT NULL CHECK(revision>=1)
);
CREATE TABLE IF NOT EXISTS ui_resource (
    code TEXT PRIMARY KEY REFERENCES sys_permission(code),
    kind TEXT NOT NULL CHECK(kind IN ('menus','pages','actions')),
    slug TEXT NOT NULL,
    territory TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    icon TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    eyebrow TEXT NOT NULL DEFAULT '',
    subtitle TEXT NOT NULL DEFAULT '',
    number TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS accounting_baseline (
    id INTEGER PRIMARY KEY CHECK(id=1),
    opening_gold INTEGER NOT NULL CHECK(opening_gold>=0)
);
CREATE TABLE IF NOT EXISTS audit_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    actor_id INTEGER NOT NULL REFERENCES sys_user(id),
    opening_gold INTEGER NOT NULL,
    total_credits INTEGER NOT NULL,
    total_debits INTEGER NOT NULL,
    expected_gold INTEGER NOT NULL,
    actual_gold INTEGER NOT NULL,
    difference INTEGER NOT NULL,
    consistent INTEGER NOT NULL CHECK(consistent IN (0,1)),
    ledger_count INTEGER NOT NULL,
    through_ledger_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_event(created_at DESC);
