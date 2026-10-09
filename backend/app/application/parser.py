"""Pure parsers for the file types discovery understands. Each takes text and returns plain data; none
touches the filesystem and none executes anything (YAML is loaded with safe_load).
"""

from __future__ import annotations

import json
import re

import yaml

CRITICALITY_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_CRITICALITY_ATTR = re.compile(r"service\.criticality=([A-Za-z]+)")
_ENV_ADDR = re.compile(r"^(?P<name>[A-Z0-9_]+?)_(?:ADDR|HOST|URL)$")
_PROTO_SERVICE = re.compile(r"service\s+(\w+)\s*\{(.*?)^\}", re.S | re.M)
_PROTO_RPC = re.compile(r"rpc\s+(\w+)\s*\(\s*(\w+)\s*\)\s*returns\s*\(\s*(\w+)\s*\)")


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load_yaml_docs(text: str) -> list[dict]:
    try:
        return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


def load_json(text: str) -> dict | list | None:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _env_items(environment) -> dict[str, str | None]:
    """Compose `environment` as a dict (list items without '=' mean 'inherit' -> None)."""
    if isinstance(environment, dict):
        return {str(k): (None if v is None else str(v)) for k, v in environment.items()}
    items: dict[str, str | None] = {}
    for entry in environment or []:
        key, sep, value = str(entry).partition("=")
        items[key.strip()] = value if sep else None
    return items


def criticality_from_attributes(value: str | None) -> str | None:
    match = _CRITICALITY_ATTR.search(value or "")
    level = match.group(1).upper() if match else None
    return level if level in CRITICALITY_LEVELS else None


def parse_compose(text: str, source: str) -> dict[str, dict]:
    """Services declared in a Docker Compose file -> facts read straight from the file (never run)."""
    services: dict[str, dict] = {}
    for doc in load_yaml_docs(text):
        for key, spec in (doc.get("services") or {}).items():
            if not isinstance(spec, dict):
                continue
            env = _env_items(spec.get("environment"))
            depends = spec.get("depends_on") or []
            depends = list(depends) if not isinstance(depends, dict) else list(depends.keys())
            services[key] = {
                "name": env.get("OTEL_SERVICE_NAME") or None,
                "container": spec.get("container_name"),
                "criticality": criticality_from_attributes(env.get("OTEL_RESOURCE_ATTRIBUTES")),
                "depends_on": depends,
                "env_refs": sorted(k for k in env if _ENV_ADDR.match(k)),
                "source": source,
            }
    return services


def parse_kubernetes(text: str, source: str) -> dict[str, dict]:
    """Deployments in Kubernetes manifests -> same shape as parse_compose."""
    services: dict[str, dict] = {}
    for doc in load_yaml_docs(text):
        if doc.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet"):
            continue
        name = (doc.get("metadata") or {}).get("name")
        containers = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
        env: dict[str, str | None] = {}
        for container in containers:
            for item in container.get("env") or []:
                env[str(item.get("name"))] = item.get("value")
        if name:
            services[name] = {
                "name": env.get("OTEL_SERVICE_NAME") or None, "container": None,
                "criticality": criticality_from_attributes(env.get("OTEL_RESOURCE_ATTRIBUTES")),
                "depends_on": [], "env_refs": sorted(k for k in env if _ENV_ADDR.match(k)), "source": source,
            }
    return services


def resolve_env_reference(var: str, known: dict[str, str]) -> str | None:
    """Env var PAYMENT_ADDR -> "payment". `known` maps normalised service name -> service name."""
    match = _ENV_ADDR.match(var)
    if not match:
        return None
    token = norm(match["name"])
    if token in known:
        return known[token]
    prefixed = [name for key, name in known.items() if key.startswith(token)]
    return prefixed[0] if len(prefixed) == 1 else None


def parse_proto(text: str, source: str) -> list[dict]:
    apis = []
    for match in _PROTO_SERVICE.finditer(text):
        for rpc in _PROTO_RPC.finditer(match.group(2)):
            apis.append({"proto_service": match.group(1), "rpc": rpc.group(1), "request": rpc.group(2),
                         "response": rpc.group(3), "source": source})
    return apis


def parse_openapi(text: str, source: str) -> list[dict]:
    doc = load_json(text) if text.lstrip().startswith("{") else (load_yaml_docs(text) or [None])[0]
    if not isinstance(doc, dict) or not (doc.get("openapi") or doc.get("swagger")):
        return []
    apis = []
    for path, methods in (doc.get("paths") or {}).items():
        for method in methods if isinstance(methods, dict) else []:
            if method.lower() in {"get", "post", "put", "delete", "patch"}:
                apis.append({"name": f"{method.upper()} {path}", "source": source})
    return apis


def flag_enabled(spec: dict) -> bool | None:
    """Is a flagd flag switched on? Its default variant's value must be truthy (false / 0 / "off" / null mean off)
    and the flag not DISABLED. None when the definition does not say which variant is active."""
    variants = spec.get("variants")
    variant = spec.get("defaultVariant")
    if not isinstance(variants, dict) or variant not in variants:
        return None
    if spec.get("state") == "DISABLED":
        return False
    value = variants[variant]
    return not (value is None or value is False or value == 0 or str(value).lower() in ("off", "false", "0"))


def parse_flags(text: str, source: str) -> list[dict]:
    """flagd / OpenFeature flag definitions -> documented failure scenarios."""
    doc = load_json(text)
    if not isinstance(doc, dict) or not isinstance(doc.get("flags"), dict):
        return []
    flags = []
    for name, spec in doc["flags"].items():
        if isinstance(spec, dict):
            flags.append({"name": name, "description": spec.get("description"),
                          "default_variant": spec.get("defaultVariant"),
                          "variants": [str(v) for v in (spec.get("variants") or {})], "source": source,
                          "enabled": flag_enabled(spec), "active_variant": spec.get("defaultVariant")})
    return flags


def parse_markdown(text: str) -> dict:
    """Title (first heading) and summary (first prose paragraph) of a Markdown document."""
    title = None
    in_code = False
    paragraph: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("#"):
            if paragraph:
                break
            title = title or stripped.lstrip("#").strip()
            continue
        if stripped.startswith(("<", "[![", "![", "|", ">", "---")) or not stripped:
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return {"title": title, "summary": " ".join(paragraph)[:500] or None}


def parse_otel_collector(text: str) -> dict:
    """Receivers / exporters / pipelines of one OpenTelemetry Collector config layer."""
    doc = (load_yaml_docs(text) or [{}])[0]
    pipelines = {}
    for signal, spec in ((doc.get("service") or {}).get("pipelines") or {}).items():
        if isinstance(spec, dict):
            pipelines[signal.split("/")[0]] = {
                "receivers": list(spec.get("receivers") or []),
                "processors": list(spec.get("processors") or []),
                "exporters": list(spec.get("exporters") or []),
            }
    return {"receivers": sorted((doc.get("receivers") or {}).keys()),
            "exporters": sorted((doc.get("exporters") or {}).keys()), "pipelines": pipelines}


def parse_env_file(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values
