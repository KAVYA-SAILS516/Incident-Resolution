"""A tiny application on disk (Compose + proto + flags + collector config + READMEs) for discovery tests."""

from __future__ import annotations

from pathlib import Path

COMPOSE = """
services:
  payment:
    container_name: payment
    environment:
      - FLAGD_HOST
      - OTEL_RESOURCE_ATTRIBUTES=${OTEL_RESOURCE_ATTRIBUTES},service.criticality=critical
      - OTEL_SERVICE_NAME=payment
    depends_on:
      otel-collector:
        condition: service_started
      flagd:
        condition: service_started
  checkout:
    environment:
      - PAYMENT_ADDR
      - EMAIL_ADDR
      - OTEL_RESOURCE_ATTRIBUTES=${OTEL_RESOURCE_ATTRIBUTES},service.criticality=critical
      - OTEL_SERVICE_NAME=checkout
  email:
    environment:
      - OTEL_RESOURCE_ATTRIBUTES=${OTEL_RESOURCE_ATTRIBUTES},service.criticality=medium
      - OTEL_SERVICE_NAME=email
  reports:
    environment:
      - OTEL_SERVICE_NAME=reports
  flagd:
    image: flagd
  otel-collector:
    image: collector
"""
PROTO = """
service PaymentService {
  rpc Charge(ChargeRequest) returns (ChargeResponse) {}
}
service EmailService {
  rpc SendOrderConfirmation(SendOrderConfirmationRequest) returns (Empty) {}
}
"""
FLAGS = '{"flags": {"paymentFailure": {"description": "Fail payment service", "defaultVariant": "off", "state": "ENABLED", "variants": {"off": 0, "on": 1}}}}'
COLLECTOR = """
receivers:
  otlp: {}
exporters:
  debug: {}
service:
  pipelines:
    traces: {receivers: [otlp], exporters: [debug]}
    metrics: {receivers: [otlp], exporters: [debug]}
    logs: {receivers: [otlp], exporters: [debug]}
"""


def make_app(root: Path) -> Path:
    app = root / "shop"
    (app / "src" / "payment").mkdir(parents=True)
    (app / "src" / "checkout").mkdir(parents=True)
    (app / "src" / "otel-collector").mkdir(parents=True)
    (app / "pb").mkdir()
    (app / "docs").mkdir()
    (app / "compose.yaml").write_text(COMPOSE, encoding="utf-8")
    (app / "pb" / "demo.proto").write_text(PROTO, encoding="utf-8")
    (app / "src" / "payment" / "flags.json").write_text(FLAGS, encoding="utf-8")
    (app / "src" / "otel-collector" / "otelcol-config.yml").write_text(COLLECTOR, encoding="utf-8")
    (app / "README.md").write_text("# Shop\n\nA small demo shop used in tests.\n", encoding="utf-8")
    (app / "docs" / "architecture.md").write_text("# Architecture\n\nServices talk over gRPC.\n", encoding="utf-8")
    (app / "src" / "payment" / "README.md").write_text(
        "# Payment Service\n\nThis service is responsible for processing payments.\n\n## Build\n\nrun it\n", encoding="utf-8")
    (app / "src" / "checkout" / "README.md").write_text("# Checkout\n\nPlaces orders.\n", encoding="utf-8")
    # things discovery must ignore
    (app / "node_modules" / "pkg").mkdir(parents=True)
    (app / "node_modules" / "pkg" / "README.md").write_text("# Ignored\n\nignored text\n", encoding="utf-8")
    (app / "binary.bin").write_bytes(b"\x00\x01" * 100)
    (app / "src" / "payment" / "main.js").write_text("throw new Error('must never be executed')", encoding="utf-8")
    return app
