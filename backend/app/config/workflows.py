"""Business workflow catalogue: keyword rules for classification and each workflow's criticality.

Classification is deterministic: endpoint path segments and the service name are split into tokens and
matched against each workflow's keywords (endpoint matches weigh more than service-name matches).
"""

from __future__ import annotations

UNKNOWN = "Unknown"

CRITICALITY_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")

# Workflow -> business criticality.
WORKFLOW_CRITICALITY: dict[str, str] = {
    "Authentication": "HIGH",
    "Authorization": "HIGH",
    "Transaction": "CRITICAL",
    "Payment": "CRITICAL",
    "Checkout/Order": "CRITICAL",
    "Search": "MEDIUM",
    "Data Retrieval": "MEDIUM",
    "Data Processing": "MEDIUM",
    "File/Document": "MEDIUM",
    "Notification": "MEDIUM",
    "Integration": "HIGH",
    "Reporting": "LOW",
    "Scheduling": "MEDIUM",
    "Administration": "MEDIUM",
    "Background Jobs": "MEDIUM",
    "Monitoring/Health": "HIGH",
    "Audit/Compliance": "HIGH",
    "Backup/Recovery": "CRITICAL",
    "Deployment/Release": "HIGH",
    "Communication": "MEDIUM",
    UNKNOWN: "UNKNOWN",
}

# Workflow -> tokens. A token matches its keyword exactly or as a plural ("orders" matches "order").
# Order matters only to break ties.
WORKFLOW_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Authentication": ("login", "logout", "signin", "signup", "token", "auth", "oauth", "sso", "session", "mfa", "password", "credential"),
    "Authorization": ("permission", "authorize", "authz", "rbac", "acl", "role", "entitlement", "access"),
    "Payment": ("payment", "pay", "refund", "charge", "billing", "invoice", "card", "wallet", "payout"),
    "Transaction": ("transaction", "txn", "transfer", "ledger", "settlement"),
    "Checkout/Order": ("order", "checkout", "cart", "basket", "purchase"),
    "Search": ("search", "query", "lookup", "autocomplete", "suggest"),
    "Data Retrieval": ("product", "catalog", "item", "inventory", "detail", "profile", "account", "customer"),
    "Data Processing": ("process", "etl", "transform", "pipeline", "ingest", "import", "batch"),
    "File/Document": ("file", "upload", "download", "document", "attachment", "pdf", "storage", "blob"),
    "Notification": ("notification", "notify", "push", "alert"),
    "Integration": ("integration", "webhook", "partner", "callback", "connector", "sync", "external"),
    "Reporting": ("report", "analytics", "export", "stat", "insight"),
    "Scheduling": ("schedule", "calendar", "booking", "appointment", "slot"),
    "Administration": ("admin", "setting", "config", "tenant", "management"),
    "Background Jobs": ("job", "worker", "queue", "cron", "task"),
    "Monitoring/Health": ("health", "healthz", "readyz", "livez", "ping", "heartbeat", "metric", "monitor"),
    "Audit/Compliance": ("audit", "compliance", "gdpr", "consent", "retention", "kyc", "aml"),
    "Backup/Recovery": ("backup", "restore", "recovery", "snapshot", "replica"),
    "Deployment/Release": ("deploy", "deployment", "release", "rollout", "migration", "build"),
    "Communication": ("message", "chat", "email", "mail", "inbox", "conversation", "sms"),
}

ENDPOINT_MATCH_WEIGHT = 3
SERVICE_MATCH_WEIGHT = 2

# Generic tokens ignored when tokenising endpoints and service names.
IGNORED_TOKENS = frozenset({"api", "service", "svc", "app", "server", "internal", "public", "v1", "v2", "v3", "id"})
