"""
Build the knowledge base from data/products.csv and data/*.md.

Run again whenever products, prices or policies change:
    python ingest.py
"""
from dotenv import load_dotenv

load_dotenv()  # same .env settings (e.g. GEMINI_API_KEY) as the server

import csv
import glob
import hashlib
import json
import os
import random
import re
import time
from collections import deque

from google.genai import errors

from rag import EMBED_DIM, EMBED_MODEL, embed, get_collection

DATA_DIR = os.getenv("DATA_DIR", "./data")
CACHE_PATH = os.getenv("EMBED_CACHE", "./embed_cache.jsonl")
BATCH_SIZE = 100  # max texts per embed_content request (each text still counts as one request for quota)
MAX_ATTEMPTS = 8
# Free-tier limits are 100 requests/min and 30K input tokens/min; stay a little under both.
RPM_LIMIT = int(os.getenv("EMBED_RPM", "90"))
TPM_LIMIT = int(os.getenv("EMBED_TPM", "27000"))
CHARS_PER_TOKEN = float(os.getenv("EMBED_CHARS_PER_TOKEN", "3"))  # cautious: Arabic tokenizes densely


def _est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


class _RateLimiter:
    """Sliding 60 s window over requests (one per text) and estimated input tokens."""

    def __init__(self, rpm: int, tpm: int):
        self.rpm, self.tpm = rpm, tpm
        self.window = deque()  # (timestamp, n_requests, n_tokens)

    def wait(self, n_req: int, n_tok: int):
        while True:
            now = time.monotonic()
            while self.window and now - self.window[0][0] >= 60:
                self.window.popleft()
            used_req = sum(r for _, r, _ in self.window)
            used_tok = sum(t for _, _, t in self.window)
            if not self.window or (used_req + n_req <= self.rpm and used_tok + n_tok <= self.tpm):
                break
            pause = 60 - (now - self.window[0][0]) + 0.1
            if pause > 1:
                print(f"  throttling: waiting {pause:.0f}s for the rate limit")
            time.sleep(pause)
        self.window.append((time.monotonic(), n_req, n_tok))


def _batches(texts: list[str]):
    """Yield (batch, est_tokens) groups that fit within one minute's request and token budget."""
    max_texts = min(BATCH_SIZE, RPM_LIMIT)
    batch, tokens = [], 0
    for t in texts:
        n = _est_tokens(t)
        if batch and (len(batch) >= max_texts or tokens + n > TPM_LIMIT):
            yield batch, tokens
            batch, tokens = [], 0
        batch.append(t)
        tokens += n
    if batch:
        yield batch, tokens


def _is_daily_quota(e: errors.APIError) -> bool:
    return e.code == 429 and "PerDay" in json.dumps(e.details or {})


def _embed_with_retry(batch: list[str]) -> list[list[float]]:
    delay = 2.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return embed(batch, "RETRIEVAL_DOCUMENT")
        except errors.APIError as e:
            if _is_daily_quota(e):
                raise  # retrying won't help until the quota resets
            retryable = e.code == 429 or (e.code or 0) >= 500
            if not retryable or attempt == MAX_ATTEMPTS:
                raise
            wait = min(delay, 60) + random.uniform(0, 1)
            print(f"  API error {e.code}, retrying in {wait:.0f}s (attempt {attempt}/{MAX_ATTEMPTS})")
            time.sleep(wait)
            delay *= 2


def _key(text: str) -> str:
    return hashlib.sha256(f"{EMBED_MODEL}|{EMBED_DIM}|{text}".encode()).hexdigest()


def _load_cache() -> dict[str, list[float]]:
    cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    cache[rec["k"]] = rec["v"]
                except (ValueError, KeyError):
                    pass  # partial last line from an interrupted run
    return cache


def embed_batched(docs: list[str]) -> list[list[float]]:
    """Embed docs, reusing cached vectors so interrupted or repeated runs only embed new text."""
    cache = _load_cache()
    unique = list(dict.fromkeys(docs))
    todo = [d for d in unique if _key(d) not in cache]
    print(f"  {len(unique)} unique texts: {len(unique) - len(todo)} cached, {len(todo)} to embed")
    limiter = _RateLimiter(RPM_LIMIT, TPM_LIMIT)
    i = 0  # texts embedded so far
    with open(CACHE_PATH, "a", encoding="utf-8") as f:
        for batch, tokens in _batches(todo):
            limiter.wait(len(batch), tokens)
            try:
                vecs = _embed_with_retry(batch)
            except errors.APIError as e:
                if _is_daily_quota(e):
                    raise SystemExit(
                        f"Daily embedding quota reached after {i}/{len(todo)} new chunks. "
                        "Progress is saved; run `python ingest.py` again after the quota resets. "
                        "The existing knowledge base was not changed."
                    )
                raise
            for text, v in zip(batch, vecs):
                cache[_key(text)] = v
                f.write(json.dumps({"k": _key(text), "v": [round(x, 6) for x in v]}) + "\n")
            f.flush()
            i += len(batch)
            print(f"  embedded {i}/{len(todo)} (~{tokens} tokens this batch)")
    return [cache[_key(d)] for d in docs]


REQUIRED_COLUMNS = {"id", "name_en", "purity"}
# Arabic-Indic (٠-٩) and Persian (۰-۹) digits -> ASCII; Arabic decimal/thousands separators.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


def _clean(s) -> str:
    return (s or "").strip()


def _num(s) -> float:
    """Parse numbers like '21', '٢١', '21K', 'عيار 21', '2,5' or '2٫5'."""
    s = _clean(s).translate(_DIGITS)
    s = re.sub(r"(?i)k|عيار|g", "", s).strip()
    if s.count(",") == 1 and "." not in s:
        s = s.replace(",", ".")  # decimal comma
    return float(s.replace(",", ""))


def _fmt(x: float) -> str:
    return f"{x:g}"


def load_products(path: str):
    docs, metas, ids = [], [], []
    skipped = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path} is missing required column(s): {', '.join(sorted(missing))}")

        for row in reader:
            pid = _clean(row.get("id"))
            try:
                karat = int(_num(row["purity"]))
                raw_weight = _clean(row.get("weight_grams")) or _clean(row.get("weight_g"))  # weight_g: old format
                weight = _num(raw_weight) if raw_weight else None
                price = _num(row["price_sar"]) if _clean(row.get("price_sar")) else None
                in_stock = _num(row["stock"]) > 0 if _clean(row.get("stock")) else None
            except ValueError as e:
                print(f"Skipping product {pid!r}: {e}")
                skipped += 1
                continue

            name_en, name_ar = _clean(row.get("name_en")), _clean(row.get("name_ar"))
            cat_en = _clean(row.get("category_en")) or _clean(row.get("category"))  # category: old format
            cat_ar = _clean(row.get("category_ar"))
            desc_en, desc_ar = _clean(row.get("description_en")), _clean(row.get("description_ar"))
            url = _clean(row.get("url"))

            lines = [f"Product: {' | '.join(filter(None, [name_en, name_ar]))}"]
            if cat_en or cat_ar:
                lines.append(f"Category: {' | '.join(filter(None, [cat_en, cat_ar]))}")
            lines.append(f"Purity: {karat}K gold | عيار {karat}")
            if weight is not None:
                lines.append(f"Weight: {_fmt(weight)} g")
            if price is not None:
                lines.append(f"Price: {_fmt(price)} SAR")
            if in_stock is not None:
                lines.append(f"Availability: {'In stock' if in_stock else 'Out of stock'}")
            if desc_en:
                lines.append(f"Description: {desc_en}")
            if desc_ar:
                lines.append(f"الوصف: {desc_ar}")
            if url:
                lines.append(f"Link: {url}")

            # Chroma metadata can't hold None, so optional fields are only set when present.
            meta = {
                "type": "product",
                "id": pid,
                "name": name_en or name_ar,
                "name_ar": name_ar,
                "category": cat_en,
                "category_ar": cat_ar,
                "karat": karat,
                "url": url,
            }
            if weight is not None:
                meta["weight_grams"] = weight
            if price is not None:
                meta["price_sar"] = price
            if in_stock is not None:
                meta["in_stock"] = in_stock

            docs.append("\n".join(lines))
            metas.append(meta)
            ids.append(f"product-{pid}")
    if skipped:
        print(f"Skipped {skipped} product row(s) with invalid values.")
    return docs, metas, ids


def chunk_markdown(text: str, max_chars: int = 900):
    """Split on headings, then pack paragraphs up to max_chars."""
    sections = re.split(r"\n(?=#{1,3} )", text)
    chunks = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        heading = sec.splitlines()[0] if sec.startswith("#") else ""
        buf = ""
        for para in sec.split("\n\n"):
            if len(buf) + len(para) > max_chars and buf:
                chunks.append(buf.strip())
                buf = heading + "\n" if heading and not para.startswith("#") else ""
            buf += para + "\n\n"
        if buf.strip():
            chunks.append(buf.strip())
    return chunks


def load_docs(pattern: str):
    docs, metas, ids = [], [], []
    for path in sorted(glob.glob(pattern)):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            for i, chunk in enumerate(chunk_markdown(f.read())):
                docs.append(chunk)
                metas.append({"type": "policy", "name": name, "url": ""})
                ids.append(f"{name}-{i}")
    return docs, metas, ids


def main():
    docs, metas, ids = [], [], []
    for d, m, i in (
        load_products(os.path.join(DATA_DIR, "products.csv")),
        load_docs(os.path.join(DATA_DIR, "*.md")),
    ):
        docs += d
        metas += m
        ids += i
    if not docs:
        raise SystemExit("No data to index.")

    # Embed everything first, so a failed run leaves the existing knowledge base untouched.
    print(f"Embedding {len(docs)} chunks with Gemini...")
    vecs = embed_batched(docs)

    col = get_collection(reset=True)  # recreated with cosine distance; old vectors are incompatible
    for i in range(0, len(docs), 1000):
        s = slice(i, i + 1000)
        col.add(documents=docs[s], metadatas=metas[s], ids=ids[s], embeddings=vecs[s])
    print(f"Indexed {col.count()} chunks into the knowledge base.")


if __name__ == "__main__":
    main()
