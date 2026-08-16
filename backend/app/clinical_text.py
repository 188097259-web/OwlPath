import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import (
    ClinicalTextCaseDraft,
    ClinicalTextDemographics,
    ClinicalTextHistory,
    ClinicalTextHost,
    ClinicalTextImaging,
    ClinicalTextLab,
    ClinicalTextOrganizeRequest,
    ClinicalTextOrganizeResponse,
    ClinicalTextVital,
    CompilerWarning,
    EventKind,
    EventStatus,
    OrganizedClinicalEvent,
)


PARSER_VERSION = "owlpath-clinical-text-rules-1.2.0"


SECTION_ALIASES: Dict[str, str] = {
    "主诉": "chief_complaint",
    "现病史": "present_illness",
    "病史摘要": "present_illness",
    "暴露史": "exposure_history",
    "接触史": "exposure_history",
    "旅行史": "exposure_history",
    "流行病学史": "epidemiology",
    "流行病学": "epidemiology",
    "既往史": "past_history",
    "基础疾病": "past_history",
    "合并症": "past_history",
    "用药史": "medications",
    "抗菌药物史": "medications",
    "抗感染用药": "medications",
    "过敏史": "allergies",
    "手术操作": "procedures",
    "侵入性操作": "procedures",
    "管路": "procedures",
    "影像学检查": "imaging",
    "影像检查": "imaging",
    "影像学": "imaging",
    "胸部CT": "imaging",
    "影像": "imaging",
    "实验室检查": "laboratory",
    "辅助检查": "laboratory",
    "检验结果": "laboratory",
    "血常规": "laboratory",
    "检验": "laboratory",
    "生命体征": "vitals",
    "体格检查": "vitals",
    "查体": "vitals",
}


SECTION_PATTERN = re.compile(
    r"(?m)(?:^|[\r\n；;。])\s*(?P<header>%s)\s*(?:[:：]|(?=\r?$))"
    % "|".join(re.escape(item) for item in sorted(SECTION_ALIASES, key=len, reverse=True)),
    flags=re.IGNORECASE,
)
SECTION_ALIASES_CASEFOLD = {key.casefold(): value for key, value in SECTION_ALIASES.items()}


DATETIME_TOKEN = (
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}(?::\d{2})?"
    r"|\d{1,2}:\d{2}(?::\d{2})?)"
)


PII_PATTERNS: Sequence[Tuple[str, str, str]] = (
    ("possible_direct_identifier_name", r"(?:患者姓名|姓名)\s*[:：]\s*[\u4e00-\u9fff·]{2,20}", "文本可能包含姓名"),
    ("possible_direct_identifier_phone", r"(?<!\d)1[3-9]\d{9}(?!\d)|(?:电话|手机号|手机)\s*[:：]?\s*\d{7,}", "文本可能包含电话号码"),
    ("possible_direct_identifier_national_id", r"(?<!\d)\d{17}[\dXx](?!\w)|(?:身份证|证件号)\s*[:：]?\s*[\dXx-]{8,}", "文本可能包含身份证或证件号码"),
    ("possible_direct_identifier_record_number", r"(?:住院号|门诊号|病案号|病历号|MRN)\s*[:：]?\s*[A-Za-z0-9-]{4,}", "文本可能包含就诊或病案编号"),
    ("possible_direct_identifier_address", r"(?:住址|家庭地址|地址)\s*[:：]\s*[^\n；;]{4,}", "文本可能包含地址"),
)


LAB_RULES: Sequence[Tuple[str, str]] = (
    ("白细胞计数", r"白细胞(?:计数)?|WBC"),
    ("中性粒细胞百分比", r"中性粒细胞(?:百分比|比率)?|NEUT%?|Neu%?"),
    ("淋巴细胞百分比", r"淋巴细胞(?:百分比|比率)?|LYM%?|Lym%?"),
    ("血红蛋白", r"血红蛋白|HGB|Hb"),
    ("血小板计数", r"血小板(?:计数)?|PLT"),
    ("C反应蛋白", r"超敏C反应蛋白|C反应蛋白|hs-?CRP|CRP"),
    ("降钙素原", r"降钙素原|PCT"),
    ("乳酸", r"血乳酸|乳酸|Lactate"),
    ("肌酐", r"血肌酐|肌酐|Cr|CREA"),
    ("丙氨酸氨基转移酶", r"丙氨酸氨基转移酶|谷丙转氨酶|ALT"),
    ("天冬氨酸氨基转移酶", r"天冬氨酸氨基转移酶|谷草转氨酶|AST"),
)


UNIT_PATTERN = (
    r"(?:×|x|X|\*)?\s*10\s*(?:\^)?\s*(?:3|6|9|12)\s*/\s*(?:L|l)"
    r"|%|℃|°C|mg\s*/\s*L|ng\s*/\s*mL|µg\s*/\s*L|μg\s*/\s*L"
    r"|g\s*/\s*L|mmol\s*/\s*L|µmol\s*/\s*L|μmol\s*/\s*L|U\s*/\s*L"
)


VITAL_RULES: Sequence[Tuple[str, str, str]] = (
    ("体温", r"(?:体温|(?<![A-Za-z])T(?![A-Za-z]))\s*[:：=]?\s*(\d{2}(?:\.\d+)?)\s*(?:℃|°C|C)?", "℃"),
    ("心率", r"(?:心率|脉搏|(?<![A-Za-z])HR(?![A-Za-z]))\s*[:：=]?\s*(\d{2,3})\s*(?:次/分|bpm)?", "次/分"),
    ("呼吸频率", r"(?:呼吸频率|呼吸|(?<![A-Za-z])RR(?![A-Za-z]))\s*[:：=]?\s*(\d{1,3})\s*(?:次/分|/min)?", "次/分"),
    ("血压", r"(?:血压|(?<![A-Za-z])BP(?![A-Za-z]))\s*[:：=]?\s*(\d{2,3}\s*/\s*\d{2,3})\s*(?:mmHg)?", "mmHg"),
    ("血氧饱和度", r"(?:血氧饱和度|血氧|SpO(?:2|₂))\s*[:：=]?\s*(\d{1,3}(?:\.\d+)?)\s*%?", "%"),
)


PATHOGEN_RESULT_PATTERN = re.compile(
    r"(?:PCR|核酸|mNGS|宏基因组|培养|病原学).{0,40}"
    r"(?:阳性|阴性|检出|未检出|分离|培养出|确诊)"
    r"|(?:检出|分离出|培养出).{0,30}(?:菌|病毒|衣原体|支原体|真菌)",
    flags=re.IGNORECASE,
)


def _add_warning(warnings: List[CompilerWarning], code: str, message: str, severity: str = "warning") -> None:
    if any(item.code == code for item in warnings):
        return
    warnings.append(CompilerWarning(code=code, message=message, severity=severity))


def clinical_text_safety_warnings(text: str, decision_time: datetime) -> List[CompilerWarning]:
    """Detect privacy, future-information and pathogen-label leakage locally."""
    warnings: List[CompilerWarning] = []
    for code, pattern, label in PII_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            _add_warning(
                warnings,
                code,
                "%s；本地编译器不会改写或删除原文，保存病例或发送外部服务前必须人工去标识化。" % label,
            )
    future_tokens: List[str] = []
    for match in re.finditer(DATETIME_TOKEN, text, flags=re.IGNORECASE):
        parsed = _parse_time_token(match.group(0), decision_time)
        if parsed is not None and parsed > decision_time:
            future_tokens.append(match.group(0))
    if future_tokens:
        _add_warning(
            warnings,
            "future_timestamp_in_text",
            "原文含有晚于 decision_time 的时间（%s）。为防止未来信息绕过时闸门，启动推演前必须删除该段、调整决策时点，或将其作为新一次运行。"
            % "、".join(dict.fromkeys(future_tokens)),
        )
    if PATHOGEN_RESULT_PATTERN.search(text):
        _add_warning(
            warnings,
            "possible_pathogen_label_leakage",
            "原文可能包含 PCR、培养、mNGS 或其他病原学结果。当前任务是根据早期非病原学证据进行推演，请先移除该结果以避免标签泄漏。",
        )
    return warnings


def _split_sections(text: str) -> Tuple[Dict[str, str], List[str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(SECTION_PATTERN.finditer(normalized))
    sections: Dict[str, str] = {key: "" for key in dict.fromkeys(SECTION_ALIASES.values())}
    unrecognized: List[str] = []
    if not matches:
        return sections, [normalized.strip()] if normalized.strip() else []
    prefix = normalized[:matches[0].start("header")].strip()
    if prefix.strip("。；;\n "):
        unrecognized.append(prefix)
    for index, match in enumerate(matches):
        key = SECTION_ALIASES_CASEFOLD[match.group("header").casefold()]
        end = matches[index + 1].start("header") if index + 1 < len(matches) else len(normalized)
        content = normalized[match.end():end].strip().lstrip(":：").strip()
        if content:
            sections[key] = (sections[key] + "\n" + content).strip()
    return sections, unrecognized


def _parse_age(text: str) -> Optional[float]:
    patterns = (
        r"(?:年龄|患者年龄)\s*[:：]?\s*(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)(岁|月龄|个月|天龄)",
        r"(?:患者\s*)?(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)(岁|月龄|个月|天龄)\s*[,，;；]?\s*(?:男性|女性|男婴|女婴|男童|女童|男|女)",
        r"(?:男性|女性|男婴|女婴|男童|女童|男|女)\s*[,，;； ]*\s*(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)(岁|月龄|个月|天龄)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = match.group(2)
        if unit in {"月龄", "个月"}:
            value /= 12.0
        elif unit == "天龄":
            value /= 365.25
        if 0 <= value <= 130:
            return round(value, 4)
    return None


def _parse_sex(text: str) -> str:
    patterns = (
        r"(?:性别)\s*[:：]?\s*(男性|女性|男婴|女婴|男童|女童|男|女)",
        r"\d{1,3}(?:\.\d{1,2})?(?:岁|月龄|个月|天龄)\s*[,，;； ]*\s*(男性|女性|男婴|女婴|男童|女童|男|女)",
        r"(男性|女性|男婴|女婴|男童|女童|男|女)\s*[,，;； ]*\s*\d{1,3}(?:\.\d{1,2})?(?:岁|月龄|个月|天龄)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return "female" if "女" in match.group(1) else "male"
    return "unknown"


def _parse_department(text: str) -> Optional[str]:
    match = re.search(r"(?:就诊科室|科室)\s*[:：]\s*([^\n，,；;]{2,30})", text)
    return match.group(1).strip() if match else None


def _encounter_type(text: str) -> Optional[str]:
    if re.search(r"\bICU\b|重症监护|重症医学", text, flags=re.IGNORECASE):
        return "icu"
    if "急诊" in text:
        return "emergency"
    if "门诊" in text:
        return "outpatient"
    if re.search(r"住院|入院", text):
        return "inpatient"
    return None


def _acquisition_context(text: str) -> str:
    if re.search(r"呼吸机相关|\bVAP\b|医院获得|院内感染|住院\s*(?:48小时|2天)后", text, flags=re.IGNORECASE):
        return "hospital_acquired"
    if re.search(r"医疗相关|近期住院|长期护理机构|维持性血液透析", text):
        return "healthcare_associated"
    if re.search(r"社区获得|社区起病|院外起病|入院前已|就诊前已", text):
        return "community"
    return "unknown"


def _explicit_status(text: str, terms: Sequence[str]) -> Optional[bool]:
    """Return a conservative tri-state from explicit nearby wording only."""
    saw_negated = False
    for term in terms:
        for match in re.finditer(term, text, flags=re.IGNORECASE):
            prefix = text[max(0, match.start() - 10):match.start()]
            if re.search(r"(?:无|否认|未见|未接受|未使用|不伴|排除)了?[^,，。；;]{0,6}$", prefix):
                saw_negated = True
                continue
            return True
    return False if saw_negated else None


def _immunocompromised_status(text: str) -> Optional[bool]:
    return _explicit_status(text, (
        r"器官移植", r"造血干细胞移植", r"强化化疗", r"严重中性粒细胞缺乏",
        r"免疫抑制", r"HIV", r"艾滋", r"长期大剂量激素", r"免疫抑制剂",
    ))


def _pregnancy_status(text: str) -> Optional[bool]:
    return _explicit_status(text, (r"妊娠", r"孕\s*\d{1,2}\s*周", r"产后"))


def _scenario(text: str) -> str:
    rules: Sequence[Tuple[str, Sequence[str]]] = (
        ("cns", ("脑膜炎", "脑炎", "脑膜脑炎", "颈强直", "脑脊液")),
        ("urinary", ("尿频", "尿急", "尿痛", "尿路感染", "肾盂肾炎", "脓尿")),
        # Sepsis and septic shock are syndromes, not proof of bloodstream
        # infection, so they must not route a case to this site by themselves.
        ("bloodstream", ("血流感染", "菌血症")),
        ("abdominal", ("腹膜炎", "胆管炎", "腹腔感染", "腹痛", "腹泻")),
        ("lower_respiratory", ("肺炎", "肺部感染", "咳嗽", "咳痰", "气促", "呼吸困难", "肺实变", "胸部ct")),
    )
    scores = [(name, sum(text.lower().count(term.lower()) for term in terms)) for name, terms in rules]
    best = max(scores, key=lambda item: item[1])
    return best[0] if best[1] > 0 else "undifferentiated"


def _sentences_with_terms(text: str, terms: Sequence[str]) -> str:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；;\n])", text) if item.strip()]
    selected = [item for item in sentences if any(term.lower() in item.lower() for term in terms)]
    return "".join(selected)


def _parse_time_token(token: str, reference: datetime) -> Optional[datetime]:
    value = token.strip()
    tz = reference.tzinfo or timezone.utc
    iso_value = value.replace("年", "-").replace("月", "-").replace("日", " ").replace("/", "-")
    iso_value = re.sub(r"\s+", " ", iso_value).strip()
    try:
        parsed = datetime.fromisoformat(iso_value.replace(" ", "T", 1))
        return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed
    except ValueError:
        pass
    for pattern, builder in (
        (r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$", "month_day"),
        (r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", "time"),
    ):
        match = re.match(pattern, iso_value)
        if not match:
            continue
        groups = [int(item) if item is not None else 0 for item in match.groups()]
        try:
            if builder == "month_day":
                month, day, hour, minute, second = groups
                parsed = datetime(reference.year, month, day, hour, minute, second, tzinfo=tz)
                # A year-less date near New Year can otherwise be silently
                # assigned to the wrong year.  Treat a >6 month gap as
                # ambiguous so the caller falls back and requests review.
                if abs((parsed - reference).total_seconds()) > 183 * 24 * 60 * 60:
                    return None
                return parsed
            hour, minute, second = groups
            return datetime(reference.year, reference.month, reference.day, hour, minute, second, tzinfo=tz)
        except ValueError:
            return None
    return None


def _labeled_time(text: str, labels: Sequence[str], reference: datetime) -> Optional[datetime]:
    label_pattern = "|".join(labels)
    for match in re.finditer(
        r"(?:%s)\s*(?:为|于|[:：])?\s*(%s)" % (label_pattern, DATETIME_TOKEN),
        text,
        flags=re.IGNORECASE,
    ):
        parsed = _parse_time_token(match.group(1), reference)
        if parsed is not None:
            return parsed
    return None


def _parse_vitals(
    text: str, decision_time: datetime, source: str, warnings: List[CompilerWarning]
) -> List[ClinicalTextVital]:
    measured = _labeled_time(text, ("测量时间", "记录时间", "生命体征时间"), decision_time)
    time_value = measured or decision_time
    certainty = "explicit" if measured else "assumed_decision_time"
    values: List[ClinicalTextVital] = []
    for display_name, pattern, unit in VITAL_RULES:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        values.append(ClinicalTextVital(
            id="vital-%s" % (len(values) + 1), measuredAt=time_value, name=display_name,
            value=re.sub(r"\s+", "", match.group(1)), unit=unit, source=source,
            timeCertainty=certainty,
        ))
    if values and not measured:
        _add_warning(
            warnings, "vital_time_assumed",
            "生命体征未识别到明确测量时间，暂以 decision_time 填充，请人工确认。",
        )
    return values


def _parse_labs(
    text: str, decision_time: datetime, source: str, warnings: List[CompilerWarning]
) -> List[ClinicalTextLab]:
    sampled = _labeled_time(text, ("采样时间", "采集时间", "抽血时间", "送检时间"), decision_time)
    available = _labeled_time(
        text, ("报告返回时间", "结果返回时间", "报告时间", "回报时间", "签发时间"), decision_time
    )
    sampled_value = sampled or decision_time
    available_value = available or decision_time
    labs: List[ClinicalTextLab] = []
    missing_units: List[str] = []
    for display_name, aliases in LAB_RULES:
        pattern = re.compile(
            r"(?:%s)\s*(?:结果)?\s*[:：=]?\s*([<>]?\s*-?\d+(?:\.\d+)?)"
            r"(?![\d.]|[,，]\d|[eE][+-]?\d)\s*(%s)?\s*"
            r"(↑|↓|(?<![A-Za-z])H(?![A-Za-z])|(?<![A-Za-z])L(?![A-Za-z])|升高|降低|偏高|偏低)?"
            % (aliases, UNIT_PATTERN),
            flags=re.IGNORECASE,
        )
        match = pattern.search(text)
        if not match:
            continue
        value = re.sub(r"\s+", "", match.group(1))
        unit = re.sub(r"\s+", "", match.group(2) or "")
        flag = (match.group(3) or "").strip().lower()
        abnormal = "high" if flag in {"↑", "h", "升高", "偏高"} else "low" if flag in {"↓", "l", "降低", "偏低"} else "unknown"
        if not unit:
            missing_units.append(display_name)
        labs.append(ClinicalTextLab(
            id="lab-%s" % (len(labs) + 1), sampledAt=sampled_value, availableAt=available_value,
            name=display_name, value=value, unit=unit, abnormal=abnormal, source=source,
            sampledTimeCertainty="explicit" if sampled else "assumed_decision_time",
            availableTimeCertainty="explicit" if available else "uncertain_assumed_decision_time",
        ))
    if labs and not sampled:
        _add_warning(
            warnings, "lab_sample_time_assumed",
            "检验未识别到明确采样时间，暂以 decision_time 填充，请人工确认。",
        )
    if labs and not available:
        _add_warning(
            warnings, "lab_visible_time_uncertain",
            "检验未识别到明确报告返回时间，visible_at/availableAt 暂取 decision_time；不得据此证明结果当时已经可见。",
        )
    if missing_units:
        _add_warning(
            warnings, "lab_unit_missing",
            "部分检验值未识别到单位（%s），已保留空单位，禁止自动补猜。" % "、".join(missing_units),
        )
    if sampled and available and available < sampled:
        _add_warning(warnings, "lab_time_conflict", "检验报告返回时间早于采样时间，请人工核对。")
    if available and available > decision_time:
        _add_warning(
            warnings, "lab_after_decision_time",
            "检验报告返回时间晚于 decision_time；生成事件会保留该时间，因此严格时间闸门应排除它。",
        )
    return labs


def _extract_imaging_text(full_text: str, section_text: str) -> str:
    if section_text.strip():
        return section_text.strip()
    sentences = [item.strip() for item in re.split(r"(?<=[。；;\n])", full_text) if item.strip()]
    selected = [item for item in sentences if re.search(
        r"(?<![A-Za-z])CT(?![A-Za-z])|MRI|X线|胸片|超声|影像|实变|磨玻璃",
        item,
        flags=re.IGNORECASE,
    )]
    return "".join(selected)


def _parse_imaging(
    full_text: str, section_text: str, decision_time: datetime, warnings: List[CompilerWarning]
) -> ClinicalTextImaging:
    report = _extract_imaging_text(full_text, section_text)
    if not report:
        return ClinicalTextImaging()
    modality = "CT" if re.search(r"(?<![A-Za-z])CT(?![A-Za-z])", full_text, flags=re.IGNORECASE) else None
    if modality is None and re.search(r"MRI|磁共振", full_text, flags=re.IGNORECASE):
        modality = "MRI"
    if modality is None and re.search(r"X线|胸片", full_text, flags=re.IGNORECASE):
        modality = "X-ray"
    if modality is None and "超声" in full_text:
        modality = "ultrasound"
    performed = _labeled_time(report, ("检查时间", "拍片时间", "扫描时间"), decision_time)
    available = _labeled_time(report, ("报告返回时间", "结果返回时间", "报告时间", "回报时间", "签发时间"), decision_time)
    performed_value = performed or decision_time
    available_value = available or decision_time
    if not performed:
        _add_warning(warnings, "imaging_performed_time_assumed", "影像未识别到明确检查时间，暂以 decision_time 填充，请确认。")
    if not available:
        _add_warning(
            warnings, "imaging_visible_time_uncertain",
            "影像未识别到明确报告返回时间，visible_at/availableAt 暂取 decision_time；请确认报告当时是否已可见。",
        )
    if performed and available and available < performed:
        _add_warning(warnings, "imaging_time_conflict", "影像报告返回时间早于检查时间，请人工核对。")
    if available and available > decision_time:
        _add_warning(
            warnings, "imaging_after_decision_time",
            "影像报告返回时间晚于 decision_time；生成事件会保留该时间，因此严格时间闸门应排除它。",
        )
    return ClinicalTextImaging(
        modality=modality, performedAt=performed_value, availableAt=available_value,
        report=report, qualityNote="本地规则抽取，需与原文逐项核对。",
        performedTimeCertainty="explicit" if performed else "assumed_decision_time",
        availableTimeCertainty="explicit" if available else "uncertain_assumed_decision_time",
    )


def _events_from_draft(draft: ClinicalTextCaseDraft, source: str) -> List[OrganizedClinicalEvent]:
    events: List[OrganizedClinicalEvent] = []
    decision_time = draft.decision_time
    history_data = {
        "deidentified_note": draft.deidentified_note,
        "chief_complaint": draft.history.chief_complaint,
        "present_illness": draft.history.present_illness,
        "prior_antimicrobials": draft.history.prior_antimicrobials,
        "comorbidities": draft.host.comorbidities,
        "immune_status": draft.host.immune_status,
        "devices_and_procedures": draft.host.devices_and_procedures,
        "allergies": draft.host.allergies,
    }
    if any(str(value).strip() for value in history_data.values()):
        events.append(OrganizedClinicalEvent(
            kind=EventKind.HISTORY, occurred_at=decision_time, visible_at=decision_time,
            source=source, status=EventStatus.FINAL, data=history_data,
            quality={"parser_version": PARSER_VERSION, "requires_clinician_review": True},
            time_certainty="assumed_decision_time",
        ))
    exposure_data = {
        "exposure_history": draft.history.exposure_history,
        "epidemiology": draft.history.epidemiology,
    }
    if any(str(value).strip() for value in exposure_data.values()):
        events.append(OrganizedClinicalEvent(
            kind=EventKind.EXPOSURE, occurred_at=decision_time, visible_at=decision_time,
            source=source, status=EventStatus.FINAL, data=exposure_data,
            quality={"parser_version": PARSER_VERSION, "requires_clinician_review": True},
            time_certainty="assumed_decision_time",
        ))
    for vital in draft.vitals:
        events.append(OrganizedClinicalEvent(
            kind=EventKind.VITAL, occurred_at=vital.measured_at, visible_at=vital.measured_at,
            source=source, status=EventStatus.FINAL,
            data={"observation": vital.name, "value": vital.value, "unit": vital.unit},
            quality={"parser_version": PARSER_VERSION, "requires_clinician_review": True},
            time_certainty=vital.time_certainty,
        ))
    for lab in draft.labs:
        events.append(OrganizedClinicalEvent(
            kind=EventKind.LAB, occurred_at=lab.sampled_at, collected_at=lab.sampled_at,
            issued_at=lab.available_at, visible_at=lab.available_at, source=source,
            status=EventStatus.FINAL,
            data={"test_name": lab.name, "value": lab.value, "unit": lab.unit,
                  "reference_range": lab.reference_range, "abnormal": lab.abnormal},
            quality={"parser_version": PARSER_VERSION, "requires_clinician_review": True,
                     "sampled_time_certainty": lab.sampled_time_certainty,
                     "visible_time_certainty": lab.available_time_certainty},
            time_certainty=lab.available_time_certainty,
        ))
    imaging = draft.imaging
    if imaging.report:
        occurred = imaging.performed_at or decision_time
        visible = imaging.available_at or decision_time
        events.append(OrganizedClinicalEvent(
            kind=EventKind.IMAGING_REPORT, occurred_at=occurred, issued_at=visible,
            visible_at=visible, source=source, status=EventStatus.FINAL,
            data={"modality": imaging.modality or "unspecified", "report": imaging.report},
            quality={"parser_version": PARSER_VERSION, "requires_clinician_review": True,
                     "performed_time_certainty": imaging.performed_time_certainty,
                     "visible_time_certainty": imaging.available_time_certainty},
            time_certainty=imaging.available_time_certainty or "uncertain_assumed_decision_time",
        ))
    return events


def organize_clinical_text(payload: ClinicalTextOrganizeRequest) -> ClinicalTextOrganizeResponse:
    text = payload.text
    decision_time = payload.decision_time
    warnings = clinical_text_safety_warnings(text, decision_time)
    sections, unrecognized = _split_sections(text)

    # A complete pasted note is preserved locally for provenance but is never
    # included in the model snapshot.  Future/pathogen-result signals still
    # block review so they cannot be copied into clinician-approved fields.
    future_tokens: List[str] = []
    for match in re.finditer(DATETIME_TOKEN, text, flags=re.IGNORECASE):
        parsed = _parse_time_token(match.group(0), decision_time)
        if parsed is not None and parsed > decision_time:
            future_tokens.append(match.group(0))
    if future_tokens:
        _add_warning(
            warnings,
            "future_timestamp_in_text",
            "原文含有晚于 decision_time 的时间（%s）。为防止未来信息绕过时间闸门，启动推演前必须删除该段、调整决策时点，或将其作为新一次运行。"
            % "、".join(dict.fromkeys(future_tokens)),
        )

    if PATHOGEN_RESULT_PATTERN.search(text):
        _add_warning(
            warnings,
            "possible_pathogen_label_leakage",
            "原文可能包含 PCR、培养、mNGS 或其他病原学结果。当前任务是根据早期非病原学证据进行推演，请先移除该结果以避免标签泄漏。",
        )

    scenario = _scenario(text)
    if scenario == "undifferentiated":
        _add_warning(warnings, "syndrome_not_identified", "未可靠识别感染综合征，已设为 undifferentiated。")
    else:
        _add_warning(warnings, "syndrome_rule_inferred", "综合征由本地关键词规则建议，必须由临床医生确认。", "info")

    present_illness = sections.get("present_illness", "")
    if not any(value.strip() for value in sections.values()):
        present_illness = _sentences_with_terms(
            text,
            (
                "发热", "咳嗽", "咳痰", "气促", "呼吸困难", "胸痛", "乏力", "寒战",
                "起病", "病程", "就诊", "肺炎", "肺部感染", "fever", "cough", "dyspnea",
            ),
        )
        _add_warning(
            warnings, "section_structure_not_identified",
            "未识别标准分节；现病史仅保留规则识别的相关句，完整原文只作为本地审计来源保存在 deidentified_note，不进入模型快照。",
        )

    exposure = sections.get("exposure_history", "")
    if not exposure:
        exposure = _sentences_with_terms(text, ("接触", "旅行", "疫区", "禽", "鸟", "动物", "生食", "职业暴露"))
    medications = sections.get("medications", "")
    if not medications:
        medications = _sentences_with_terms(
            text, ("抗菌药", "抗生素", "抗感染", "青霉素", "头孢", "阿奇霉素", "喹诺酮", "碳青霉烯")
        )
    immune_status = _sentences_with_terms(
        text, ("免疫抑制", "化疗", "移植", "HIV", "艾滋", "中性粒细胞缺乏", "激素", "生物制剂")
    )
    procedures = sections.get("procedures", "") or _sentences_with_terms(
        text, ("中心静脉导管", "尿管", "气管插管", "机械通气", "手术", "透析", "引流管")
    )
    allergies = sections.get("allergies", "")
    if not allergies:
        allergy_match = re.search(r"过敏史\s*[:：]?\s*([^\n；;。]{1,100})", text)
        allergies = allergy_match.group(1).strip() if allergy_match else ""

    vital_text = sections.get("vitals", "") or text
    lab_text = sections.get("laboratory", "") or text
    vitals = _parse_vitals(vital_text, decision_time, payload.source, warnings)
    labs = _parse_labs(lab_text, decision_time, payload.source, warnings)
    imaging = _parse_imaging(text, sections.get("imaging", ""), decision_time, warnings)

    demographics = ClinicalTextDemographics(
        age=_parse_age(text), sex=_parse_sex(text), department=_parse_department(text),
        encounterType=_encounter_type(text), pregnant=_pregnancy_status(text),
        immunocompromised=_immunocompromised_status(text),
    )
    if demographics.age is None:
        _add_warning(warnings, "age_not_identified", "未识别到可靠年龄。", "info")
    if demographics.sex == "unknown":
        _add_warning(warnings, "sex_not_identified", "未识别到可靠性别。", "info")

    draft = ClinicalTextCaseDraft(
        decisionTime=decision_time,
        scenario=scenario,
        acquisitionContext=_acquisition_context(text),
        demographics=demographics,
        history=ClinicalTextHistory(
            chiefComplaint=sections.get("chief_complaint", ""),
            presentIllness=present_illness,
            exposureHistory=exposure,
            epidemiology=sections.get("epidemiology", ""),
            priorAntimicrobials=medications,
        ),
        host=ClinicalTextHost(
            comorbidities=sections.get("past_history", ""), immuneStatus=immune_status,
            devicesAndProcedures=procedures, allergies=allergies,
        ),
        vitals=vitals,
        labs=labs,
        imaging=imaging,
        timeline=[],
        selectedProviders=[],
        # The field name matches the existing UI contract. The value is preserved
        # byte-for-byte, but is NOT automatically de-identified; warnings above
        # make that limitation explicit when identifier patterns are detected.
        deidentified_note=text,
    )
    if draft.acquisition_context == "unknown":
        _add_warning(
            warnings,
            "acquisition_context_not_identified",
            "未能确认是社区/院外起病。当前 v1 仅覆盖成人社区起病下呼吸道场景，请在高级校对中确认。",
        )
    events = _events_from_draft(draft, payload.source)
    recognized = {key: value for key, value in sections.items() if value.strip()}
    return ClinicalTextOrganizeResponse(
        case_draft=draft,
        recognized_sections=recognized,
        unrecognized_segments=unrecognized,
        events=events,
        warnings=warnings,
        parser_version=PARSER_VERSION,
        source_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        persistence="none",
    )
