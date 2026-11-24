"""Portable reported-summary and P-value recomputation.

The functions here accept literal values or explicitly bound tables. They never
choose a test from two means, never impute missing observations, and never
replace a reported inequality with an exact value. Distribution functions are
implemented with the standard library so the standalone package keeps its
locked dependency set.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any

METHOD_VERSION = "reported-stats/1"
_TAILS = {"two", "greater", "less"}


def _finite(value: Any, label: str = "value") -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _clean(values: Iterable[Any], label: str = "observations") -> list[float]:
    result = [_finite(value, label) for value in values]
    if not result:
        raise ValueError(f"{label} must not be empty")
    return result


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _var(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("sample variance requires at least two observations")
    center = _mean(values)
    return math.fsum((value - center) ** 2 for value in values) / (len(values) - 1)


def _betacf(a: float, b: float, x: float) -> float:
    max_iterations = 300
    epsilon = 3e-14
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for index in range(1, max_iterations + 1):
        twice = 2 * index
        coefficient = index * (b - index) * x / ((qam + twice) * (a + twice))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        coefficient = -(a + index) * (qab + index) * x / ((a + twice) * (qap + twice))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return result


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    factor = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return factor * _betacf(a, b, x) / a
    return 1.0 - factor * _betacf(b, a, 1.0 - x) / b


def _gammaincc(a: float, x: float) -> float:
    if x <= 0.0:
        return 1.0
    if x < a + 1.0:
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 3e-14:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    result = d
    for index in range(1, 1000):
        coefficient = -index * (index - a)
        b += 2.0
        d = coefficient * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * result


def _df(value: Any, label: str = "df") -> float:
    number = _finite(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def p_from_statistic(value: Any, distribution: str, *, df: Any = None,
                     tails: str = "two") -> float:
    """Return the requested tail probability for z, t, chi-square or F."""
    if tails not in _TAILS:
        raise ValueError("tails must be two, greater or less")
    statistic = _finite(value, "statistic")
    distribution = str(distribution).lower()
    if distribution == "z":
        sf = 0.5 * math.erfc(statistic / math.sqrt(2.0))
        if tails == "two":
            return min(1.0, 2.0 * sf if statistic >= 0 else 2.0 * (1.0 - sf))
        return max(0.0, min(1.0, sf if tails == "greater" else 1.0 - sf))
    if distribution == "t":
        degrees = _df(df)
        two_sided = _betainc(degrees / 2.0, 0.5, degrees / (degrees + statistic * statistic))
        if tails == "two":
            return max(0.0, min(1.0, two_sided))
        half = 0.5 * two_sided
        if tails == "greater":
            return max(0.0, min(1.0, half if statistic >= 0 else 1.0 - half))
        return max(0.0, min(1.0, half if statistic <= 0 else 1.0 - half))
    if distribution in {"chi2", "chi-square"}:
        degrees = _df(df)
        sf = _gammaincc(degrees / 2.0, statistic / 2.0)
        if tails == "two":
            raise ValueError("chi-square requires a one-sided tail")
        return max(0.0, min(1.0, sf if tails == "greater" else 1.0 - sf))
    if distribution == "f":
        if not isinstance(df, (list, tuple)) or len(df) != 2:
            raise ValueError("F requires df=[df1, df2]")
        numerator, denominator = _df(df[0], "df1"), _df(df[1], "df2")
        sf = _betainc(denominator / 2.0, numerator / 2.0,
                      denominator / (denominator + numerator * statistic))
        if tails == "two":
            raise ValueError("F requires a one-sided tail")
        return max(0.0, min(1.0, sf if tails == "greater" else 1.0 - sf))
    raise ValueError("distribution must be z, t, chi2 or f")


def p_from_samples(samples: Mapping[str, Any], *, test: str,
                   tails: str = "two", correction: bool = False) -> dict[str, Any]:
    """Recompute a P value from raw samples under an explicitly named design."""
    if tails not in _TAILS:
        raise ValueError("tails must be two, greater or less")
    name = str(test).lower()
    if name in {"welch_t", "student_t", "paired_t"}:
        groups = samples.get("groups")
        if not isinstance(groups, (list, tuple)) or len(groups) != 2:
            raise ValueError("two-sample tests require exactly two groups")
        first, second = _clean(groups[0], "group 1"), _clean(groups[1], "group 2")
        if name == "paired_t":
            if len(first) != len(second):
                raise ValueError("paired samples must have equal length")
            differences = [a - b for a, b in zip(first, second)]
            if len(differences) < 2:
                raise ValueError("paired test requires at least two pairs")
            center = _mean(differences)
            standard_error = math.sqrt(_var(differences) / len(differences))
            if standard_error == 0:
                return {"applicable": False, "test": "paired_t",
                        "reason": "zero variance of paired differences"}
            statistic = center / standard_error
            degrees = len(differences) - 1
            return {"applicable": True, "test": "paired_t", "statistic": statistic,
                    "df": degrees, "p": p_from_statistic(statistic, "t", df=degrees, tails=tails),
                    "n": len(differences),
                    "assumptions": ["independent pairs", "approximately normal differences"]}
        if len(first) < 2 or len(second) < 2:
            return {"applicable": False, "test": name, "reason": "each group needs at least two observations"}
        first_mean, second_mean = _mean(first), _mean(second)
        first_var, second_var = _var(first), _var(second)
        difference = first_mean - second_mean
        if name == "student_t":
            pooled = ((len(first) - 1) * first_var + (len(second) - 1) * second_var) / (len(first) + len(second) - 2)
            standard_error = math.sqrt(pooled * (1.0 / len(first) + 1.0 / len(second)))
            degrees = len(first) + len(second) - 2
            assumptions = ["independent groups", "approximately normal data", "equal variances"]
        else:
            first_component = first_var / len(first)
            second_component = second_var / len(second)
            standard_error = math.sqrt(first_component + second_component)
            denominator = (first_component ** 2 / (len(first) - 1)
                           + second_component ** 2 / (len(second) - 1))
            degrees = (first_component + second_component) ** 2 / denominator if denominator else None
            assumptions = ["independent groups", "approximately normal data", "unequal variances allowed"]
        if standard_error == 0 or degrees is None:
            return {"applicable": False, "test": name, "reason": "zero standard error or undefined degrees of freedom"}
        statistic = difference / standard_error
        return {"applicable": True, "test": name, "statistic": statistic,
                "df": degrees, "p": p_from_statistic(statistic, "t", df=degrees, tails=tails),
                "n": [len(first), len(second)], "assumptions": assumptions}
    if name == "one_way_anova":
        groups = samples.get("groups")
        if not isinstance(groups, (list, tuple)) or len(groups) < 2:
            raise ValueError("ANOVA requires at least two groups")
        cleaned = [_clean(group, f"group {index + 1}") for index, group in enumerate(groups)]
        if any(len(group) < 2 for group in cleaned):
            return {"applicable": False, "test": name, "reason": "each group needs at least two observations"}
        total = sum(len(group) for group in cleaned)
        grand = math.fsum(value for group in cleaned for value in group) / total
        between = math.fsum(len(group) * (_mean(group) - grand) ** 2 for group in cleaned)
        within = math.fsum((value - _mean(group)) ** 2 for group in cleaned for value in group)
        df_between, df_within = len(cleaned) - 1, total - len(cleaned)
        if df_within <= 0 or within == 0:
            return {"applicable": False, "test": name, "reason": "within-group variance is zero or df is not positive"}
        statistic = (between / df_between) / (within / df_within)
        return {"applicable": True, "test": name, "statistic": statistic,
                "df": [df_between, df_within],
                "p": p_from_statistic(statistic, "f", df=[df_between, df_within], tails="greater"),
                "n": [len(group) for group in cleaned],
                "assumptions": ["independent groups", "approximately normal data", "equal variances"]}
    if name == "pearson":
        first, second = _clean(samples.get("x", []), "x"), _clean(samples.get("y", []), "y")
        if len(first) != len(second) or len(first) < 3:
            return {"applicable": False, "test": name, "reason": "Pearson requires paired x/y with at least three pairs"}
        first_mean, second_mean = _mean(first), _mean(second)
        covariance = math.fsum((a - first_mean) * (b - second_mean) for a, b in zip(first, second))
        first_ss = math.fsum((a - first_mean) ** 2 for a in first)
        second_ss = math.fsum((b - second_mean) ** 2 for b in second)
        if first_ss == 0 or second_ss == 0:
            return {"applicable": False, "test": name, "reason": "zero variance in x or y"}
        r = covariance / math.sqrt(first_ss * second_ss)
        if abs(r) >= 1.0:
            p = 0.0
            statistic = math.copysign(math.inf, r)
        else:
            statistic = r * math.sqrt((len(first) - 2) / (1.0 - r * r))
            p = p_from_statistic(statistic, "t", df=len(first) - 2, tails=tails)
        return {"applicable": True, "test": name, "statistic": statistic, "r": r,
                "df": len(first) - 2, "p": p, "n": len(first),
                "assumptions": ["paired observations", "linear association", "approximately normal residuals"]}
    if name in {"chi2_contingency", "chi_square_contingency"}:
        table = samples.get("table")
        if not isinstance(table, (list, tuple)) or len(table) < 2 or any(not isinstance(row, (list, tuple)) or len(row) < 2 for row in table):
            raise ValueError("chi-square contingency requires a 2D table of counts")
        counts = [[_finite(value, "count") for value in row] for row in table]
        if len({len(row) for row in counts}) != 1 or any(value < 0 for row in counts for value in row):
            raise ValueError("counts must be nonnegative and rectangular")
        row_totals = [math.fsum(row) for row in counts]
        column_totals = [math.fsum(counts[row][column] for row in range(len(counts))) for column in range(len(counts[0]))]
        total = math.fsum(row_totals)
        if total <= 0:
            return {"applicable": False, "test": name, "reason": "empty contingency table"}
        statistic = 0.0
        yates = bool(correction) and len(counts) == 2 and len(counts[0]) == 2
        for row, row_total in enumerate(row_totals):
            for column, column_total in enumerate(column_totals):
                expected = row_total * column_total / total
                if expected <= 0:
                    return {"applicable": False, "test": name, "reason": "zero expected count"}
                difference = counts[row][column] - expected
                if yates:
                    difference = max(0.0, abs(difference) - 0.5)
                    difference = math.copysign(difference, counts[row][column] - expected)
                statistic += difference ** 2 / expected
        degrees = (len(counts) - 1) * (len(counts[0]) - 1)
        return {"applicable": True, "test": name, "statistic": statistic, "df": degrees,
                "p": p_from_statistic(statistic, "chi2", df=degrees, tails="greater"),
                "n": int(total), "correction": "yates" if yates else "none",
                "assumptions": ["independent observations", "expected counts are adequate"]}
    raise ValueError("test must be welch_t, student_t, paired_t, one_way_anova, pearson or chi2_contingency")


def parse_reported_p(value: Any, *, decimals: int | None = None) -> dict[str, Any]:
    """Parse a numeric P value or an explicit inequality such as P<0.001."""
    raw = value
    if isinstance(value, bool) or value is None:
        raise ValueError("reported P must be a number or inequality")
    if isinstance(value, (int, float, Decimal, Fraction)):
        number = _finite(value, "reported P")
        if not 0.0 <= number <= 1.0:
            raise ValueError("reported P must be in [0, 1]")
        places = decimals if decimals is not None else (None if number == 0.0 else _infer_decimals(number))
        return {"raw": str(raw), "operator": "=", "value": number, "decimals": places}
    text = str(value).strip().replace("≤", "<=").replace("≥", ">=").replace("＜", "<").replace("＞", ">")
    if not text:
        raise ValueError("reported P is empty")
    if re.fullmatch(r"(?:n\.?s\.?|NS)", text, flags=re.IGNORECASE):
        return {"raw": str(raw), "operator": "ns", "value": None, "decimals": None,
                "status": "not_checked", "reason": "reported as non-significant without a numeric bound"}
    match = re.fullmatch(r"(?:[Pp]\s*)?(<=|>=|<|>|=)?\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)", text)
    if not match:
        raise ValueError("reported P must be numeric or use <, <=, >, >=, =")
    operator = match.group(1) or "="
    token = match.group(2)
    number = _finite(token, "reported P")
    if not 0.0 <= number <= 1.0:
        raise ValueError("reported P must be in [0, 1]")
    places = decimals
    if places is None and "e" not in token.lower() and "." in token:
        places = len(token.split(".", 1)[1])
    if places is not None and (type(places) is not int or not 0 <= places <= 12):
        raise ValueError("reported decimals must be an integer in [0,12]")
    return {"raw": str(raw), "operator": operator, "value": number, "decimals": places}


def _infer_decimals(number: float) -> int | None:
    text = format(number, ".15g")
    if "e" in text.lower():
        return None
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def compare_p_value(recomputed: Any, reported: Any, *, decimals: int | None = None) -> dict[str, Any]:
    """Compare a recomputed P with a reported value or inequality."""
    p = _finite(recomputed, "recomputed P")
    if not 0.0 <= p <= 1.0:
        raise ValueError("recomputed P must be in [0, 1]")
    parsed = reported if isinstance(reported, Mapping) and "operator" in reported else parse_reported_p(reported, decimals=decimals)
    operator, target = parsed["operator"], parsed["value"]
    if operator == "ns":
        return {**parsed, "status": "not_checked", "recomputed": p}
    if operator == "=":
        places = decimals if decimals is not None else parsed.get("decimals")
        if target == 0.0 and (places is None or places == 0):
            return {"status": "not_checked", "recomputed": p, "reported": target,
                    "reason": "reported P=0 needs a stated inequality or decimal precision"}
        if places is None:
            places = _infer_decimals(target)
        if places is None:
            return {"status": "not_checked", "recomputed": p, "reported": target,
                    "reason": "reported precision unavailable"}
        half = 0.5 * 10.0 ** (-places)
        low, high = max(0.0, target - half), min(1.0, target + half)
        consistent = low <= p <= high
        return {"status": "checked", "operator": "=", "consistent": consistent,
                "recomputed": p, "reported": target, "decimals": places,
                "admitted_interval": [low, high], "difference": p - target}
    consistent = {"<": p < target, "<=": p <= target, ">": p > target, ">=": p >= target}[operator]
    return {"status": "checked", "operator": operator, "consistent": consistent,
            "recomputed": p, "reported": target, "difference": p - target}


def recompute_p_value(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch one P-value binding.

    spec:
      {"mode":"statistic","statistic":2.0,"distribution":"t","df":10,"tails":"two",
       "reported_p":"0.05","reported_decimals":2}
      {"mode":"samples","test":"welch_t","groups":[[...],[...]],"tails":"two"}
    """
    if not isinstance(spec, Mapping):
        raise ValueError("P-value binding must be an object")
    mode = spec.get("mode")
    if mode == "statistic":
        distribution = spec.get("distribution")
        default_tail = "greater" if str(distribution).lower() in {"chi2", "chi-square", "f"} else "two"
        tails = spec.get("tails") or default_tail
        value = p_from_statistic(spec.get("statistic"), distribution,
                                 df=spec.get("df"), tails=tails)
        result = {"applicable": True, "mode": mode, "distribution": distribution,
                  "statistic": _finite(spec.get("statistic"), "statistic"),
                  "df": spec.get("df"), "tails": tails, "p": value}
    elif mode == "samples":
        samples = {key: spec.get(key) for key in ("groups", "x", "y", "table")}
        result = p_from_samples(samples, test=spec.get("test"), tails=spec.get("tails", "two"),
                                correction=spec.get("correction", False))
        result["mode"] = mode
    else:
        raise ValueError("P-value mode must be statistic or samples")
    if "reported_p" in spec or "reported" in spec:
        result["comparison"] = compare_p_value(
            result.get("p"), spec.get("reported_p", spec.get("reported")),
            decimals=spec.get("reported_decimals"))
    return result


def adjust_p_values(pvalues: Sequence[Any], *, method: str = "holm") -> list[float]:
    """Return Holm or Benjamini-Hochberg adjusted P values in input order."""
    values = [_finite(value, "P value") for value in pvalues]
    if any(not 0.0 <= value <= 1.0 for value in values):
        raise ValueError("P values must be in [0, 1]")
    count = len(values)
    if count == 0:
        return []
    method = str(method).lower()
    if method in {"none", "raw"}:
        return values
    order = sorted(range(count), key=lambda index: values[index])
    adjusted = [0.0] * count
    if method == "holm":
        running = 0.0
        for rank, index in enumerate(order):
            running = max(running, min(1.0, (count - rank) * values[index]))
            adjusted[index] = running
        return adjusted
    if method in {"bh", "fdr_bh"}:
        running = 1.0
        for rank in range(count - 1, -1, -1):
            index = order[rank]
            running = min(running, min(1.0, values[index] * count / (rank + 1)))
            adjusted[index] = running
        return adjusted
    raise ValueError("method must be holm, bh or none")


def _fraction(value: Any, label: str = "observation") -> Fraction:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a finite number")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} must be a finite number")
    try:
        number = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    return number


def _decimal(value: Fraction) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return Decimal(value.numerator) / Decimal(value.denominator)


def recompute_summary(values: Iterable[Any], *, ddof: int = 1) -> dict[str, Any]:
    """Compute n, exact sum, mean, sample SD and SE from explicit observations."""
    if type(ddof) is not int or ddof not in {0, 1}:
        raise ValueError("ddof must be 0 or 1")
    observations = [_fraction(value) for value in values]
    if not observations:
        raise ValueError("summary requires at least one observation")
    count = len(observations)
    total = sum(observations, Fraction(0))
    mean = total / count
    result = {"n": count, "sum": str(total), "mean": str(_decimal(mean)),
              "ddof": ddof, "sd": None, "se": None}
    if count > ddof:
        divisor = count - ddof
        variance = sum(((value - mean) ** 2 for value in observations), Fraction(0)) / divisor
        sd = _decimal(variance).sqrt()
        result["sd"] = str(sd)
        result["se"] = str(sd / Decimal(count).sqrt())
    else:
        result["reason"] = "n must exceed ddof for SD/SE"
    return result


def summarize_groups(groups: Mapping[Any, Iterable[Any]] | Sequence[tuple[Any, Iterable[Any]]], *,
                     ddof: int = 1) -> dict[str, Any]:
    """Compute summary receipts for explicitly named groups."""
    items = groups.items() if isinstance(groups, Mapping) else groups
    result = {}
    for name, values in items:
        try:
            result[str(name)] = recompute_summary(values, ddof=ddof)
        except ValueError as exc:
            result[str(name)] = {"status": "not_checked", "reason": str(exc)}
    return result


def _interval(value: Any, places: int | None):
    if type(places) is not int or not 0 <= places <= 12:
        raise ValueError("reported decimals must be an integer in [0,12]")
    number = Decimal(str(value))
    half = Decimal(5).scaleb(-places - 1)
    return number - half, number + half


def compare_summary_values(computed: Mapping[str, Any], reported: Mapping[str, Any], *,
                           metrics: Iterable[str] | None = None,
                           decimals: Mapping[str, int] | None = None,
                           sample_unit_confirmed: bool = False,
                           same_units_confirmed: bool = False,
                           sd_kind: str = "") -> dict[str, Any]:
    """Compare a recompute_summary receipt with explicitly reported cells."""
    if not isinstance(computed, Mapping) or not isinstance(reported, Mapping):
        raise ValueError("computed and reported summaries must be objects")
    selected = list(metrics) if metrics is not None else ["n", "sum", "mean", "sd", "se"]
    unknown = set(selected) - {"n", "sum", "mean", "sd", "se"}
    if unknown:
        raise ValueError("unsupported summary metrics: " + ", ".join(sorted(unknown)))
    decimals = dict(decimals or {})
    results = []
    for metric in selected:
        result = {"metric": metric, "status": "not_checked"}
        try:
            if metric not in computed or metric not in reported:
                raise ValueError("metric is absent from computed or reported values")
            if metric != "n" and sample_unit_confirmed is not True:
                raise ValueError("confirm that raw rows represent the reported experimental/sample unit")
            if metric != "n" and same_units_confirmed is not True:
                raise ValueError("confirm matching units for raw and reported measurements")
            if metric in {"sd", "se"} and sd_kind != "sample":
                raise ValueError("only explicitly declared sample SD/SE is compared")
            expected = Decimal(str(computed[metric]))
            observed = Decimal(str(reported[metric]))
            if metric == "n":
                exact_count = observed == observed.to_integral_value() and observed == expected
                result.update(status="checked", discrepancy=not exact_count,
                              recomputed=str(expected), reported=str(observed))
            else:
                if metric not in decimals:
                    raise ValueError("actual reported decimal precision required")
                low, high = _interval(observed, decimals[metric])
                result.update(status="checked", discrepancy=not low <= expected <= high,
                              recomputed=str(expected), reported=str(observed),
                              admitted_interval=[str(low), str(high)])
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            result["reason"] = str(exc)
        results.append(result)
    return {"method_version": METHOD_VERSION, "results": results,
            "status": "checked" if any(item["status"] == "checked" for item in results) else "not_checked"}


def audit_p_values(bindings: Sequence[Mapping[str, Any]], *, correction: str = "none",
                   alpha: float = 0.05) -> dict[str, Any]:
    """Recompute a batch of P-value bindings and optionally adjust them together."""
    alpha = _finite(alpha, "alpha")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    checks = []
    for binding in bindings:
        checks.append(recompute_p_value(binding))
    usable = [check for check in checks if check.get("applicable") and check.get("p") is not None]
    if correction not in {"none", "raw"} and usable:
        adjusted = adjust_p_values([check["p"] for check in usable], method=correction)
        for check, value in zip(usable, adjusted):
            check["p_adjusted"] = value
            check["significant"] = bool(value < alpha)
    elif usable:
        for check in usable:
            check["p_adjusted"] = check["p"]
            check["significant"] = bool(check["p"] < alpha)
    findings = []
    for index, check in enumerate(checks):
        comparison = check.get("comparison")
        if comparison and comparison.get("status") == "checked" and comparison.get("consistent") is False:
            findings.append({
                "severity": "review",
                "check": "reported_p_value_mismatch",
                "detail": (f"报告 P 值与按声明设计复算的结果不一致："
                           f"recomputed={comparison['recomputed']:.6g}, "
                           f"reported={comparison['operator']}{comparison['reported']:.6g}。"),
                "scope": "cell",
                "layer": "reported_statistics",
                "method_version": METHOD_VERSION,
                "evidence": {"binding_index": index, **comparison},
            })
    return {"method_version": METHOD_VERSION, "checks": checks, "findings": findings,
            "coverage": {"requested": len(checks), "applicable": len(usable),
                         "correction": correction, "alpha": alpha}}


def _plain(value):
    if isinstance(value, Mapping):
        if set(value) == {"decimal"}:
            return value["decimal"]
        if set(value) == {"float"}:
            return value["float"]
    return value


def _entry_for(entries, selector):
    if not isinstance(selector, Mapping):
        raise ValueError("a table selector must be an object")
    region, sheet = selector.get("region_id"), selector.get("sheet")
    candidates = list(entries)
    if region:
        candidates = [entry for entry in candidates if entry["table"].get("region_id") == region]
    if sheet:
        candidates = [entry for entry in candidates if entry["table"].get("sheet") == sheet]
    if len(candidates) != 1:
        raise ValueError("select exactly one reviewed region with region_id or sheet")
    return candidates[0]


def _column_index(table, column):
    numbers, names = table.get("column_numbers") or [], table.get("columns") or []
    if type(column) is int:
        if column not in numbers:
            raise ValueError(f"column number {column} is outside the reviewed region")
        return numbers.index(column)
    if column not in names:
        raise ValueError(f"column {column!r} is outside the reviewed region")
    return names.index(column)


def _cell(entry, row, column):
    table = entry["table"]
    if type(row) is not int or row not in (table.get("row_numbers") or []):
        raise ValueError(f"row {row!r} is outside the reviewed region")
    index = table["row_numbers"].index(row)
    column_index = _column_index(table, column)
    return _plain(table["values"][index][column_index])


def _selector_value(value, entries):
    if isinstance(value, Mapping) and ("row" in value or "column" in value):
        entry = _entry_for(entries, value)
        return _cell(entry, value.get("row"), value.get("column")), value, entry
    return value, None, None


def _column_values(selector, entries):
    if not isinstance(selector, Mapping) or "column" not in selector:
        raise ValueError("a sample selector needs a column")
    entry = _entry_for(entries, selector)
    table = entry["table"]
    column_index = _column_index(table, selector["column"])
    rows = selector.get("rows")
    if rows is None:
        rows = list(table.get("row_numbers") or [])
    if (not isinstance(rows, (list, tuple)) or not rows
            or any(type(row) is not int for row in rows) or len(set(rows)) != len(rows)):
        raise ValueError("rows must be unique original row numbers")
    values, excluded = [], []
    for row in rows:
        if row not in table["row_numbers"]:
            raise ValueError(f"row {row} is outside the reviewed region")
        value = _plain(table["values"][table["row_numbers"].index(row)][column_index])
        if value is None or (isinstance(value, str) and not value.strip()):
            excluded.append(row)
        else:
            values.append(value)
    return values, excluded, selector


def _reported_cell(selector, entries):
    if not isinstance(selector, Mapping) or "row" not in selector:
        raise ValueError("reported cell needs row and column")
    entry = _entry_for(entries, selector)
    return _cell(entry, selector["row"], selector.get("column")), selector, entry


def _binding_finding(check, detail, selector, entry, *, evidence):
    table = entry["table"]
    column = selector.get("column") if isinstance(selector, Mapping) else None
    column_index = None
    if column is not None:
        column_index = (table["column_numbers"][_column_index(table, column)])
    return {"severity": "review", "check": check, "detail": detail,
            "sheet": table.get("sheet", ""), "region_id": table.get("region_id", ""),
            "row": selector.get("row") if isinstance(selector, Mapping) else None,
            "column_index": column_index, "scope": "cell",
            "layer": "reported_statistics", "method_version": METHOD_VERSION,
            "evidence": evidence}


def apply_reported_bindings(entries, *, summaries=None, p_values=None):
    """Apply explicit summary/P bindings to reviewed entry dictionaries.

    This is the host-facing bridge behind review_sheet(reported_summaries=...,
    reported_p_values=...). It never scans a workbook: every raw or reported
    value is selected by region_id/sheet and absolute row/column.
    """
    summary_checks, p_checks, gaps = [], [], []
    for index, binding in enumerate(summaries or []):
        check = {"check": "reported_summary_binding", "binding_index": index,
                 "state": "not_applicable", "tested": 0, "skipped": 0,
                 "reason": "", "method_version": METHOD_VERSION}
        try:
            if not isinstance(binding, Mapping):
                raise ValueError("summary binding must be an object")
            raw = binding.get("raw")
            if not isinstance(raw, Mapping) or "column" not in raw:
                raise ValueError("summary binding needs raw.column")
            values, excluded, raw_selector = _column_values(raw, entries)
            if excluded and binding.get("missing_policy") != "exclude":
                raise ValueError("missing raw rows; set missing_policy='exclude' only after confirming the exclusion policy")
            if not values:
                raise ValueError("no usable raw observations")
            computed = recompute_summary(values)
            computed["excluded_rows"] = excluded
            reported = binding.get("reported")
            if not isinstance(reported, Mapping) or "row" not in reported or not isinstance(reported.get("columns"), Mapping):
                raise ValueError("reported summary needs row and columns")
            reported_entry = _entry_for(entries, reported)
            reported_row = reported["row"]
            reported_values, cells = {}, {}
            for metric, column in reported["columns"].items():
                reported_values[metric] = _cell(reported_entry, reported_row, column)
                cells[metric] = column
                if reported_values[metric] is None:
                    raise ValueError(f"reported {metric} cell is empty")
            decimals = dict(binding.get("decimals") or reported.get("decimals") or {})
            comparison = compare_summary_values(
                computed, reported_values, metrics=list(reported["columns"]),
                decimals=decimals, sample_unit_confirmed=binding.get("sample_unit_confirmed") is True,
                same_units_confirmed=binding.get("same_units_confirmed") is True,
                sd_kind=binding.get("sd_kind") or "")
            executed = [item for item in comparison["results"] if item["status"] == "checked"]
            skipped = [item for item in comparison["results"] if item["status"] != "checked"]
            discrepancies = [item for item in executed if item.get("discrepancy")]
            check.update(tested=len(executed), skipped=len(skipped),
                         state="partial_coverage" if skipped and executed else
                         "checked_with_findings" if discrepancies else
                         "checked_no_findings" if executed else "not_applicable",
                         reason="; ".join(item.get("reason", "") for item in skipped)[:400])
            for item in discrepancies:
                metric = item["metric"]
                selector = {"region_id": reported_entry["table"].get("region_id", ""),
                            "sheet": reported_entry["table"].get("sheet", ""),
                            "row": reported_row, "column": cells[metric]}
                detail = (f"报告 {metric} 与显式绑定的原始行复算不一致："
                          f"recomputed={item['recomputed']}, reported={item['reported']}"
                          + (f", admitted={item['admitted_interval']}" if item.get("admitted_interval") else "") + "。")
                reported_entry["result"]["data"]["findings"].append(_binding_finding(
                    "reported_" + metric + "_differs_from_raw", detail, selector, reported_entry,
                    evidence={"metric": metric, **item, "raw": raw_selector,
                              "raw_rows": raw.get("rows"), "excluded_rows": excluded}))
            if skipped:
                gaps.append({"sheet": reported_entry["table"].get("sheet", ""),
                             "region_id": reported_entry["table"].get("region_id", ""),
                             "check": "reported_summary_binding", "state": "partial_coverage",
                             "reason": check["reason"]})
        except (ValueError, KeyError, TypeError) as exc:
            check.update(state="not_applicable", reason=str(exc))
            gaps.append({"sheet": "", "region_id": "", "check": "reported_summary_binding",
                         "state": "not_applicable", "reason": str(exc)})
        try:
            binding_map = summaries[index] if isinstance(summaries[index], Mapping) else {}
            first = _entry_for(entries, binding_map.get("reported") or binding_map.get("raw") or {})
        except (ValueError, KeyError, TypeError, AttributeError):
            first = entries[0]
        first["result"]["data"]["checklist"].append(check)
        summary_checks.append(check)

    for index, binding in enumerate(p_values or []):
        check = {"check": "reported_p_value", "binding_index": index,
                 "state": "not_applicable", "tested": 0, "skipped": 0,
                 "reason": "", "method_version": METHOD_VERSION}
        try:
            if not isinstance(binding, Mapping):
                raise ValueError("P-value binding must be an object")
            spec = dict(binding)
            statistic, statistic_selector, statistic_entry = _selector_value(spec.get("statistic"), entries)
            if statistic_selector is not None:
                spec["statistic"] = statistic
            if spec.get("mode") == "samples":
                groups = spec.get("groups")
                if groups is not None:
                    resolved = []
                    for group in groups:
                        if isinstance(group, Mapping) and "column" in group:
                            values, excluded, _ = _column_values(group, entries)
                            if excluded and binding.get("missing_policy") != "exclude":
                                raise ValueError("missing sample rows; set missing_policy='exclude' only after confirming the exclusion policy")
                            resolved.append(values)
                        else:
                            resolved.append(group)
                    spec["groups"] = resolved
                for key in ("x", "y"):
                    if isinstance(spec.get(key), Mapping) and "column" in spec[key]:
                        values, excluded, _ = _column_values(spec[key], entries)
                        if excluded and binding.get("missing_policy") != "exclude":
                            raise ValueError("missing sample rows; set missing_policy='exclude' only after confirming the exclusion policy")
                        spec[key] = values
            reported_selector = spec.pop("reported_p", None)
            if reported_selector is None:
                reported_selector = spec.pop("reported", None)
            reported_entry = None
            if isinstance(reported_selector, Mapping) and "row" in reported_selector:
                value, _, reported_entry = _reported_cell(reported_selector, entries)
                spec["reported_p"] = value
                spec["reported_decimals"] = reported_selector.get("decimals")
            elif reported_selector is not None:
                spec["reported_p"] = reported_selector
            result = recompute_p_value(spec)
            if not result.get("applicable"):
                raise ValueError(result.get("reason", "P-value recomputation not applicable"))
            comparison = result.get("comparison") or {}
            finding = None
            if comparison.get("status") == "checked" and comparison.get("consistent") is False:
                location = binding.get("location") or reported_selector or statistic_selector
                entry = reported_entry or statistic_entry or _entry_for(entries, location or {})
                finding = _binding_finding(
                    "reported_p_value_mismatch",
                    (f"报告 P 值与按声明设计复算的结果不一致：recomputed={comparison['recomputed']:.6g}, "
                     f"reported={comparison['operator']}{comparison['reported']:.6g}。"),
                    location if isinstance(location, Mapping) else {}, entry,
                    evidence={"binding_index": index, **comparison,
                              "test": result.get("test"), "distribution": result.get("distribution"),
                              "statistic": result.get("statistic"), "df": result.get("df"),
                              "tails": result.get("tails")})
                entry["result"]["data"]["findings"].append(finding)
            if comparison.get("status") == "not_checked":
                location = binding.get("location") or reported_selector or statistic_selector
                entry = (reported_entry or statistic_entry
                         or _entry_for(entries, location if isinstance(location, Mapping) else {}))
                gaps.append({"sheet": entry["table"].get("sheet", ""),
                             "region_id": entry["table"].get("region_id", ""),
                             "check": "reported_p_value", "state": "partial_coverage",
                             "reason": comparison.get("reason", "reported P precision unavailable")})
                check.update(state="partial_coverage", reason=comparison.get("reason", ""))
            else:
                check.update(tested=1, state="checked_with_findings" if finding else "checked_no_findings")
        except (ValueError, KeyError, TypeError) as exc:
            check.update(state="not_applicable", reason=str(exc))
            gaps.append({"sheet": "", "region_id": "", "check": "reported_p_value",
                         "state": "not_applicable", "reason": str(exc)})
        try:
            binding_map = p_values[index] if isinstance(p_values[index], Mapping) else {}
            first = _entry_for(entries, binding_map.get("location") or binding_map.get("statistic") or {})
        except (ValueError, KeyError, TypeError, AttributeError):
            first = entries[0]
        first["result"]["data"]["checklist"].append(check)
        p_checks.append(check)

    review = {"summaries": {"checks": summary_checks,
                            "coverage": {"requested": len(summaries or []),
                                         "completed": sum(item["state"].startswith("checked") for item in summary_checks)}},
              "p_values": {"checks": p_checks,
                           "coverage": {"requested": len(p_values or []),
                                        "completed": sum(item["state"].startswith("checked") for item in p_checks)}}}
    return {"review": review, "coverage_gaps": gaps,
            "partial": bool(gaps) or any(item["state"] not in {"checked_with_findings", "checked_no_findings"}
                                         for item in summary_checks + p_checks)}


def merge_review_findings(snapshot, findings: Sequence[Mapping[str, Any]], *,
                          checks: Sequence[Mapping[str, Any]] | None = None):
    """Return a new snapshot with extra findings/check rows for annotated export."""
    from .api import AuditSnapshot
    from .reporting import dumps, loads
    report = loads(snapshot.report_json)
    entries = report.get("tables") or []
    if not entries:
        raise ValueError("snapshot contains no table entries")
    def target(item, location=None):
        result = dict(item)
        if "check" not in result and "rule" in result:
            result["check"] = result["rule"]
        result.setdefault("severity", "review")
        result.setdefault("detail", result.get("summary", ""))
        if location:
            result["sheet"] = location.get("sheet", result.get("sheet", ""))
            if location.get("region_id"):
                result["region_id"] = location["region_id"]
            if location.get("rows"):
                result["row"] = location["rows"][0]
            if location.get("columns"):
                result["column_index"] = location["columns"][0].get("index")
            result["evidence"] = {**result.get("evidence", {}), "source_location": location}
        return result

    for item in findings:
        locations = item.get("locations") if "check" not in item else None
        normalized = [target(item, location) for location in locations] if locations else [target(item)]
        for entry_item in normalized:
            sheet = entry_item.get("sheet")
            region = entry_item.get("region_id")
            entry = next((candidate for candidate in entries
                          if (not sheet or candidate["table"].get("sheet") == sheet)
                          and (not region or candidate["table"].get("region_id") == region)), entries[0])
            entry["result"]["data"]["findings"].append(entry_item)
    if checks:
        entries[0]["result"]["data"]["checklist"].extend(dict(item) for item in checks)
    return AuditSnapshot(snapshot.source_bytes, snapshot.source_name, dumps(report),
                         snapshot.source_sha256)
