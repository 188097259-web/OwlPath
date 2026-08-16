from typing import Dict, Iterable, List, Optional, Set

from .models import AggregatedResult, EvaluationLabel


def _mean(values: Iterable[float]) -> Optional[float]:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def evaluate_result(result: AggregatedResult, label: EvaluationLabel) -> Dict[str, Optional[float]]:
    gold: Set[str] = {
        item.canonical_id.strip().lower()
        for item in label.causal_pathogens
        if item.certainty in {"confirmed", "probable"}
    }
    ranked = [item.canonical_id.strip().lower() for item in result.candidates]
    metrics: Dict[str, Optional[float]] = {
        "top1": None,
        "top3": None,
        "top5": None,
        "mrr": None,
        "pathogen_brier": None,
        "infection_brier": None,
    }
    if label.infection_status != "uncertain":
        target = 1.0 if label.infection_status == "infectious" else 0.0
        metrics["infection_brier"] = (result.infection_probability - target) ** 2
    if not gold:
        if label.infection_status == "non_infectious":
            predicted = {item.canonical_id.strip().lower(): item.probability for item in result.candidates}
            metrics["pathogen_brier"] = _mean(score ** 2 for score in predicted.values()) or 0.0
        return metrics

    metrics["top1"] = 1.0 if any(item in gold for item in ranked[:1]) else 0.0
    metrics["top3"] = 1.0 if any(item in gold for item in ranked[:3]) else 0.0
    metrics["top5"] = 1.0 if any(item in gold for item in ranked[:5]) else 0.0
    reciprocal = 0.0
    for index, item in enumerate(ranked, start=1):
        if item in gold:
            reciprocal = 1.0 / index
            break
    metrics["mrr"] = reciprocal

    predicted = {item.canonical_id.strip().lower(): item.probability for item in result.candidates}
    universe = set(predicted) | gold
    # Multi-label one-vs-rest Brier over the union of reportable predictions and
    # confirmed/probable causes. This is useful for prototype comparison but is
    # not a substitute for prespecified multicentre calibration analysis.
    metrics["pathogen_brier"] = _mean(
        (predicted.get(item, 0.0) - (1.0 if item in gold else 0.0)) ** 2
        for item in universe
    )
    return metrics


def summarize_metrics(rows: List[Dict[str, Optional[float]]]) -> Dict[str, Optional[float]]:
    keys = ["top1", "top3", "top5", "mrr", "pathogen_brier", "infection_brier"]
    summary: Dict[str, Optional[float]] = {"n_evaluations": float(len(rows))}
    for key in keys:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        summary[key] = _mean(values)
        summary["n_%s" % key] = float(len(values))
    return summary
