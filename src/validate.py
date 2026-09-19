"""Setup validation for the printer statistics project.

Provides validate_setup() for local sanity checks (config file, JSON Schema,
printer/adapters, runtime dirs, database) and validate_network() for optional
live SNMP reachability checks against each configured printer.
"""

import concurrent.futures
import json
import os
from collections import namedtuple
from functools import partial

from jsonschema import Draft7Validator
from jsonschema.exceptions import best_match

from adapters import get_adapter_class

Issue = namedtuple('Issue', ['level', 'message'])
OK = 'OK'
WARN = 'WARN'
FAIL = 'FAIL'


def _load_json(path, label):
    """Load a JSON file, returning (data, issue_or_None)."""
    if not os.path.exists(path):
        return None, Issue(FAIL, f'{label} not found: {path} (run `python main.py --init`)')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f), None
    except json.JSONDecodeError as e:
        return None, Issue(FAIL, f'{label} is not valid JSON: {e}')


def find_schema_violation(config, schema):
    """Return the single best schema violation for a config, or None if valid."""
    validator = Draft7Validator(schema)
    return best_match(validator.iter_errors(config))


def resolve_schema_path(config_path, default=None):
    """Pick the file to validate a config against.

    Prefers the config's own '$schema' link when it points at a local file;
    otherwise falls back to the default schema next to the config.
    """
    if default is None:
        default = os.path.join(os.path.dirname(config_path), 'config.json.schema')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            link = json.load(f).get('$schema')
    except Exception:
        return default
    if not link:
        return default
    # Skip remote/absolute links - we can only validate against local files
    if (link.startswith(('http://', 'https://', 'file://', '/', '\\'))
            or len(link) > 1 and link[1] == ':'):
        return default
    candidate = os.path.join(os.path.dirname(config_path), link)
    return candidate if os.path.isfile(candidate) else default


def validate_setup(config_path, project_root, schema_path=None, db_path=None):
    """Run local validation checks.

    Args:
        config_path: Path to config.json.
        project_root: Project root (for runtime dir checks).
        schema_path: Schema file to validate against. When None, the config's
            '$schema' link is resolved and used, falling back to the default.
        db_path: Database path to test. Defaults to data/<filename> from config.

    Returns:
        list of Issue namedtuples (levels: OK / WARN / FAIL).
    """
    issues = []

    # 1. Config file exists and parses
    config, err = _load_json(config_path, 'Config file')
    if err:
        return _summarize(issues + [err])

    issues.append(Issue(OK, f'Config file found and valid JSON: {config_path}'))

    # 2. Validates against the JSON Schema (honoring the config's $schema link)
    schema_path = resolve_schema_path(config_path, schema_path)
    schema, err = _load_json(schema_path, 'Config schema')
    if err:
        issues.append(err)  # schema ships with repo, so this is a repo problem
    else:
        match = find_schema_violation(config, schema)
        if match:
            issues.append(Issue(FAIL, f'Config does not match {os.path.basename(schema_path)}: '
                                      f'{match.message} (at {".".join(str(p) for p in match.path) or "<root>"})'))
        else:
            issues.append(Issue(OK, f'Config matches {os.path.basename(schema_path)}'))

    # 3. At least one printer configured
    printers = config.get('printers') or []
    if not printers:
        issues.append(Issue(FAIL, 'No printers configured in config.json'))
    else:
        issues.append(Issue(OK, f'{len(printers)} printer(s) configured'))

    # 4. Every configured model has a registered adapter
    for p in printers:
        model = p.get('model', '')
        try:
            get_adapter_class(model)
            issues.append(Issue(OK, f'Adapter found for model: {model}'))
        except ValueError as e:
            issues.append(Issue(FAIL, f'{e} - collection would fail for this printer'))

    # 5. Runtime directories exist
    for name in ('data', 'logs'):
        d = os.path.join(project_root, name)
        if os.path.isdir(d):
            issues.append(Issue(OK, f'Runtime directory exists: {name}/'))
        else:
            issues.append(Issue(WARN, f'Runtime directory missing: {name}/ '
                                      f'(run `python main.py --init`)'))

    # 6. Database initializes (implies data/ is writable)
    try:
        import db
        actual_db = db_path or os.path.join(
            project_root, 'data',
            config.get('db', {}).get('filename', 'printer_stats.db'))
        conn = db.init_db(actual_db)
        db.close_db(conn)
        issues.append(Issue(OK, f'Database initializes: {actual_db}'))
    except Exception as e:
        issues.append(Issue(FAIL, f'Database error: {e}'))

    return _summarize(issues)


def _summarize(issues):
    """Ensure at least one issue is present and return sorted by severity."""
    if not issues:
        issues.append(Issue(FAIL, 'No validation checks ran'))
    order = {'FAIL': 0, 'WARN': 1, 'OK': 2}
    return sorted(issues, key=lambda i: order[i.level])


def _network_check_one(printer, community, timeout_sec, retries):
    """Check SNMP reachability of one printer; returns an Issue (never raises)."""
    ip = printer['ip']
    model = printer['model']
    try:
        adapter = get_adapter_class(model)(
            ip=ip, community=community,
            timeout_sec=timeout_sec, retries=retries)
        if adapter.is_reachable():
            return Issue(OK, f'SNMP reachable: {model} ({ip})')
        return Issue(WARN, f'SNMP NOT reachable: {model} ({ip}) - '
                           f'check network, community "{community}", port 161')
    except Exception as e:
        return Issue(WARN, f'SNMP check failed for {model} ({ip}): {e}')


def validate_network(config):
    """Live SNMP reachability check for each configured printer.

    Checks run in parallel (collection.max_concurrency, default 20) so a
    dead printer's SNMP timeout doesn't stall checks for the rest of the
    fleet. Issues retain printer config order.

    Returns:
        list of Issue namedtuples. Unreachable printers are WARN (the check
        depends on the live network, not the project setup).
    """
    snmp = config.get('snmp', {})
    community = snmp.get('community', 'public')
    timeout = snmp.get('timeout_sec', 3)
    retries = snmp.get('retries', 2)
    printers = list(config.get('printers', []))
    if not printers:
        return []

    max_concurrency = max(
        1, int(config.get('collection', {}).get('max_concurrency', 20))
    )

    check_one = partial(
        _network_check_one,
        community=community,
        timeout_sec=timeout,
        retries=retries,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        return list(executor.map(check_one, printers))