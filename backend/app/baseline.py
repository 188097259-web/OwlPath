import json
from typing import Any, Dict, List, Tuple

from .models import (
    ModelPrediction,
    NextTestSuggestion,
    PathogenCandidate,
    RankLevel,
)


DISCLAIMER = "内置规则仅用于无 Key 的产品联调，未经临床验证；其分数不是校准概率，不能用于独立诊疗决策。"


def _text(snapshot: Dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, default=str).lower()


def _has(text: str, terms: List[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _candidate(
    canonical_id: str,
    name: str,
    category: str,
    genus: str,
    species: str,
    probability: float,
    evidence: str,
) -> PathogenCandidate:
    return PathogenCandidate(
        canonical_id=canonical_id,
        name=name,
        rank_level=RankLevel.SPECIES,
        category=category,
        genus=genus,
        species=species,
        probability=probability,
        calibration_status="heuristic_unvalidated",
        evidence_for=[evidence],
        evidence_against=["未纳入本地病原谱、培养/PCR 结果及完整宿主因素"],
    )


def _respiratory(text: str) -> Tuple[List[PathogenCandidate], List[NextTestSuggestion]]:
    bacterial = _has(text, ["实变", "consolidation", "中性粒", "neutroph", "脓痰", "lobar"])
    viral = _has(text, ["流感样", "myalgia", "肌痛", "ground-glass", "磨玻璃", "接触史"])
    candidates = [
        _candidate("taxon:1313", "Streptococcus pneumoniae", "bacteria", "Streptococcus", "Streptococcus pneumoniae", 0.31 if bacterial else 0.23, "呼吸道综合征规则"),
        _candidate("taxon:11320", "Influenza A virus", "virus", "Alphainfluenzavirus", "Influenza A virus", 0.23 if viral else 0.16, "呼吸道综合征规则"),
        _candidate("taxon:2697049", "SARS-CoV-2", "virus", "Betacoronavirus", "SARS-CoV-2", 0.19 if viral else 0.13, "呼吸道综合征规则"),
        _candidate("taxon:1280", "Staphylococcus aureus", "bacteria", "Staphylococcus", "Staphylococcus aureus", 0.12, "重症肺部感染备选"),
        _candidate("taxon:727", "Haemophilus influenzae", "bacteria", "Haemophilus", "Haemophilus influenzae", 0.11, "呼吸道综合征规则"),
    ]
    tests = [NextTestSuggestion(
        test_code="respiratory-multiplex-naat",
        test_name="呼吸道多重核酸检测",
        specimen="规范采集的呼吸道标本",
        rationale="可较快区分常见病毒和部分非典型病原；结果必须结合标本质量、宿主状态及定植可能解释。",
        expected_information_gain=0.75,
        estimated_turnaround="依本地实验室而定",
        burden="low",
    )]
    return candidates, tests


def _urinary(text: str) -> Tuple[List[PathogenCandidate], List[NextTestSuggestion]]:
    candidates = [
        _candidate("taxon:562", "Escherichia coli", "bacteria", "Escherichia", "Escherichia coli", 0.42, "泌尿系统感染基础规则"),
        _candidate("taxon:573", "Klebsiella pneumoniae", "bacteria", "Klebsiella", "Klebsiella pneumoniae", 0.18, "泌尿系统感染基础规则"),
        _candidate("taxon:1351", "Enterococcus faecalis", "bacteria", "Enterococcus", "Enterococcus faecalis", 0.14, "泌尿系统感染基础规则"),
        _candidate("taxon:287", "Pseudomonas aeruginosa", "bacteria", "Pseudomonas", "Pseudomonas aeruginosa", 0.11, "复杂性尿路感染备选"),
        _candidate("taxon:1280", "Staphylococcus aureus", "bacteria", "Staphylococcus", "Staphylococcus aureus", 0.05, "低概率备选"),
    ]
    tests = [NextTestSuggestion(
        test_code="urine-culture-ast",
        test_name="规范尿培养及药敏",
        specimen="中段清洁尿或按规范采集的导管尿",
        rationale="直接缩小病原范围并提供药敏，但须结合菌落计数、症状和采样方式判断定植或污染。",
        expected_information_gain=0.82,
        estimated_turnaround="依本地实验室而定",
        burden="low",
    )]
    return candidates, tests


def _bloodstream(text: str) -> Tuple[List[PathogenCandidate], List[NextTestSuggestion]]:
    candidates = [
        _candidate("taxon:1280", "Staphylococcus aureus", "bacteria", "Staphylococcus", "Staphylococcus aureus", 0.20, "血流感染综合征规则"),
        _candidate("taxon:562", "Escherichia coli", "bacteria", "Escherichia", "Escherichia coli", 0.20, "血流感染综合征规则"),
        _candidate("taxon:573", "Klebsiella pneumoniae", "bacteria", "Klebsiella", "Klebsiella pneumoniae", 0.16, "血流感染综合征规则"),
        _candidate("taxon:1351", "Enterococcus faecalis", "bacteria", "Enterococcus", "Enterococcus faecalis", 0.11, "血流感染综合征规则"),
        _candidate("taxon:5476", "Candida albicans", "fungus", "Candida", "Candida albicans", 0.08, "特定高危宿主备选，当前规则未完整核实风险因素"),
    ]
    tests = [NextTestSuggestion(
        test_code="paired-blood-cultures",
        test_name="规范采集多套血培养",
        specimen="不同静脉穿刺点血液",
        rationale="可确认菌血症/真菌血症并支持药敏；采集时机、血量和污染控制决定信息价值。",
        expected_information_gain=0.86,
        estimated_turnaround="初步与最终报告时间依实验室而定",
        burden="moderate",
    )]
    return candidates, tests


def _cns(text: str) -> Tuple[List[PathogenCandidate], List[NextTestSuggestion]]:
    candidates = [
        _candidate("taxon:1313", "Streptococcus pneumoniae", "bacteria", "Streptococcus", "Streptococcus pneumoniae", 0.25, "中枢神经系统感染基础规则"),
        _candidate("taxon:487", "Neisseria meningitidis", "bacteria", "Neisseria", "Neisseria meningitidis", 0.18, "中枢神经系统感染基础规则"),
        _candidate("taxon:10298", "Herpes simplex virus 1", "virus", "Simplexvirus", "Herpes simplex virus 1", 0.15, "脑炎备选"),
        _candidate("taxon:10376", "Herpes simplex virus 2", "virus", "Simplexvirus", "Herpes simplex virus 2", 0.10, "脑膜脑炎备选"),
        _candidate("taxon:1352", "Enterococcus faecium", "bacteria", "Enterococcus", "Enterococcus faecium", 0.05, "医疗相关感染低概率备选"),
    ]
    tests = [NextTestSuggestion(
        test_code="csf-standard-plus-naat",
        test_name="脑脊液常规、生化、培养及指征明确的核酸检测",
        specimen="脑脊液",
        rationale="若临床评估无腰穿禁忌，可同时评估炎症类型并缩小细菌/病毒病原范围。",
        expected_information_gain=0.88,
        estimated_turnaround="依项目和本地实验室而定",
        burden="high",
    )]
    return candidates, tests


def predict_baseline(snapshot: Dict[str, Any]) -> ModelPrediction:
    text = _text(snapshot)
    context = snapshot.get("case", {}).get("context", {})
    primary = str(context.get("primary_syndrome") or "").lower()
    if primary in {"respiratory", "lower_respiratory"} or _has(text, ["咳嗽", "肺炎", "sputum", "cough", "pneumonia"]):
        syndrome = "respiratory"
        candidates, tests = _respiratory(text)
    elif primary in {"urinary", "urinary_tract"} or _has(text, ["尿频", "尿痛", "pyuria", "dysuria", "urinary"]):
        syndrome = "urinary"
        candidates, tests = _urinary(text)
    elif primary in {"central_nervous_system", "cns"} or _has(text, ["脑膜", "脑炎", "mening", "encephal"]):
        syndrome = "central_nervous_system"
        candidates, tests = _cns(text)
    else:
        syndrome = "bloodstream" if primary == "bloodstream" or _has(text, ["菌血症", "sepsis", "bloodstream"]) else "other"
        candidates, tests = _bloodstream(text)

    infection_clues = _has(text, ["发热", "fever", "中性粒", "neutroph", "crp", "pct", "实变", "脓"])
    immunocompromised = bool(snapshot.get("case", {}).get("demographics", {}).get("immunocompromised"))
    unknown = 0.38 + (0.10 if immunocompromised else 0.0)
    warning = [DISCLAIMER, "规则未读取经验证的本地流行病学先验，不能推导耐药性或推荐用药。"]
    if not snapshot.get("events"):
        unknown = max(unknown, 0.65)
        warning.append("决策时点前没有可见临床事件。")
    return ModelPrediction(
        summary="透明规则基线识别为 %s 综合征；仅供工程联调和与外部模型对照。" % syndrome,
        infection_probability=0.68 if infection_clues else 0.52,
        syndrome_probabilities={syndrome: 0.72, "other": 0.28 if syndrome != "other" else 0.72},
        candidates=sorted(candidates, key=lambda item: item.probability, reverse=True),
        coinfection_probability=0.12,
        coinfection_pairs=[],
        unknown_probability=min(1.0, unknown),
        next_tests=tests,
        data_quality_warnings=warning,
        distribution_shift_warning=immunocompromised,
        abstain=not bool(snapshot.get("events")),
        abstain_reason="决策时点前无可用事件" if not snapshot.get("events") else None,
    )
