"""Application discovery: a read-only scan of an application directory into ApplicationKnowledge.

Everything is derived from files in the supplied directory (Compose / Kubernetes manifests, .proto files,
OpenAPI files, OpenTelemetry Collector configs, feature-flag files, Markdown). Nothing is executed and
nothing outside the directory is read. What is not found stays None / empty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.application import parser
from app.application.models import ApiInfo, ApplicationKnowledge, DocumentRef, FailureScenario, ServiceInfo
from app.application.reader import ApplicationFiles, collect_files, validate_application_path

TELEMETRY_PLUMBING = {"otel-collector"}  # carries telemetry; not a business dependency of any service
MAX_DOCUMENTS = 200
SIGNALS = ("logs", "metrics", "traces")


def _is_compose(path: Path) -> bool:
    return path.suffix.lower() in (".yml", ".yaml") and (path.name.lower().startswith(("compose", "docker-compose")))


def _is_collector_config(path: Path) -> bool:
    return path.suffix.lower() in (".yml", ".yaml") and "otel" in path.name.lower() and "config" in path.name.lower()


def _is_kubernetes(files: ApplicationFiles, path: Path) -> bool:
    return path.suffix.lower() in (".yml", ".yaml") and any(
        part.lower() in {"kubernetes", "k8s", "helm", "deploy"} for part in path.relative_to(files.root).parts[:-1]
    )


def _words(text: str) -> str:
    return parser.norm(text)


def _discover_services(files: ApplicationFiles, warnings: list[str]) -> tuple[dict[str, dict], list[dict]]:
    declared: dict[str, dict] = {}
    for path in files.files:
        if _is_compose(path):
            parsed = parser.parse_compose(files.read(path), files.rel(path))
        elif _is_kubernetes(files, path):
            parsed = parser.parse_kubernetes(files.read(path), files.rel(path))
        else:
            continue
        for key, spec in parsed.items():
            current = declared.setdefault(key, {**spec, "depends_on": [], "env_refs": []})
            current["name"] = current.get("name") or spec["name"]
            current["criticality"] = current.get("criticality") or spec["criticality"]
            if spec["criticality"] and not current.get("criticality_source"):
                current["criticality_source"] = spec["source"]
            current["depends_on"] = sorted(set(current["depends_on"]) | set(spec["depends_on"]))
            current["env_refs"] = sorted(set(current["env_refs"]) | set(spec["env_refs"]))

    telemetry_name = {key: spec["name"] or key for key, spec in declared.items()}
    known = {parser.norm(name): name for name in telemetry_name.values()}
    known.update({parser.norm(key): telemetry_name[key] for key in declared})
    edges: list[dict] = []
    for key, spec in declared.items():
        source = telemetry_name[key]
        targets: dict[str, str] = {}
        for dep in spec["depends_on"]:
            if dep not in TELEMETRY_PLUMBING:
                targets[telemetry_name.get(dep, dep)] = f"{spec['source']} depends_on"
        for var in spec["env_refs"]:
            target = parser.resolve_env_reference(var, known)
            if target and target != source and target not in TELEMETRY_PLUMBING:
                targets.setdefault(target, f"{spec['source']} env {var}")
        edges += [{"from": source, "to": target, "source": origin} for target, origin in sorted(targets.items())]
    return {telemetry_name[k]: {**v, "key": k} for k, v in declared.items()}, edges


def _discover_apis(files: ApplicationFiles, service_names: dict[str, str]) -> list[ApiInfo]:
    apis: list[ApiInfo] = []
    for path in files.matching(suffixes=(".proto",)):
        for api in parser.parse_proto(files.read(path), files.rel(path)):
            base = api["proto_service"].removesuffix("Service")
            apis.append(ApiInfo(service=service_names.get(parser.norm(base)), kind="grpc",
                                name=f"{api['proto_service']}/{api['rpc']}", request=api["request"],
                                response=api["response"], source=api["source"]))
    for path in files.matching(suffixes=(".yml", ".yaml", ".json")):
        if "openapi" in path.name.lower() or "swagger" in path.name.lower():
            for api in parser.parse_openapi(files.read(path), files.rel(path)):
                apis.append(ApiInfo(service=None, kind="http", name=api["name"], source=api["source"]))
    return apis


def _discover_scenarios(files: ApplicationFiles, service_names: dict[str, str]) -> list[FailureScenario]:
    scenarios: dict[str, FailureScenario] = {}
    for path in files.matching(suffixes=(".json",)):
        for flag in parser.parse_flags(files.read(path), files.rel(path)):
            if flag["name"] in scenarios:  # the same flag is often copied next to the service that reads it
                continue
            token = parser.norm(flag["name"])
            matches = [k for k in service_names if token.startswith(k)]
            owner = service_names[max(matches, key=len)] if matches else None
            scenarios[flag["name"]] = FailureScenario(service=owner, **flag)
    if scenarios:  # which documents mention each scenario by name
        for path in files.matching(suffixes=(".md",)):
            text = files.read(path)
            for scenario in scenarios.values():
                if scenario.name in text:
                    scenario.documented_in.append(files.rel(path))
    return list(scenarios.values())


def _discover_documents(files: ApplicationFiles) -> list[DocumentRef]:
    docs = []
    for path in files.matching(suffixes=(".md",)):
        rel = files.rel(path)
        if path.name.lower() == "readme.md" or rel.lower().startswith("docs/"):
            meta = parser.parse_markdown(files.read(path))
            docs.append(DocumentRef(path=rel, title=meta["title"], summary=meta["summary"]))
    return docs[:MAX_DOCUMENTS]


def _discover_telemetry(files: ApplicationFiles) -> dict:
    layers = []
    for path in files.files:
        if _is_collector_config(path):
            layers.append({"file": files.rel(path), **parser.parse_otel_collector(files.read(path))})
    signals: dict[str, dict] = {s: {"receivers": set(), "exporters": set()} for s in SIGNALS}
    for layer in layers:
        for signal, pipeline in layer["pipelines"].items():
            if signal in signals:
                signals[signal]["receivers"].update(pipeline["receivers"])
                signals[signal]["exporters"].update(pipeline["exporters"])
    present = {s: {k: sorted(v) for k, v in spec.items()} for s, spec in signals.items() if spec["receivers"] or spec["exporters"]}
    return {"collector_configs": [layer["file"] for layer in layers], "signals": present,
            "collector_receivers": sorted({r for layer in layers for r in layer["receivers"]}),
            "collector_exporters": sorted({e for layer in layers for e in layer["exporters"]})}


def _service_documents(docs: list[DocumentRef], name: str, key: str) -> list[DocumentRef]:
    wanted = {parser.norm(name), parser.norm(key)}
    found = []
    for doc in docs:
        parts = Path(doc.path).parts[:-1]
        if parts and parser.norm(parts[-1]) in wanted:
            found.append(doc)
    return found


def discover_application(name: str, path: str, allowed_root: Path | None = None) -> ApplicationKnowledge:
    root = validate_application_path(path, allowed_root)
    files = collect_files(root)
    warnings = [f"Skipped {item}" for item in files.skipped[:20]]
    if files.truncated:
        warnings.append(f"Scan stopped after {len(files.files)} files; some files were not read")

    declared, edges = _discover_services(files, warnings)
    service_names = {parser.norm(n): n for n in declared} | {parser.norm(v["key"]): n for n, v in declared.items()}
    documents = _discover_documents(files)
    apis = _discover_apis(files, service_names)
    scenarios = _discover_scenarios(files, service_names)
    telemetry = _discover_telemetry(files)
    if not declared:
        warnings.append("No service definitions (Compose / Kubernetes) were found")
    if not telemetry["signals"]:
        warnings.append("No OpenTelemetry Collector configuration was found")

    services = []
    for service_name, spec in sorted(declared.items()):
        docs = _service_documents(documents, service_name, spec["key"])
        readme = next((d for d in docs if d.summary), None)
        services.append(ServiceInfo(
            name=service_name,
            kind="application" if spec["name"] else "infrastructure",
            purpose=readme.summary if readme else None,
            criticality=spec["criticality"],
            criticality_source=spec.get("criticality_source") if spec["criticality"] else None,
            dependencies=sorted({e["to"] for e in edges if e["from"] == service_name}),
            dependents=sorted({e["from"] for e in edges if e["to"] == service_name}),
            apis=sorted({a.name for a in apis if a.service == service_name}),
            documentation_references=[d.path for d in docs],
            telemetry_information={"otel_service_name": spec["name"], "container": spec["container"],
                                   "defined_in": spec["source"]},
            failure_scenarios=sorted(s.name for s in scenarios if s.service == service_name),
        ))

    env_files = [files.rel(p) for p in files.files if p.name.startswith(".env")]
    ports = {}
    for path_ in files.files:
        if path_.name == ".env":
            ports = {k: v for k, v in parser.parse_env_file(files.read(path_)).items() if k.endswith("_PORT")}
    root_readme = next((d for d in documents if d.path.lower() == "readme.md"), None)

    return ApplicationKnowledge(
        application_name=name.strip(),
        application_path=str(root),
        summary=root_readme.summary if root_readme else None,
        services=services,
        dependencies=edges,
        apis=apis,
        documentation=documents,
        configuration={"compose_files": [files.rel(p) for p in files.files if _is_compose(p)],
                       "env_files": env_files, "ports": ports,
                       "feature_flag_files": sorted({s.source for s in scenarios})},
        criticality={s.name: s.criticality for s in services if s.kind == "application"},
        telemetry=telemetry,
        failure_scenarios=scenarios,
        files_scanned=len(files.files),
        scan_warnings=warnings,
        last_scanned_at=datetime.now(timezone.utc),
    )
