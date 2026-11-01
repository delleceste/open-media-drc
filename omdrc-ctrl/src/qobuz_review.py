"""Albums parked to review later: a small ring buffer that ages by days of use.

Time is counted in *days the application was used*, not calendar days: the
counter advances by one the first time the app is touched on a new calendar
day, so a fortnight away costs nothing.  An album lives `lifetime` use-days
(extendable); the list holds at most `size` albums, the oldest making room.
"""
from __future__ import annotations

import datetime
import json
import os
import threading

DEFAULTS = {"size": 16, "lifetime": 10}
LIMITS = {"size": (1, 200), "lifetime": (1, 365)}
NOTE_MAX = 500
_lock = threading.Lock()


def _today() -> str:
    return datetime.date.today().isoformat()


class ReviewList:
    def __init__(self, path: str, today=_today):
        self.path, self.today = path, today

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["settings"] = self._settings(data.get("settings"))
        data["days"] = data["days"] if isinstance(data.get("days"), int) else 0
        data["last_day"] = data.get("last_day") if isinstance(data.get("last_day"), str) else ""
        data["items"] = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("id")] \
            if isinstance(data.get("items"), list) else []
        return data

    @staticmethod
    def _settings(raw) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        out = {}
        for key, (low, high) in LIMITS.items():
            value = raw.get(key, DEFAULTS[key])
            out[key] = value if isinstance(value, int) and not isinstance(value, bool) \
                and low <= value <= high else DEFAULTS[key]
        return out

    def _save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, **data}, stream, ensure_ascii=False)
        os.replace(temporary, self.path)

    def _tick(self, data: dict) -> bool:
        """Count today as a day of use; drop what has run out.  True if changed."""
        changed = False
        today = self.today()
        if data["last_day"] != today:
            data["last_day"] = today
            data["days"] += 1
            changed = True
        kept = [i for i in data["items"] if i.get("expires", 0) > data["days"]]
        if len(kept) != len(data["items"]):
            data["items"], changed = kept, True
        return changed

    def _trim(self, data: dict) -> None:
        data["items"] = data["items"][-data["settings"]["size"]:]

    def view(self, touch: bool = True) -> dict:
        """Everything for the list, oldest-parked first, with the use-days left."""
        with _lock:
            data = self._load()
            if touch and self._tick(data):
                self._save(data)
            self._trim(data)
            items = [{**i, "left": i["expires"] - data["days"]} for i in data["items"]]
            return {"items": items, "settings": data["settings"], "days": data["days"]}

    def add(self, card: dict, note: str = "") -> dict:
        with _lock:
            data = self._load()
            self._tick(data)
            album_id = str(card["id"])
            expires = data["days"] + data["settings"]["lifetime"]
            existing = next((i for i in data["items"] if i["id"] == album_id), None)
            if existing:
                existing["expires"] = expires
                if note:
                    existing["note"] = note[:NOTE_MAX]
                data["items"].remove(existing)
                data["items"].append(existing)
            else:
                keep = ("id", "title", "version", "artist", "composer", "label", "year",
                        "image", "image_large", "bits", "rate", "streamable")
                data["items"].append({**{k: card.get(k) for k in keep if card.get(k) is not None},
                                      "id": album_id, "note": note[:NOTE_MAX], "expires": expires})
            self._trim(data)
            self._save(data)
            return {"already": bool(existing)}

    def update(self, album_id: str, action: str, note: str = "") -> bool:
        with _lock:
            data = self._load()
            self._tick(data)
            item = next((i for i in data["items"] if i["id"] == album_id), None)
            if not item:
                self._save(data)
                return False
            if action == "remove":
                data["items"].remove(item)
            elif action == "extend":
                item["expires"] = max(item["expires"], data["days"]) + data["settings"]["lifetime"]
            elif action == "note":
                item["note"] = note[:NOTE_MAX]
            else:
                raise ValueError("unknown review action")
            self._save(data)
            return True

    def configure(self, raw: dict) -> dict:
        with _lock:
            data = self._load()
            merged = {**data["settings"], **{k: v for k, v in (raw or {}).items() if k in LIMITS}}
            for key, (low, high) in LIMITS.items():
                value = merged[key]
                if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                    raise ValueError(f"{key} must be a whole number from {low} to {high}")
            data["settings"] = merged
            self._tick(data)
            self._trim(data)
            self._save(data)
            return merged
