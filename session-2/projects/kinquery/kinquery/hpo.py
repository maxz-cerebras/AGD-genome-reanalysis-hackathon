"""HPO ontology (release 2024-08-13, matching the data) from hp.json (OBO Graphs).

Downloaded on first use into ``.cache/``. Provides subsumption ("Focal-onset
seizure" is-a "Seizure") and label/synonym lookup for grounding LLM output.
"""

from __future__ import annotations

import json
import re
import urllib.request
from functools import lru_cache
from pathlib import Path

RELEASE = "v2024-08-13"
URL = f"https://github.com/obophenotype/human-phenotype-ontology/releases/download/{RELEASE}/hp.json"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / f"hp-{RELEASE}.json"
_IRI = "http://purl.obolibrary.org/obo/HP_"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class HPO:
    def __init__(self, graph: dict):
        self.label: dict[str, str] = {}
        self.synonyms: dict[str, list[str]] = {}
        self.parents: dict[str, set[str]] = {}
        self.children: dict[str, set[str]] = {}
        self._by_name: dict[str, str] = {}
        for node in graph["nodes"]:
            if not node["id"].startswith(_IRI) or node.get("type") not in (None, "CLASS"):
                continue
            meta = node.get("meta", {})
            if meta.get("deprecated"):
                continue
            hid = "HP:" + node["id"][len(_IRI):]
            lbl = node.get("lbl", "")
            self.label[hid] = lbl
            syns = [s["val"] for s in meta.get("synonyms", []) if s.get("val")]
            self.synonyms[hid] = syns
            self.parents.setdefault(hid, set())
            self.children.setdefault(hid, set())
            self._by_name.setdefault(_norm(lbl), hid)
            for s in syns:
                self._by_name.setdefault(_norm(s), hid)
        for e in graph["edges"]:
            if e.get("pred") != "is_a":
                continue
            sub, obj = e["sub"], e["obj"]
            if sub.startswith(_IRI) and obj.startswith(_IRI):
                s, o = "HP:" + sub[len(_IRI):], "HP:" + obj[len(_IRI):]
                if s in self.label and o in self.label:
                    self.parents[s].add(o)
                    self.children[o].add(s)

    @classmethod
    def load(cls, path: Path = CACHE) -> "HPO":
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(URL, tmp)
            tmp.rename(path)
        with open(path) as fh:
            return cls(json.load(fh)["graphs"][0])

    def __contains__(self, hid: str) -> bool:
        return hid in self.label

    @lru_cache(maxsize=4096)
    def descendants(self, hid: str) -> frozenset[str]:
        """All transitive subclasses of ``hid`` including itself."""
        out = {hid}
        stack = list(self.children.get(hid, ()))
        while stack:
            c = stack.pop()
            if c not in out:
                out.add(c)
                stack.extend(self.children[c])
        return frozenset(out)

    def is_a(self, hid: str, ancestor: str) -> bool:
        return hid in self.descendants(ancestor)

    def lookup(self, text: str) -> str | None:
        """Exact (normalised) label or synonym match."""
        return self._by_name.get(_norm(text))

    def search(self, text: str, limit: int = 10) -> list[tuple[str, str]]:
        """Substring search over labels and synonyms; exact match first."""
        q = _norm(text)
        if not q:
            return []
        exact = self.lookup(text)
        hits: list[tuple[str, str]] = [(exact, self.label[exact])] if exact else []
        for name, hid in self._by_name.items():
            if q in name and (hid, self.label[hid]) not in hits:
                hits.append((hid, self.label[hid]))
                if len(hits) >= limit:
                    break
        return hits
