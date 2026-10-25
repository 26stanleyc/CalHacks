-- Append-only log tables (spec section 6). Every part writes and reads only through these.
-- Rows are never updated or deleted; corrections are new rows. JSONB columns hold the
-- matching contracts/schemas.py model serialized with model_dump(mode="json").
-- Ryan applies this to Supabase (R1); everyone else treats it as read-only.

create table if not exists runs (
    run_id      text primary key,
    seed        integer not null,
    defense     text not null default 'full',   -- 'full', a baseline name, or an ablation name
    config      jsonb not null default '{}',
    started_at  timestamptz not null default now()
);

create table if not exists transactions (
    tx_id       text primary key,
    run_id      text not null references runs(run_id),
    round       integer not null,
    wave        integer not null default 0,
    source      text not null,                  -- legit | attacker | asi_one
    attacker_id text,
    payload     jsonb not null,                 -- Transaction
    created_at  timestamptz not null default now()
);
create index if not exists transactions_run_round on transactions(run_id, round);

create table if not exists ground_truth (
    tx_id            text primary key references transactions(tx_id),
    is_fraud         boolean not null,
    attacker_id      text,
    breaches_mandate boolean not null default false
);

create table if not exists features (
    tx_id   text primary key references transactions(tx_id),
    feature_values  jsonb not null              -- FeatureVector.values
);

create table if not exists votes (
    id      bigserial primary key,
    tx_id   text not null references transactions(tx_id),
    voter   text not null,                      -- lane name or 'xgboost'
    payload jsonb not null                      -- Vote
);
create index if not exists votes_tx on votes(tx_id);

create table if not exists decisions (
    tx_id      text primary key references transactions(tx_id),
    action     text not null,                   -- allow | step_up | block
    escalated  boolean not null default false,
    payload    jsonb not null                   -- Decision (includes weights and investigator report)
);

create table if not exists escalations (
    tx_id       text primary key references transactions(tx_id),
    tool_calls  jsonb not null,                 -- list[ToolCall]
    decision    text not null,
    reasons     text not null,
    timed_out   boolean not null default false
);

create table if not exists settlements (
    tx_id    text primary key references transactions(tx_id),
    settled  boolean not null,
    tx_hash  text,
    network  text not null
);
alter table settlements add column if not exists reason text not null default '';  -- SettlementResult.reason

create table if not exists rulebook_versions (
    id          bigserial primary key,
    run_id      text not null references runs(run_id),
    round       integer not null,
    lane        text not null,
    draft       integer not null default 0,     -- rulebook draft number (stretch)
    rules       jsonb not null,
    diff        jsonb,
    deployed    boolean not null default true,
    veto_reason text
);

create table if not exists reflections (
    id       bigserial primary key,
    run_id   text not null references runs(run_id),
    round    integer not null,
    lane     text not null,
    payload  jsonb not null
);

create table if not exists features_added (
    id       bigserial primary key,
    run_id   text not null references runs(run_id),
    round    integer not null,
    lane     text not null,
    name     text not null,
    spec     jsonb not null                     -- field x window x aggregate
);

create table if not exists attackers (
    id          bigserial primary key,
    attacker_id text not null,
    run_id      text not null references runs(run_id),
    round       integer not null,
    source      text not null,                  -- spawned | outsider | threat_intel | held_out
    payload     jsonb not null                  -- AttackerRecord (playbook, parent, model, lineage)
);
create index if not exists attackers_id on attackers(attacker_id);

create table if not exists playbook_waves (
    id          bigserial primary key,
    attacker_id text not null,
    run_id      text not null references runs(run_id),
    round       integer not null,
    wave        integer not null,
    playbook    jsonb not null,
    strategy_changed boolean not null default false,
    trigger     text
);

create table if not exists archive_snapshots (
    id          bigserial primary key,
    run_id      text not null references runs(run_id),
    round       integer not null,
    shelf_id    text not null,                  -- ShelfKey.id()
    payload     jsonb not null                  -- ShelfState plus catch/miss tags
);

create table if not exists archive_events (
    id          bigserial primary key,
    run_id      text not null references runs(run_id),
    round       integer not null,
    shelf_id    text not null,
    attacker_id text not null,
    event       text not null,                  -- inserted | rejected_profit | rejected_duplicate | evicted | rescored
    detail      jsonb not null default '{}'
);

create table if not exists defender_actions (
    id        bigserial primary key,
    run_id    text not null references runs(run_id),
    round     integer not null,
    actor     text not null,                    -- lane or 'orchestrator'
    action    text not null,                    -- spawn | request_feature | request_scan | skip
    cost      integer not null,
    granted   boolean not null,
    reason    text not null,
    detail    jsonb not null default '{}'       -- target shelf, feature spec, merchants to scan
);

create table if not exists critic_checks (
    id         bigserial primary key,
    run_id     text not null references runs(run_id),
    round      integer not null,
    kind       text not null,                   -- miss_assignment | veto
    critic     jsonb not null,
    automated  jsonb not null,
    agree      boolean not null,
    second_opinion jsonb
);

create table if not exists threat_reports (
    report_id   text primary key,
    source_url  text not null,
    payload     jsonb not null,                 -- ThreatReport
    pattern     jsonb,                          -- ThreatPattern
    watch_rule_id text,
    critic_verdict text,
    attacker_ids jsonb not null default '[]'
);

create table if not exists mandates (
    mandate_id  text primary key,
    principal_id text not null,
    payload     jsonb not null                  -- Mandate
);

create table if not exists merchant_pages (
    id          bigserial primary key,
    url         text not null,
    merchant_id text,
    payload     jsonb not null                  -- PageScan
);

create table if not exists shopper_sessions (
    id          bigserial primary key,
    run_id      text not null references runs(run_id),
    round       integer not null,
    agent_id    text not null,
    pages_read  jsonb not null,
    decision    jsonb not null,
    hijacked    boolean not null
);

create table if not exists round_metrics (
    run_id   text not null references runs(run_id),
    round    integer not null,
    payload  jsonb not null,                    -- RoundMetrics
    primary key (run_id, round)
);

create table if not exists llm_spend (
    id          bigserial primary key,
    run_id      text,
    round       integer,
    role        text not null,
    model       text not null,
    input_tokens  integer not null,
    output_tokens integer not null,
    cost_usd    numeric(10, 5) not null,
    created_at  timestamptz not null default now()
);

create table if not exists chat_requests (
    id          bigserial primary key,
    sender      text not null,
    message     text not null,
    function    text,
    result      jsonb,
    created_at  timestamptz not null default now()
);
