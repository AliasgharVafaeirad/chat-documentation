"""Accept only structurally complete, source-bound individual correction objects."""

import json
import re


def complete_objects(text):
    match = re.search(r'"items"\s*:\s*\[', text)
    if not match:
        return []
    cursor = match.end()
    decoder = json.JSONDecoder()
    rows = []
    while cursor < len(text):
        while cursor < len(text) and text[cursor] in " \r\n\t,":
            cursor += 1
        if cursor >= len(text) or text[cursor] == "]":
            break
        try:
            item, end = decoder.raw_decode(text, cursor)
        except json.JSONDecodeError:
            break
        if not isinstance(item, dict):
            break
        rows.append(item)
        cursor = end
    return rows


def validate_individual(rows, source):
    originals = {item["id"]: item for item in source}
    valid = []
    errors = {}
    seen = set()
    for row in rows:
        key = row.get("id")
        if not isinstance(key, str) or key not in originals:
            continue
        if key in seen:
            errors[key] = "Duplicate correction ID"
            valid = [r for r in valid if r["id"] != key]
            continue
        seen.add(key)
        required = (
            "corrected_text",
            "selected_context_ids",
            "uncertain_phrases",
            "sensitive_changes",
        )
        if any(k not in row for k in required):
            errors[key] = "Incomplete correction object"
            continue
        if not isinstance(row["corrected_text"], str) or not row["corrected_text"].strip():
            errors[key] = "Empty corrected text returned"
            continue
        if any(not isinstance(row[k], list) for k in required[1:]):
            errors[key] = "Invalid correction metadata"
            continue
        allowed = {m["source_id"] for m in originals[key].get("chat_context", [])}
        if any(
            not isinstance(value, str) or value not in allowed
            for value in row["selected_context_ids"]
        ):
            errors[key] = "Nonexistent context reference"
            continue
        if any(not isinstance(value, str) for value in row["uncertain_phrases"]):
            errors[key] = "Invalid uncertainty metadata"
            continue
        if any(
            not isinstance(value, dict)
            or any(
                not isinstance(value.get(field), str)
                for field in ("before", "after", "kind", "basis")
            )
            for value in row["sensitive_changes"]
        ):
            errors[key] = "Invalid change metadata"
            continue
        valid.append(row)
    return valid, errors
