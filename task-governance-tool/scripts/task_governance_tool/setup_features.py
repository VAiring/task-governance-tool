"""Setup-only optional feature selection; core setup and feature outcomes are separate.

No Task mutation, process launch, host trust inference, or automatic retries.
Existing feature readers own validity; Runner uses its existing publisher.
"""

from dataclasses import replace

from task_governance_tool import setup_feature_config as config
from task_governance_tool.effort import load_effort_profile
from task_governance_tool.viewer_config import load_viewer_refresh_interval
from task_governance_tool.usage_hook_setup import setup_usage_hooks
from task_governance_tool.state_paths import path_lexically_exists
from task_governance_tool.verification_runner_plan import (
    VerificationRunnerPlan, decode_verification_runner_plan, encode_verification_runner_plan,
)
from task_governance_tool.verification_runner_plan_publisher import (
    CONFIRM_RUNNER_PLAN_SOURCE, capture_runner_plan_authoring_source,
    publish_verification_runner_plan,
)


def _runner(root, skill, enabled, read_only):
    path = skill / "config/verification-runner.json"
    if not path_lexically_exists(path) and enabled is not True:
        config.read(path, skill)  # validate an existing parent without requiring Git
        return False, False, False
    source = capture_runner_plan_authoring_source(root, skill)
    existing = decode_verification_runner_plan(source.raw_blob) if source.raw_blob is not None else None
    plan = existing or VerificationRunnerPlan(1, "taskgov-local-plan", False, ())
    changed = enabled is not None and enabled != plan.trusted_local
    if enabled is not None and not read_only:
        candidate = (encode_verification_runner_plan(replace(plan, trusted_local=enabled))
                     if changed else CONFIRM_RUNNER_PLAN_SOURCE)
        publish_verification_runner_plan(root, skill, source, candidate)
    effective = enabled if changed and not read_only else plan.trusted_local
    return existing is not None, effective, changed


def _profile(skill, feature, enabled, read_only):
    viewer = feature == "viewer_reload"
    path = skill / "config" / ("viewer.json" if viewer else "effort-advisory.json")
    original = config.read(path, skill)
    if viewer:
        current = load_viewer_refresh_interval(skill) > 0
        default = {"schema_version": 1, "profile": "visibility-refresh-v1",
                   "refresh_interval_seconds": 30}
    else:
        profile = load_effort_profile(skill)
        if not profile.valid:
            raise ValueError("invalid effort profile")
        current = profile.enabled
        default = {"schema_version": 1, "profile": "informational-v1", "thresholds": {}}
    document = config.decode(original[0]) if original is not None else default
    # Confirm that the reader observed the same bytes we will retain and edit.
    if config.read(path, skill) != original:
        raise ValueError("configuration changed")
    changed = enabled is not None and enabled != current
    if changed and not read_only:
        document["enabled"] = enabled
        config.publish(path, skill, original, config.encode(document))
    return original is not None, enabled if changed and not read_only else current, changed


def _wait_policy(skill, enabled, read_only):
    _original, choices = config.read_choices(skill)
    current = choices.get("review_wait") is True
    changed = enabled is not None and choices.get("review_wait") is not enabled
    if enabled is not None and not read_only:
        changed = _save_choice(skill, "review_wait", enabled) or changed
    return "review_wait" in choices, enabled if enabled is not None and not read_only else current, changed


def _configuration(root, skill, name, enabled, read_only):
    if name == "verification_runner":
        return _runner(root, skill, enabled, read_only)
    if name == "review_wait":
        return _wait_policy(skill, enabled, read_only)
    return _profile(skill, name, enabled, read_only)


def _save_choice(skill, feature, enabled):
    original, choices = config.read_choices(skill)
    if choices.get(feature) is enabled:
        return False
    choices[feature] = enabled
    config.publish(skill / config.CHOICES_PATH, skill, original,
                   config.encode({"schema_version": 1, "choices": choices}))
    return True


def _viewer_publish(scope):
    from task_governance_tool.state_resolver import resolve_project_state
    from task_governance_tool.viewer_maintenance import publish_setup_viewer
    resolution = resolve_project_state(skill_root=scope.skill_root, repo=scope.canonical_repo)
    if resolution.error_code or resolution.target is None or resolution.binding != "matching":
        raise ValueError("Viewer unavailable")
    if publish_setup_viewer(resolution.target, skill_root=scope.skill_root).code != "succeeded":
        raise ValueError("Viewer unavailable")


def setup_features(inspection, core_result, *, selections, read_only):
    """Apply only supplied choices; reread effective configs rather than trust intent."""
    rows = {name: {"requested": selections.get(name), "selection": "undecided",
                   "selection_source": "none", "effective": "unknown",
                   "status": "not_attempted", "error": None}
            for name in config.FEATURES}
    result = {"features": rows, "offer": [], "viewer_default_interval_seconds": 30}
    hooks = {"status": "not_attempted", "planned_writes": [], "completed_writes": [],
             "trust": "unknown", "next_action": None, "error": None}
    if not core_result.ok or inspection.scope is None:
        return result, hooks
    scope = inspection.scope
    skill, root = scope.skill_root, scope.canonical_repo
    try:
        _original, choices = config.read_choices(skill)
    except Exception:
        for row in rows.values():
            row.update(selection="unknown", selection_source="unknown", status="unavailable",
                       error="feature_choices_unavailable")
        return result, hooks

    for name, row in rows.items():
        requested = selections.get(name)
        if name in choices:
            row.update(selection="on" if choices[name] else "off", selection_source="saved")
        try:
            if requested is not None and type(requested) is not bool:
                raise ValueError("invalid choice")
            if name == "usage_collection":
                changed = requested is not None and choices.get(name) is not requested
                if changed and not read_only:
                    _save_choice(skill, name, requested)
                # Preview uses the proposed switch for its plan, never claims it applied.
                enabled = requested if requested is not None else choices.get(name)
                hooks = setup_usage_hooks(inspection, core_result, read_only=read_only, enabled=enabled)
                if hooks["status"] in {"unavailable", "not_attempted"}:
                    raise ValueError("hooks unavailable")
                present = hooks["status"] not in {"not_requested", "disabled"}
                effective = ("off" if hooks["status"] in {"disabled", "not_requested"} else
                             "on" if hooks["status"] in {"current", "prepared"} else "unknown")
                if read_only and requested is not None:
                    observed = setup_usage_hooks(inspection, core_result, read_only=True,
                                                 enabled=choices.get(name))
                    effective = ("on" if observed["status"] == "current" else
                                 "off" if observed["status"] in {"disabled", "not_requested"} else "unknown")
            else:
                present, effective_bool, changed = _configuration(root, skill, name, requested, read_only)
                effective = "on" if effective_bool else "off"
            row.update(effective=effective, status="observed")
            if name not in choices and present and requested is None:
                row.update(selection=effective, selection_source="existing")
            if requested is not None:
                if read_only:
                    row["status"] = "preview"
                else:
                    saved = _save_choice(skill, name, requested)
                    row.update(selection="on" if requested else "off", selection_source="saved",
                               status="applied" if changed or saved else "unchanged")
                    if name == "viewer_reload" and changed:
                        _viewer_publish(scope)
            if row["selection"] == "undecided" and requested is None:
                result["offer"].append(name)
        except Exception:
            # Configuration may already have been saved. Report what is readable
            # now, but never turn a partial update into a successful selection.
            row.update(status="unavailable", error="feature_configuration_unavailable")
            try:
                _original, saved_choices = config.read_choices(skill)
                if name in saved_choices:
                    row.update(selection="on" if saved_choices[name] else "off", selection_source="saved")
                if name != "usage_collection":
                    _present, actual, _changed = _configuration(root, skill, name, None, True)
                    row["effective"] = "on" if actual else "off"
            except Exception:
                row["effective"] = "unknown"
    return result, hooks


def feature_notice(result):
    lines = []
    for name, row in result["features"].items():
        lines.append(f"{name}: selection={row['selection']}, effective={row['effective']}, {row['status']}")
    if result["offer"]:
        lines.append("Optional choices (ON/OFF or defer): " + ", ".join(result["offer"]))
    if result["features"].get("review_wait", {}).get("effective") == "on":
        lines.append("Review wait: local policy enabled; host MCP configuration, authorization and connection remain separate. See references/review_wait.md#setup-and-connection.")
    return "\n".join(lines)
