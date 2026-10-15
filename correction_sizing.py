"""Bounded correction requests; all transcript characters remain in ordered parts."""

import hashlib
import json


def split_text(text, limit=1800):
    parts = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit + 1), text.rfind(" ", 0, limit + 1))
        if cut < limit // 2:
            cut = limit
        else:
            cut += 1
        parts.append(text[:cut])
        text = text[cut:]
    parts.append(text)
    return parts


def units_for(task):
    item = task["item"]
    context = []
    remaining = 1200
    for row in item.get("chat_context", []):
        if remaining <= 0:
            break
        copied = dict(row)
        copied["text"] = str(row.get("text", ""))[:remaining]
        remaining -= len(copied["text"])
        context.append(copied)
    alternatives = [
        (alternative, split_text(alternative["text"])) for alternative in item["alternatives"]
    ]
    count = max(len(parts) for _, parts in alternatives)
    units = []
    for index in range(count):
        part = dict(
            item,
            id=item["id"] + f"__part_{index + 1}",
            chat_context=context,
            alternatives=[
                dict(alternative, text=parts[index])
                for alternative, parts in alternatives
                if index < len(parts)
            ],
        )
        key = hashlib.sha256(
            (task["input_hash"] + json.dumps(part, sort_keys=True, ensure_ascii=False)).encode()
        ).hexdigest()
        units.append({"item": part, "key": key})
    return units


def batches(units, budget=24000):
    current = []
    for unit in units:
        candidate = current + [unit]
        size = len(json.dumps({"items": [u["item"] for u in candidate]}).encode())
        if current and (size > budget or len(candidate) > 3):
            yield current
            current = [unit]
        else:
            current = candidate
        if len(json.dumps({"items": [u["item"] for u in current]}).encode()) > budget:
            raise ValueError("Single correction part exceeds request budget")
    if current:
        yield current


def combine(task, units, cache):
    if any(cache.get(unit["key"], {}).get("status") != "done" for unit in units):
        return None
    parts = [cache[unit["key"]] for unit in units]
    result = {
        "id": task["item"]["id"],
        "corrected_text": "\n".join(p["corrected_text"] for p in parts),
    }
    for field in ("selected_context_ids", "uncertain_phrases", "sensitive_changes"):
        values = []
        seen = set()
        for part in parts:
            for value in part.get(field, []):
                key = json.dumps(value, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    values.append(value)
        result[field] = values
    return result
