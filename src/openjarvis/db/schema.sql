-- Anti-Brain Fog Agentic Core PostgreSQL Schema

CREATE TABLE IF NOT EXISTS financial_ledger (
    id SERIAL PRIMARY KEY,
    transaction_date TIMESTAMP WITH TIME ZONE NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    merchant VARCHAR(255) NOT NULL,
    account_identifier VARCHAR(4) NOT NULL, -- Last 4 digits
    raw_email_id VARCHAR(255) UNIQUE NOT NULL, -- Prevents duplicate indexing
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_balances (
    id SERIAL PRIMARY KEY,
    account_identifier VARCHAR(4) UNIQUE NOT NULL,
    current_balance NUMERIC(12, 2) NOT NULL,
    last_updated TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_insight_logs (
    id SERIAL PRIMARY KEY,
    insight_type VARCHAR(100) NOT NULL, -- e.g., 'hvac_efficiency', 'budget_leak'
    last_triggered TIMESTAMP WITH TIME ZONE NOT NULL,
    summary_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_insight_type UNIQUE (insight_type)
);

CREATE TABLE IF NOT EXISTS daily_telemetry_history (
    id SERIAL PRIMARY KEY,
    telemetry_date DATE NOT NULL UNIQUE DEFAULT CURRENT_DATE,
    telemetry_data JSONB NOT NULL, -- High-density JSONB storage for suppressed events
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_financial_ledger_date ON financial_ledger(transaction_date);
CREATE INDEX IF NOT EXISTS idx_financial_ledger_account ON financial_ledger(account_identifier);
CREATE INDEX IF NOT EXISTS idx_agent_insight_logs_type ON agent_insight_logs(insight_type);

CREATE TABLE IF NOT EXISTS user_state (
    id SERIAL PRIMARY KEY,
    state_key VARCHAR(100) UNIQUE NOT NULL,
    state_value JSONB NOT NULL,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fitness_stats (
    id SERIAL PRIMARY KEY,
    activity_date DATE NOT NULL,
    activity_type VARCHAR(50) NOT NULL,
    metrics JSONB NOT NULL,
    raw_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fitness_activity_date ON fitness_stats(activity_date);
