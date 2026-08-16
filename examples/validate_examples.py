#!/usr/bin/env python3
"""Validate OwlPath's synthetic research contract examples using stdlib only.

This is an acceptance check for file structure, time visibility, output-state
invariants, probability accounting, and safety boundaries. It does not test
clinical validity or model performance.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
CASES = ROOT / "examples" / "cases"
OUTPUTS = ROOT / "examples" / "outputs"


class ValidationFailure(Exception):
    pass


def fail(message: str) -> None:
    raise ValidationFailure(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"{path.relative_to(ROOT)}: JSON 解析失败: {exc}")


def parse_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        fail(f"{label}: 无效 ISO 8601 时间: {value!r} ({exc})")
    if parsed.tzinfo is None:
        fail(f"{label}: 时间必须包含时区: {value!r}")
    return parsed


def close_to_one(values: list[float], label: str) -> None:
    if not math.isclose(sum(values), 1.0, abs_tol=1e-8):
        fail(f"{label}: 概率之和应为 1，实际为 {sum(values):.12g}")


def schema_type_ok(instance: Any, expected: str) -> bool:
    return {
        "null": instance is None,
        "boolean": isinstance(instance, bool),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "number": isinstance(instance, (int, float)) and not isinstance(instance, bool),
        "string": isinstance(instance, str),
        "array": isinstance(instance, list),
        "object": isinstance(instance, dict),
    }.get(expected, False)


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        fail(f"Schema 使用了测试器不支持的外部引用: {ref}")
    node: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def schema_errors(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON-Schema keywords used by this repository."""
    errors: list[str] = []
    if "$ref" in schema:
        return schema_errors(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: 应等于 {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} 不在允许枚举中")

    if "type" in schema:
        expected_types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(schema_type_ok(instance, item) for item in expected_types):
            errors.append(f"{path}: 类型不符，应为 {expected_types}")
            return errors

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: 缺少必填字段 {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(schema_errors(value, properties[key], root_schema, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: 出现未允许字段 {key!r}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: 项数少于 {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: 项数多于 {schema['maxItems']}")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in instance]
            if len(canonical) != len(set(canonical)):
                errors.append(f"{path}: 数组项目不唯一")
        item_schema = schema.get("items")
        if item_schema:
            for index, value in enumerate(instance):
                errors.extend(schema_errors(value, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: 字符串短于 {schema['minLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: 不符合模式 {schema['pattern']!r}")
        if schema.get("format") == "date-time":
            try:
                parse_time(instance, path)
            except ValidationFailure as exc:
                errors.append(str(exc))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: 小于最小值 {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: 大于最大值 {schema['maximum']}")

    if "oneOf" in schema:
        matches = sum(
            not schema_errors(instance, option, root_schema, path)
            for option in schema["oneOf"]
        )
        if matches != 1:
            errors.append(f"{path}: oneOf 应恰好匹配一项，实际匹配 {matches} 项")

    for subschema in schema.get("allOf", []):
        errors.extend(schema_errors(instance, subschema, root_schema, path))

    if "if" in schema and not schema_errors(instance, schema["if"], root_schema, path):
        errors.extend(schema_errors(instance, schema.get("then", {}), root_schema, path))

    return errors


def walk_keys(value: Any, prefix: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append((key, f"{prefix}.{key}"))
            found.extend(walk_keys(child, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(walk_keys(child, f"{prefix}[{index}]"))
    return found


def validate_case(case: dict[str, Any], source: Path) -> None:
    label = str(source.relative_to(ROOT))
    if case.get("is_synthetic") is not True:
        fail(f"{label}: is_synthetic 必须为 true")
    if case.get("contains_real_patient_data") is not False:
        fail(f"{label}: contains_real_patient_data 必须为 false")
    if case.get("prohibited_real_world_use") is not True:
        fail(f"{label}: prohibited_real_world_use 必须为 true")
    decision_time = parse_time(case.get("decision_time"), f"{label}.decision_time")
    event_ids: set[str] = set()
    for event in case.get("events", []):
        event_id = event.get("event_id")
        if not event_id or event_id in event_ids:
            fail(f"{label}: event_id 缺失或重复: {event_id!r}")
        event_ids.add(event_id)
        visible_at = parse_time(event.get("clinician_visible_at"), f"{label}.{event_id}.clinician_visible_at")
        expected_eligible = visible_at <= decision_time
        if event.get("eligible_at_decision_time") is not expected_eligible:
            fail(f"{label}.{event_id}: 可见时间与 eligible_at_decision_time 不一致")
        if expected_eligible and event.get("exclusion_reason") is not None:
            fail(f"{label}.{event_id}: 决策时点前可见事件不应有排除原因")
        if not expected_eligible and event.get("exclusion_reason") != "visible_after_decision_time":
            fail(f"{label}.{event_id}: 决策时点后事件必须标记 visible_after_decision_time")


def validate_output(
    result: dict[str, Any],
    case: dict[str, Any],
    schema: dict[str, Any],
    intended_use: dict[str, Any],
    allowed_next_items: set[str],
    source: Path,
) -> None:
    label = str(source.relative_to(ROOT))
    errors = schema_errors(result, schema, schema)
    if errors:
        fail(f"{label}: 不符合 model_output.schema.json:\n  - " + "\n  - ".join(errors))

    for field in ("case_id", "decision_time", "snapshot_stage", "run_context"):
        if result[field] != case[field]:
            fail(f"{label}: {field} 与病例快照不一致")
    if result["main_state"] != case["expected_main_state"]:
        fail(f"{label}: main_state 与病例 expected_main_state 不一致")

    included = set(result["time_gate"]["included_event_ids"])
    excluded = {item["event_id"]: item["reason"] for item in result["time_gate"]["excluded_events"]}
    expected_included = {item["event_id"] for item in case["events"] if item["eligible_at_decision_time"]}
    expected_excluded = {
        item["event_id"]: item["exclusion_reason"]
        for item in case["events"]
        if not item["eligible_at_decision_time"]
    }
    if included != expected_included or excluded != expected_excluded:
        fail(f"{label}: time_gate 与病例事件可见性不一致")
    if result["time_gate"]["future_information_leakage_detected"]:
        fail(f"{label}: 合成验收输出不应含未来信息泄漏")

    infection = result["infection_assessment"]
    if infection["evaluated"]:
        close_to_one(
            [infection["infection_probability"], infection["non_infection_probability"]],
            f"{label}.infection_assessment",
        )
    elif infection["infection_probability"] is not None or infection["non_infection_probability"] is not None:
        fail(f"{label}: 未评估感染时概率必须为 null")

    pathogen = result["pathogen_assessment"]
    if pathogen["evaluated"]:
        expected_categories = {"bacterial", "viral", "fungal", "parasitic", "other_known"}
        if set(pathogen["category_probabilities"]) != expected_categories:
            fail(f"{label}: 已评估病原体时必须给出五个病原大类概率")
        close_to_one(
            list(pathogen["category_probabilities"].values()) + [pathogen["unknown_probability"]],
            f"{label}.pathogen_assessment",
        )
        if pathogen["probability_condition"] != "conditional_on_infection":
            fail(f"{label}: 病原概率必须明确为 conditional_on_infection")
    else:
        if pathogen["category_probabilities"] or pathogen["unknown_probability"] is not None:
            fail(f"{label}: 未评估病原体时不得伪造病原概率")
        if pathogen["coinfection_probability"] is not None or pathogen["probability_condition"] != "not_evaluated":
            fail(f"{label}: 未评估病原体时条件和共感染概率应为空")

    ranks = [candidate["rank"] for candidate in pathogen["top_k"]]
    if ranks != list(range(1, len(ranks) + 1)):
        fail(f"{label}: top_k 排名必须从 1 连续递增")
    candidate_ids = {candidate["pathogen_id"] for candidate in pathogen["top_k"]}
    if not set(pathogen["prediction_set"]).issubset(candidate_ids):
        fail(f"{label}: prediction_set 必须来自 top_k")

    state = result["main_state"]
    if state == "species_set" and any(item["taxonomic_level"] != "species" for item in pathogen["top_k"]):
        fail(f"{label}: species_set 中只能出现物种级候选")
    if state == "category_only" and any(item["taxonomic_level"] != "category" for item in pathogen["top_k"]):
        fail(f"{label}: category_only 中只能出现大类候选")
    if state == "more_information_needed" and result["next_information"] is None:
        fail(f"{label}: more_information_needed 必须给出一个信息价值建议")
    if state == "abstain" and result["scope"]["status"] != "out_of_scope":
        fail(f"{label}: 本验收场景的 abstain 应由超范围触发")

    next_information = result["next_information"]
    if next_information is not None:
        if next_information["item_id"] not in allowed_next_items:
            fail(f"{label}: 下一项信息不在专家白名单中")
        if next_information["not_an_order"] is not True:
            fail(f"{label}: 下一项信息必须明确不是医嘱")

    evidence_fact_ids: set[str] = set()
    for polarity in ("supporting", "contradicting"):
        for fact in result["evidence_summary"][polarity]:
            evidence_fact_ids.add(fact["fact_id"])
            if not set(fact["event_ids"]).issubset(included):
                fail(f"{label}: 证据引用了当前决策时点不可见事件")
    for candidate in pathogen["top_k"]:
        referenced = set(candidate["supporting_fact_ids"] + candidate["contradicting_fact_ids"])
        if not referenced.issubset(evidence_fact_ids):
            fail(f"{label}: 候选引用了不存在的 fact_id")

    if result["disclaimer"] != intended_use["mandatory_disclaimer"]:
        fail(f"{label}: 强制免责声明不一致")
    blocked_keys = {
        "drug",
        "dose",
        "dosage",
        "prescription",
        "medication_recommendation",
        "treatment_recommendation",
        "automated_order",
    }
    for key, path in walk_keys(result):
        if key.lower() in blocked_keys:
            fail(f"{label}: 出现禁止的诊疗字段 {path}")


def validate_local_prior(prior: dict[str, Any]) -> None:
    label = "config/local_priors.example.json"
    if prior.get("synthetic") is not True or prior.get("not_for_clinical_use") is not True:
        fail(f"{label}: 必须明确为纯合成且不可临床使用")
    provenance = prior.get("provenance", {})
    if provenance.get("contains_patient_level_data") is not False:
        fail(f"{label}: 示例不得包含患者级数据")
    if provenance.get("contains_model_predictions_as_ground_truth") is not False:
        fail(f"{label}: 不得把模型预测循环写回先验真值")
    close_to_one(list(prior["category_prior"].values()), f"{label}.category_prior")
    for category, items in prior["within_category_priors"].items():
        close_to_one(
            [item["probability_within_category"] for item in items],
            f"{label}.within_category_priors.{category}",
        )


def main() -> int:
    json_paths = sorted(CONFIG.glob("*.json")) + sorted(CASES.glob("*.json")) + sorted(OUTPUTS.glob("*.json"))
    if not json_paths:
        fail("未找到 JSON 文件")
    parsed = {path: load_json(path) for path in json_paths}

    intended_use = parsed[CONFIG / "intended_use.v1.json"]
    schema = parsed[CONFIG / "model_output.schema.json"]
    prior = parsed[CONFIG / "local_priors.example.json"]
    next_rules = parsed[CONFIG / "next_test_rules.example.json"]
    release_gates = parsed[CONFIG / "release_gates.v1.json"]

    expected_states = {
        "infection_unlikely",
        "species_set",
        "category_only",
        "more_information_needed",
        "abstain",
    }
    if set(intended_use["permitted_primary_states"]) != expected_states:
        fail("intended_use 的五态契约不完整")
    if set(schema["properties"]["main_state"]["enum"]) != expected_states:
        fail("model_output.schema.json 的五态枚举与适用范围契约不一致")
    if set(release_gates["required_main_state_test_coverage"]) != expected_states:
        fail("release_gates 的五态覆盖要求不完整")

    case_files = sorted(CASES.glob("*.json"))
    output_files = sorted(OUTPUTS.glob("*.json"))
    if len(case_files) < 5:
        fail("至少需要五个纯合成病例，以覆盖五种主输出状态")
    cases: dict[str, dict[str, Any]] = {}
    for path in case_files:
        case = parsed[path]
        validate_case(case, path)
        if case["case_id"] in cases:
            fail(f"病例 ID 重复: {case['case_id']}")
        cases[case["case_id"]] = case

    allowed_next_items = {item["item_id"] for item in next_rules["allowlist"]}
    outputs_seen: set[str] = set()
    states_seen: set[str] = set()
    for path in output_files:
        result = parsed[path]
        case_id = result.get("case_id")
        if case_id not in cases:
            fail(f"{path.relative_to(ROOT)}: 找不到配对病例 {case_id!r}")
        if case_id in outputs_seen:
            fail(f"输出病例 ID 重复: {case_id}")
        validate_output(result, cases[case_id], schema, intended_use, allowed_next_items, path)
        outputs_seen.add(case_id)
        states_seen.add(result["main_state"])
    if outputs_seen != set(cases):
        fail(f"病例与输出未一一对应，缺少: {sorted(set(cases) - outputs_seen)}")
    if states_seen != expected_states:
        fail(f"五种主状态未全部覆盖，实际为: {sorted(states_seen)}")

    validate_local_prior(prior)
    print(f"PASS: parsed {len(json_paths)} JSON files")
    print(f"PASS: {len(cases)} synthetic cases and outputs are one-to-one")
    print("PASS: all five primary states are covered")
    print("PASS: current-decision-time visibility and no-leakage checks passed")
    print("PASS: probabilities, evidence links, allowlist, and safety boundaries passed")
    print("NOTE: this validates the research contract, not clinical performance or safety")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
