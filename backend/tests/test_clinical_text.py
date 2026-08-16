import hashlib
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient


CHINESE_NOTE = """姓名：测试甲
68岁男性，急诊就诊。
主诉：发热、咳嗽2天
现病史：2天前受凉后出现发热，最高39.1℃，伴咳嗽、黄痰。
暴露史：近期无旅行史，有家人流感样症状接触史。
既往史：高血压病10年，无器官移植史。
用药史：院外未使用抗菌药物。
生命体征：体温 38.6℃，心率 112次/分，呼吸 24次/分，血压 126/72 mmHg，SpO2 92%。
实验室检查：采样时间 2026-08-06 08:10，报告时间 2026-08-06 08:40，WBC 15.2×10^9/L↑，CRP 126 mg/L↑。
影像学检查：检查时间 2026-08-06 08:20，报告返回时间 2026-08-06 08:50，胸部CT示右下肺实变。"""


def _table_counts(client: TestClient) -> dict:
    db = client.app.state.db
    tables = ("cases", "clinical_events", "runs", "run_model_outputs", "run_events", "evaluations", "audit_log")
    return {table: db.fetchone("SELECT COUNT(*) AS n FROM %s" % table)["n"] for table in tables}


def test_chinese_note_is_locally_organized_and_fully_preserved(client: TestClient) -> None:
    decision_time = "2026-08-06T09:00:00+08:00"
    before = _table_counts(client)

    class BombProvider:
        calls = 0

        async def invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise AssertionError("clinical text compiler must not call a provider")

    bomb = BombProvider()
    client.app.state.provider_client = bomb
    response = client.post("/api/clinical-text/organize", json={
        "text": CHINESE_NOTE, "decision_time": decision_time, "source": "local-paste-test",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    draft = data["case_draft"]

    assert data["persistence"] == "none"
    assert data["parser_version"].startswith("owlpath-clinical-text-rules-")
    assert data["source_text_sha256"] == hashlib.sha256(CHINESE_NOTE.encode("utf-8")).hexdigest()
    assert draft["deidentified_note"] == CHINESE_NOTE
    assert draft["demographics"]["age"] == 68.0
    assert draft["demographics"]["sex"] == "male"
    assert draft["demographics"]["encounterType"] == "emergency"
    assert draft["scenario"] == "lower_respiratory"
    assert draft["history"]["chiefComplaint"] == "发热、咳嗽2天"
    assert "高血压" in draft["host"]["comorbidities"]
    assert {item["name"] for item in draft["vitals"]} >= {"体温", "心率", "呼吸频率", "血压", "血氧饱和度"}
    assert {item["name"] for item in draft["labs"]} >= {"白细胞计数", "C反应蛋白"}
    assert draft["imaging"]["modality"] == "CT"
    assert "右下肺实变" in draft["imaging"]["report"]

    lab = next(item for item in draft["labs"] if item["name"] == "白细胞计数")
    assert lab["sampledAt"] == "2026-08-06T08:10:00+08:00"
    assert lab["availableAt"] == "2026-08-06T08:40:00+08:00"
    assert lab["availableTimeCertainty"] == "explicit"
    lab_event = next(item for item in data["events"] if item["kind"] == "lab")
    assert lab_event["visible_at"] == "2026-08-06T08:40:00+08:00"
    history_event = next(item for item in data["events"] if item["kind"] == "history")
    assert history_event["data"]["deidentified_note"] == CHINESE_NOTE
    serialized_events = json.dumps(data["events"], ensure_ascii=False)
    restored_events = json.loads(serialized_events)
    restored_history = next(item for item in restored_events if item["kind"] == "history")
    assert restored_history["data"]["deidentified_note"] == CHINESE_NOTE

    warning_codes = {item["code"] for item in data["warnings"]}
    assert "possible_direct_identifier_name" in warning_codes
    assert bomb.calls == 0
    assert _table_counts(client) == before


def test_missing_report_time_uses_decision_time_and_marks_uncertainty(client: TestClient) -> None:
    note = "患者45岁女性。发热咳嗽。WBC 14.2×10^9/L，CRP 112 mg/L，胸部CT示左下肺斑片影。"
    decision_time = "2026-08-06T09:00:00+08:00"
    response = client.post("/api/clinical-text/organize", json={
        "text": note, "decision_time": decision_time, "source": "clipboard",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["case_draft"]["deidentified_note"] == note
    lab = data["case_draft"]["labs"][0]
    assert lab["availableAt"] == decision_time
    assert lab["availableTimeCertainty"] == "uncertain_assumed_decision_time"
    abnormal = {item["name"]: item["abnormal"] for item in data["case_draft"]["labs"]}
    assert abnormal["白细胞计数"] == "unknown"
    assert abnormal["C反应蛋白"] == "unknown"
    event = next(item for item in data["events"] if item["kind"] == "lab")
    assert event["visible_at"] == decision_time
    assert event["time_certainty"] == "uncertain_assumed_decision_time"
    warning_codes = {item["code"] for item in data["warnings"]}
    assert "lab_visible_time_uncertain" in warning_codes
    assert "imaging_visible_time_uncertain" in warning_codes


def test_sex_before_age_is_parsed_without_crashing(client: TestClient) -> None:
    decision_time = "2026-08-06T09:00:00+08:00"
    male = client.post("/api/clinical-text/organize", json={
        "text": "男，68岁。发热、咳嗽2天。",
        "decision_time": decision_time,
        "source": "synthetic-regression",
    })
    female = client.post("/api/clinical-text/organize", json={
        "text": "女性 45岁。发热、咳嗽2天。",
        "decision_time": decision_time,
        "source": "synthetic-regression",
    })

    assert male.status_code == 200, male.text
    assert female.status_code == 200, female.text
    assert male.json()["case_draft"]["demographics"]["sex"] == "male"
    assert male.json()["case_draft"]["demographics"]["age"] == 68.0
    assert female.json()["case_draft"]["demographics"]["sex"] == "female"
    assert female.json()["case_draft"]["demographics"]["age"] == 45.0


@pytest.mark.parametrize("note", [
    "性别：男\n年龄：68岁\n主诉：发热咳嗽2天",
    "68岁，女，咳嗽伴气促。",
    "男性\n68岁\n急诊就诊。",
    "女 6月龄，发热。",
    "女性，3天龄；鼻塞。",
    "患者130岁男性，无明显不适。",
    "患者131岁女性，年龄应降级为未识别。",
    "报告时间 2026-13-99 25:99，WBC 14.2×10^9/L。",
    "检验：WBC -2.0×10^9/L，CRP 999999 mg/L；无报告时间。",
    "No structured section. 68-year-old male with fever and cough.",
    "🩺。，；\nCT提示右下肺实变，其余字段缺失。",
    "主诉：\n现病史：\n生命体征：SpO₂ 92%。",
])
def test_common_messy_note_formats_degrade_without_internal_error(
    client: TestClient, note: str
) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": note,
        "decision_time": "2026-08-06T09:00:00+08:00",
        "source": "synthetic-format-matrix",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["persistence"] == "none"
    assert data["case_draft"]["deidentified_note"] == note
    assert data["source_text_sha256"] == hashlib.sha256(note.encode("utf-8")).hexdigest()


def test_age_decimal_months_and_age_before_sex_punctuation_are_parsed_safely(
    client: TestClient,
) -> None:
    decision_time = "2026-08-06T09:00:00+08:00"
    expected = [
        ("患者1.5岁男性，发热。", 1.5, "male"),
        ("68岁,女性;咳嗽。", 68.0, "female"),
        ("女性 6个月，发热。", 0.5, "female"),
        ("男婴，6个月，发热。", 0.5, "male"),
        ("女童 3岁，咳嗽。", 3.0, "female"),
    ]
    for note, age, sex in expected:
        response = client.post("/api/clinical-text/organize", json={
            "text": note,
            "decision_time": decision_time,
            "source": "synthetic-age-regression",
        })
        assert response.status_code == 200, response.text
        demographics = response.json()["case_draft"]["demographics"]
        assert demographics["age"] == age
        assert demographics["sex"] == sex


def test_unsupported_lab_numbers_are_not_partially_extracted(client: TestClient) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": "检验：CRP 12,5 mg/L；WBC 1.2e1×10^9/L；PCT 2.1 ng/mL。",
        "decision_time": "2026-08-06T09:00:00+08:00",
        "source": "synthetic-lab-regression",
    })
    assert response.status_code == 200, response.text
    labs = response.json()["case_draft"]["labs"]
    assert [(item["name"], item["value"]) for item in labs] == [("降钙素原", "2.1")]


def test_labeled_time_skips_invalid_candidate_and_uses_next_valid_one(client: TestClient) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": "检验：报告时间 2026-13-40 25:61；报告时间 2026-08-06 08:40；WBC 12.0×10^9/L。",
        "decision_time": "2026-08-06T09:00:00+08:00",
        "source": "synthetic-time-regression",
    })
    assert response.status_code == 200, response.text
    lab = response.json()["case_draft"]["labs"][0]
    assert lab["availableAt"] == "2026-08-06T08:40:00+08:00"
    assert lab["availableTimeCertainty"] == "explicit"


def test_cross_year_shorthand_date_is_treated_as_ambiguous(client: TestClient) -> None:
    decision_time = "2026-01-01T09:00:00+08:00"
    response = client.post("/api/clinical-text/organize", json={
        "text": "检验：报告时间 12月31日 23:00；WBC 12.0×10^9/L。",
        "decision_time": decision_time,
        "source": "synthetic-cross-year-regression",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    lab = data["case_draft"]["labs"][0]
    assert lab["availableAt"] == decision_time
    assert lab["availableTimeCertainty"] == "uncertain_assumed_decision_time"
    codes = {item["code"] for item in data["warnings"]}
    assert "lab_visible_time_uncertain" in codes
    assert "future_timestamp_in_text" not in codes


def test_ct_token_does_not_match_letters_inside_an_english_word(client: TestClient) -> None:
    decision_time = "2026-08-06T09:00:00+08:00"
    ordinary = client.post("/api/clinical-text/organize", json={
        "text": "No structured section. The patient has fever and cough.",
        "decision_time": decision_time,
        "source": "synthetic-ct-boundary",
    })
    explicit_ct = client.post("/api/clinical-text/organize", json={
        "text": "Chest CT shows right lower lobe consolidation.",
        "decision_time": decision_time,
        "source": "synthetic-ct-boundary",
    })

    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["case_draft"]["imaging"]["report"] == ""
    assert not any(item["kind"] == "imaging_report" for item in ordinary.json()["events"])
    assert explicit_ct.status_code == 200, explicit_ct.text
    assert explicit_ct.json()["case_draft"]["imaging"]["modality"] == "CT"
    assert "Chest CT" in explicit_ct.json()["case_draft"]["imaging"]["report"]


def test_lab_abnormality_requires_an_explicit_adjacent_marker(client: TestClient) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": "检验：WBC 14.2×10^9/L↑，CRP 112 mg/L降低，PCT 2.1 ng/mL。",
        "decision_time": "2026-08-06T09:00:00+08:00", "source": "paste",
    })
    assert response.status_code == 200
    abnormal = {item["name"]: item["abnormal"] for item in response.json()["case_draft"]["labs"]}
    assert abnormal["白细胞计数"] == "high"
    assert abnormal["C反应蛋白"] == "low"
    assert abnormal["降钙素原"] == "unknown"


def test_explicit_post_decision_report_time_is_not_backdated(client: TestClient) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": "检验结果：采样时间 2026-08-06 08:30，报告返回时间 2026-08-06 14:00，PCT 3.2 ng/mL。",
        "decision_time": "2026-08-06T09:00:00+08:00", "source": "lab-paste",
    })
    assert response.status_code == 200
    data = response.json()
    lab = data["case_draft"]["labs"][0]
    assert lab["availableAt"] == "2026-08-06T14:00:00+08:00"
    event = next(item for item in data["events"] if item["kind"] == "lab")
    assert event["visible_at"] == "2026-08-06T14:00:00+08:00"
    assert "lab_after_decision_time" in {item["code"] for item in data["warnings"]}


def test_text_limit_uses_uniform_safe_validation_error(client: TestClient) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": "敏" * 30001, "decision_time": datetime.now().astimezone().isoformat(), "source": "paste",
    })
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "敏" * 100 not in response.text


def test_unicode_spo2_is_recognized_and_sepsis_does_not_imply_bloodstream(client: TestClient) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": "68岁男性，脓毒症，SpO₂ 92%，尚未明确感染部位。",
        "decision_time": "2026-08-06T09:00:00+08:00",
        "source": "clipboard",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["case_draft"]["scenario"] == "undifferentiated"
    assert any(item["name"] == "血氧饱和度" and item["value"] == "92" for item in data["case_draft"]["vitals"])


def test_host_scope_flags_are_conservative_and_negation_aware(client: TestClient) -> None:
    positive = client.post("/api/clinical-text/organize", json={
        "text": "35岁女性，妊娠18周，器官移植后使用免疫抑制剂。",
        "decision_time": "2026-08-06T09:00:00+08:00", "source": "clipboard",
    }).json()["case_draft"]["demographics"]
    negative = client.post("/api/clinical-text/organize", json={
        "text": "68岁男性，否认器官移植史，未使用免疫抑制剂。",
        "decision_time": "2026-08-06T09:00:00+08:00", "source": "clipboard",
    }).json()["case_draft"]["demographics"]
    assert positive["pregnant"] is True and positive["immunocompromised"] is True
    assert negative["pregnant"] is None and negative["immunocompromised"] is False


def test_future_timestamp_and_pathogen_result_are_flagged_as_leakage(client: TestClient) -> None:
    response = client.post("/api/clinical-text/organize", json={
        "text": "上午08:30就诊；下午14:00 PCR报告阳性。",
        "decision_time": "2026-08-06T09:00:00+08:00",
        "source": "clipboard",
    })
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["warnings"]}
    assert "future_timestamp_in_text" in codes
    assert "possible_pathogen_label_leakage" in codes


def test_atomic_fact_preview_handles_chinese_and_english_negation(client: TestClient) -> None:
    now = "2026-08-06T09:00:00+08:00"
    response = client.post("/api/clinical-facts/preview", json={
        "events": [{
            "kind": "history",
            "occurred_at": now,
            "visible_at": now,
            "source": "clinician-reviewed",
            "status": "final",
            "data": {
                "present_illness": (
                    "不伴发热但有咳嗽，未使用抗生素。"
                    "The patient denies dyspnea but possible diarrhea."
                )
            },
            "quality": {"clinician_reviewed": True, "free_text_note": "must-not-cross"},
        }],
    })
    assert response.status_code == 200, response.text
    fact_event = response.json()["facts"][0]
    facts = {item["code"]: item for item in fact_event["data"]["clinical_facts"]}
    assert facts["fever"]["status"] == "absent"
    assert facts["cough"]["status"] == "present"
    assert facts["prior_antimicrobial_exposure"]["status"] == "absent"
    assert facts["dyspnea"]["status"] == "absent"
    assert facts["diarrhea"]["status"] == "unknown"
    assert "free_text_note" not in fact_event["quality"]
    assert fact_event["quality"]["raw_provenance_excluded"] is True


def test_atomic_fact_preview_rejects_unit_and_numeric_smuggling(client: TestClient) -> None:
    now = "2026-08-06T09:00:00+08:00"
    response = client.post("/api/clinical-facts/preview", json={
        "events": [
            {
                "kind": "lab", "occurred_at": now, "visible_at": now,
                "source": "manual", "data": {"test_name": "WBC", "value": "12", "unit": "MRSA"},
            },
            {
                "kind": "vital", "occurred_at": now, "visible_at": now,
                "source": "manual", "data": {"observation": "SpO2", "value": "92", "unit": "PATIENT42"},
            },
            {
                "kind": "lab", "occurred_at": now, "visible_at": now,
                "source": "manual", "data": {"test_name": "WBC", "value": "9876543210", "unit": "10^9/L"},
            },
        ],
    })
    assert response.status_code == 200, response.text
    assert response.json()["facts"] == []
    assert response.json()["excluded_event_indexes"] == [0, 1, 2]
    assert "MRSA" not in response.text
    assert "PATIENT42" not in response.text
    assert "9876543210" not in response.text


def test_atomic_fact_preview_canonicalizes_units_and_broad_safe_ranges(client: TestClient) -> None:
    now = "2026-08-06T09:00:00+08:00"
    response = client.post("/api/clinical-facts/preview", json={
        "events": [
            {
                "kind": "lab", "occurred_at": now, "visible_at": now,
                "source": "manual", "data": {"test_name": "HGB", "value": "12", "unit": "g/dL"},
            },
            {
                "kind": "vital", "occurred_at": now, "visible_at": now,
                "source": "manual", "data": {"observation": "T", "value": "98.6", "unit": "°F"},
            },
            {
                "kind": "vital", "occurred_at": now, "visible_at": now,
                "source": "manual", "data": {"observation": "BP", "value": "126/72", "unit": "mmHg"},
            },
        ],
    })
    assert response.status_code == 200, response.text
    facts = response.json()["facts"]
    assert facts[0]["data"] == {"test_code": "hemoglobin", "value": "120", "unit": "g/L"}
    assert facts[1]["data"] == {"observation_code": "temperature", "value": "37", "unit": "degC"}
    assert facts[2]["data"] == {"observation_code": "blood_pressure", "value": "126/72", "unit": "mmHg"}
